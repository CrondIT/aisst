"""
LLM Worker — обработчик очереди LLM-запросов (chat, gigachatpro, gemini).
Запускается как отдельный процесс. Обеспечивает:
- Semaphore — глобальное ограничение concurrent-запросов к LLM
- Timeout на каждый вызов
- Retry с экспоненциальной задержкой
- Dead letter после исчерпания retry
"""

import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("llm_worker.log", encoding="utf-8", mode="a"),
    ],
)
logger = logging.getLogger("llm_worker")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from redis_utils import RedisQueue, RedisQueueError
from redis_utils.redis_config import REDIS_PREFIX
from ai_models import OpenAIClient, GeminiClient, GigaChatClient
from gigachat.client import GigaChat
import db as db_module
from global_state import (
    GIGACHAT_CONFIG,
    OPENAI_API_KEY_CHAT,
    GEMINI_API_KEY,
    MODELS,
    get_token_limit,
    get_user_context_async,
    set_user_context_async,
    MAX_CONTEXT_MESSAGES,
    _use_redis,
)
from cost_tracker import UsageInfo
from shared.message_utils import check_and_send_formatted, get_file_extracted_text
from rag_chain import ask_rag
import max_api

# ─── Лимиты ───
MAX_CONCURRENT_LLM = int(os.getenv("MAX_CONCURRENT_LLM", "3"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "45"))  # секунд на один вызов
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 45]

# ─── Клиенты (инициализируются один раз) ───
openai_client = None
gemini_client = None
giga_client = None
giga_lc_client = None


def init_clients():
    global openai_client, gemini_client, giga_client, giga_lc_client

    if OPENAI_API_KEY_CHAT:
        openai_client = OpenAIClient(OPENAI_API_KEY_CHAT)
        logger.info("✅ OpenAI клиент инициализирован")

    if GEMINI_API_KEY:
        gemini_client = GeminiClient(GEMINI_API_KEY)
        logger.info("✅ Gemini клиент инициализирован")

    if GIGACHAT_CONFIG.credentials:
        giga = GigaChat(
            credentials=GIGACHAT_CONFIG.credentials,
            scope=GIGACHAT_CONFIG.scope,
            ca_bundle_file=GIGACHAT_CONFIG.ca_bundle_file,
        )
        giga_client = GigaChatClient(giga)
        logger.info("✅ GigaChat клиент инициализирован")

        from langchain_gigachat import GigaChat as LangChainGigaChat
        _rag_model = MODELS.get("rag_llm")
        giga_lc_client = LangChainGigaChat(
            credentials=GIGACHAT_CONFIG.credentials,
            scope=GIGACHAT_CONFIG.scope,
            model=_rag_model,
            ca_bundle_file=GIGACHAT_CONFIG.ca_bundle_file,
            max_tokens=get_token_limit(_rag_model),
        )
        logger.info("✅ LangChain GigaChat клиент инициализирован")


async def _call_llm(
    mode: str, messages: list[dict], model: str,
    image_paths: list[str] | None = None,
) -> tuple[str, UsageInfo | None]:
    """Вызывает соответствующую LLM в зависимости от режима."""
    if mode in ("chat",):
        if not openai_client:
            raise RuntimeError("OpenAI клиент не настроен")
        result = await openai_client.chat(messages=messages, model=model)
        return result.text or "", result.usage

    if mode == "gigachatpro":
        if not giga_client:
            raise RuntimeError("GigaChat клиент не настроен")
        result = await giga_client.chat(messages=messages, model=model)
        return result.text or "", result.usage

    if mode == "gemini":
        if not gemini_client:
            raise RuntimeError("Gemini клиент не настроен")
        result = await gemini_client.chat(
            messages=messages, model=model, image_paths=image_paths,
        )
        return result.text or "", result.usage

    raise RuntimeError(f"Неизвестный режим LLM: {mode}")


async def process_llm_task(task_data: dict) -> dict:
    """
    Обрабатывает одну LLM задачу.

    Args:
        task_data: {
            "mode": "chat" | "gigachatpro" | "gemini",
            "model": "gpt-5.2-chat-latest" | ...,
            "user_id": int,
            "user_text": str,
            "sender": dict,
            "extracted_text": str | None,
            "temperature": float,
        }
    """
    mode = task_data.get("mode")
    model = task_data.get("model")
    user_id = task_data.get("user_id")
    user_text = task_data.get("user_text", "")
    sender = task_data.get("sender", {})
    extracted_text = task_data.get("extracted_text")
    temperature = task_data.get("temperature", 0.7)
    gemini_image_paths = task_data.get("gemini_image_queue")

    if not mode or not user_id:
        logger.error(f"Неполные данные задачи: {task_data}")
        return {"status": "failed", "error": "Недостаточно данных", "user_id": user_id}

    logger.info(f"Начало обработки: mode={mode}, user_id={user_id}")

    try:
        # 1. Загружаем контекст (в нём уже есть сообщение пользователя,
        #    добавленное до постановки в очередь)
        context = await get_user_context_async(user_id, mode)
        if not context:
            context = []

        # 2. Формируем промпт с учётом файла (полная копия логики LlmDirectHandler)
        from prompt_builder import full_prompt
        user_prompt = await full_prompt(
            user_id, user_text, extracted_text, context=context
        )

        # 3. Обрезаем контекст по токенам
        if not extracted_text:
            import token_utils
            model_name_for_limits = MODELS.get(mode)
            truncated = token_utils.truncate_messages_for_token_limit(
                user_prompt,
                model=model_name_for_limits,
                reserve_tokens=2500,
            )
            if len(truncated) > MAX_CONTEXT_MESSAGES:
                truncated = truncated[-MAX_CONTEXT_MESSAGES:]
            user_prompt = truncated

        logger.info(f"{mode}: user_id={user_id}, сообщений={len(user_prompt)}")

        # 4. Вызов LLM с retry
        answer = None
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                async with asyncio.timeout(LLM_TIMEOUT):
                    answer, llm_usage = await _call_llm(
                        mode, user_prompt, model,
                        image_paths=gemini_image_paths,
                    )
                break
            except (asyncio.TimeoutError, RuntimeError) as e:
                last_error = str(e)
                logger.warning(
                    f"Попытка {attempt + 1}/{MAX_RETRIES} для user_id={user_id}: {e}"
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
            except Exception as e:
                last_error = str(e)
                logger.error(
                    f"Неожиданная ошибка (попытка {attempt + 1}): {e}",
                    exc_info=True,
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])

        if answer is None:
            error_msg = f"Все попытки исчерпаны: {last_error}"
            logger.error(f"❌ {error_msg} для user_id={user_id}")
            return {
                "status": "failed",
                "error": error_msg,
                "user_id": user_id,
            }

        # 5. Добавляем ответ модели в контекст
        context.append({"role": "assistant", "content": answer})

        # 6. Обрезаем контекст
        system_msgs = [m for m in context if m.get("role") == "system"]
        non_system = [m for m in context if m.get("role") != "system"]
        if len(non_system) > MAX_CONTEXT_MESSAGES * 2:
            non_system = non_system[-(MAX_CONTEXT_MESSAGES * 2):]
        context = system_msgs + non_system

        # 7. Сохраняем контекст
        await set_user_context_async(user_id, mode, context)

        # 8. Биллинг
        from cost_tracker import calculate_cost
        cost = calculate_cost(usage=llm_usage, model=model, mode=mode)
        await db_module.add_billing(user_id, mode, user_text, 0, cost)

        # 9. Если пользователь запросил формат — создаём файл
        formatted = await check_and_send_formatted(user_text, user_id, answer)
        final_answer = formatted if formatted is not None else answer

        logger.info(f"✅ {mode}: user_id={user_id}, ответ ({len(final_answer)} символов)")
        return {
            "status": "completed",
            "result": final_answer,
            "user_id": user_id,
        }

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        return {
            "status": "failed",
            "error": str(e),
            "user_id": user_id,
        }


async def _handle_task(
    task: dict,
    semaphore: asyncio.Semaphore,
    queue: RedisQueue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Обрабатывает одну задачу с ограничением Semaphore."""
    task_id = task.get("id", "unknown")

    async with semaphore:
        try:
            result = await process_llm_task(task.get("data", {}))

            await loop.run_in_executor(
                None,
                lambda: queue.publish_result(task_id, result, task_type="llm"),
            )

            if result.get("status") == "completed":
                logger.info(
                    f"✅ Задача {task_id[:8]}... выполнена "
                    f"для user_id={result.get('user_id')}"
                )
            else:
                logger.error(
                    f"❌ Задача {task_id[:8]}... провалена: "
                    f"{result.get('error')}"
                )

        except Exception as e:
            logger.exception(f"Ошибка обработки задачи {task_id[:8]}...: {e}")
            await loop.run_in_executor(
                None,
                lambda: queue.publish_result(
                    task_id,
                    {
                        "status": "failed",
                        "error": str(e),
                        "user_id": task.get("data", {}).get("user_id"),
                    },
                    task_type="llm",
                ),
            )


async def run_worker():
    """Основной цикл воркера."""
    try:
        init_clients()
        queue = RedisQueue()
        logger.info(
            f"LLM Worker запущен, слушаю очередь llm... "
            f"Параллельных задач: {MAX_CONCURRENT_LLM}, "
            f"таймаут: {LLM_TIMEOUT}с"
        )

        await db_module.create_database()

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM)
        active_tasks: set[asyncio.Task] = set()
        loop = asyncio.get_running_loop()

        while True:
            try:
                task = await loop.run_in_executor(
                    None,
                    lambda: queue.dequeue(
                        queue_types=["llm"],
                        timeout=5,
                        priority_aware=True,
                    ),
                )

                if task:
                    logger.info(
                        f"📥 Получена задача {task.get('id', 'unknown')[:8]}... "
                        f"(тип: {task.get('type')})"
                    )

                    t = asyncio.create_task(
                        _handle_task(task, semaphore, queue, loop)
                    )
                    active_tasks.add(t)
                    t.add_done_callback(active_tasks.discard)

            except RedisQueueError as e:
                logger.error(f"Ошибка Redis: {e}")
                await asyncio.sleep(5)
            except Exception as e:
                logger.exception(f"Неожиданная ошибка в цикле: {e}")
                await asyncio.sleep(1)

    except KeyboardInterrupt:
        if active_tasks:
            logger.info(
                f"Получен сигнал завершения, "
                f"ожидаю {len(active_tasks)} активных задач..."
            )
            await asyncio.gather(*active_tasks, return_exceptions=True)
        logger.info("Завершение LLM Worker...")
    except Exception as e:
        logger.exception(f"Критическая ошибка воркера: {e}")
        raise
    finally:
        if openai_client:
            await openai_client.close()
        if gemini_client:
            await gemini_client.close()
        logger.info("LLM Worker остановлен")


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("Запуск LLM Worker")
    logger.info("=" * 50)
    asyncio.run(run_worker())
