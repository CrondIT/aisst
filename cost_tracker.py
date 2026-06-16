"""
Модуль подсчёта токенов: предварительная оценка и извлечение из ответов SDK.
"""
import os
from dataclasses import dataclass
from typing import Any, Literal

import token_utils


@dataclass
class UsageInfo:
    """Фактическое использование токенов, полученное из ответа API."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def _is_image_model(model: str) -> bool:
    """Проверяет, является ли модель моделью для генерации изображений."""
    return "dall-e" in model or "gpt-image" in model


async def estimate_input_tokens(
    messages: list[dict] | None = None,
    model: str = "",
    image_paths: list[str] | None = None,
    prompt: str | None = None,
) -> int:
    """
    Предварительная оценка токенов в запросе ДО отправки.

    Для текстовых моделей — точный подсчёт через tiktoken.
    Для моделей изображений (gpt-image, dall-e) — подсчёт символов.
    Для multimodel (Gemini/OpenAI chat + изображения) — текст + изображения.

    Args:
        messages: список сообщений (для чата)
        model: имя модели
        image_paths: пути к изображениям (для multimodal)
        prompt: строка промпта (для image generation/edit)

    Returns:
        Количество токенов (для image-моделей — символов).
    """
    if _is_image_model(model):
        text = prompt or ""
        for msg in (messages or []):
            text += msg.get("content", "") if isinstance(msg, dict) else str(msg)
        return len(text)

    text_tokens = 0
    if messages:
        text_tokens = token_utils.token_counter.count_openai_messages_tokens(
            messages, model
        )
    elif prompt is not None:
        text_tokens = token_utils.token_counter.count_openai_tokens(prompt, model)

    image_tokens = 0
    if image_paths:
        for path in image_paths:
            if path and os.path.exists(path):
                try:
                    size = os.path.getsize(path)
                    image_tokens += (
                        token_utils.token_counter.estimate_gemini_image_tokens(
                            b" " * size
                        )
                    )
                except OSError:
                    image_tokens += 258

    return text_tokens + image_tokens


def extract_usage_from_response(
    response: Any,
    client_type: Literal["openai", "gemini", "gigachat"],
) -> UsageInfo | None:
    """
    Извлекает фактическое использование токенов из ответа SDK.

    Поля в ответах:
      OpenAI  (responses.create) → response.usage.input_tokens / output_tokens
      Gemini  → response.usage_metadata.prompt_token_count / candidates_token_count
      GigaChat → response.usage.prompt_tokens / completion_tokens
    """
    if client_type == "openai":
        usage = getattr(response, "usage", None)
        if usage:
            inp = getattr(usage, "input_tokens", 0) or 0
            out = getattr(usage, "output_tokens", 0) or 0
            return UsageInfo(
                prompt_tokens=inp,
                completion_tokens=out,
                total_tokens=inp + out,
            )

    elif client_type == "gemini":
        usage = getattr(response, "usage_metadata", None)
        if usage:
            inp = getattr(usage, "prompt_token_count", 0) or 0
            out = getattr(usage, "candidates_token_count", 0) or 0
            return UsageInfo(
                prompt_tokens=inp,
                completion_tokens=out,
                total_tokens=inp + out,
            )

    elif client_type == "gigachat":
        usage = getattr(response, "usage", None)
        if usage:
            inp = getattr(usage, "prompt_tokens", 0) or 0
            out = getattr(usage, "completion_tokens", 0) or 0
            return UsageInfo(
                prompt_tokens=inp,
                completion_tokens=out,
                total_tokens=inp + out,
            )

    return None


def count_response_tokens(text: str, model: str) -> int:
    """
    Подсчитывает токены в ответе модели (fallback, если SDK не отдал usage).
    """
    return token_utils.token_counter.count_openai_tokens(text, model)


def calculate_cost(
    usage: UsageInfo | None,
    model: str,
    mode: str,
    *,
    is_image_gen: bool = False,
    is_image_edit: bool = False,
    image_quality: str | None = None,
    image_size: str = "1024x1024",
    image_count: int = 1,
) -> int:
    """
    Рассчитывает стоимость в coins на основе usage или фикс. цены.

    Приоритет:
    1. Image gen/edit → фикс цена из pricing
    2. usage передан → prompt_tokens * rate + completion_tokens * rate
    3. usage не передан → default_cost
    """
    from config import pricing

    # === Изображения ===
    if is_image_gen or is_image_edit:
        cost = pricing.image_edit_cost if is_image_edit else pricing.image_gen_cost

        quality_mult = {
            "high": pricing.image_high_quality_mult,
            "medium": pricing.image_medium_quality_mult,
            "low": pricing.image_low_quality_mult,
        }.get(image_quality.lower() if image_quality else "low", 1.0)
        cost *= quality_mult

        try:
            w, h = map(int, image_size.lower().split("x"))
            if w > pricing.image_large_size_threshold or h > pricing.image_large_size_threshold:
                cost *= pricing.image_large_size_mult
        except (ValueError, AttributeError):
            pass

        cost *= image_count
        return max(1, round(cost))

    # === Токеновые модели ===
    if usage is not None:
        model_lower = model.lower()

        def _rate(rates: dict[str, float]) -> float:
            sorted_keys = sorted(rates, key=len, reverse=True)
            for key in sorted_keys:
                if key in model_lower:
                    return rates[key]
            return pricing.default_cost

        prompt_rate = _rate(pricing.prompt_per_1m)
        completion_rate = _rate(pricing.completion_per_1m)

        total = (
            usage.prompt_tokens * prompt_rate
            + usage.completion_tokens * completion_rate
        ) / 1_000_000
        return max(1, round(total))

    return round(pricing.default_cost)
