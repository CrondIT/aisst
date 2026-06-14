import tiktoken
from typing import List, Dict, Any, Union
from global_state import get_token_limit


class TokenCounter:
    """
    Utility class to count tokens for different AI models
    """

    # Энкодеры для разных семейств моделей
    # OpenAI/GigaChat используют cl100k_base
    ENCODER_CACHE = {
        "cl100k_base": None,  # Для OpenAI и GigaChat
        "o200k_base": None,    # Для GPT-4o
    }

    def __init__(self):
        self.openai_encoders = {}
        # Предзагружаем cl100k_base (используется для GigaChat)
        try:
            self.openai_encoders["cl100k_base"] = tiktoken.get_encoding(
                "cl100k_base"
            )
        except Exception:
            pass

    def _get_encoder(self, model: str):
        """
        Получает подходящий энкодер для модели.
        GigaChat использует тот же токенизатор, что и GPT-4 (cl100k_base).
        """
        # Модели GigaChat
        gigachat_models = [
            "gigachat-2-max", "gigachat-2-pro", "gigachat-2",
            "gigachat"
        ]
        
        # Проверяем, является ли модель GigaChat
        for gm in gigachat_models:
            if gm in model.lower():
                # GigaChat использует cl100k_base
                encoder_key = "cl100k_base"
                if encoder_key not in self.openai_encoders:
                    try:
                        self.openai_encoders[encoder_key] = (
                            tiktoken.get_encoding(encoder_key)
                        )
                    except Exception:
                        return None
                return self.openai_encoders[encoder_key]

        # Для остальных моделей используем стандартную логику tiktoken
        if model not in self.openai_encoders:
            try:
                self.openai_encoders[model] = tiktoken.encoding_for_model(
                    model
                )
            except KeyError:
                self.openai_encoders[model] = tiktoken.get_encoding(
                    "cl100k_base"
                )
        
        return self.openai_encoders[model]

    def count_openai_tokens(
        self, text: Union[str, None, Any], model: str
    ) -> int:
        """
        Count tokens for OpenAI и GigaChat моделей.
        GigaChat использует тот же токенизатор, что и GPT-4.
        """
        if text is None:
            text = ""
        elif not isinstance(text, str):
            text = str(text)

        if "dall-e" in model or "gpt-image" in model:
            return len(text)

        encoder = self._get_encoder(model)
        if encoder is None:
            # Fallback: приблизительная оценка (1 токен ~ 4 символа)
            return len(text) // 4
        
        return len(encoder.encode(text))

    def count_openai_messages_tokens(
        self, messages: List[Dict], model: str
    ) -> int:
        """
        Count tokens for message list (OpenAI и GigaChat).
        """
        if "dall-e" in model or "gpt-image" in model:
            total_chars = 0
            for message in messages:
                for key, value in message.items():
                    if isinstance(value, str):
                        total_chars += len(value)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict) and "text" in item:
                                total_chars += len(item["text"])
            return total_chars

        encoder = self._get_encoder(model)
        if encoder is None:
            # Fallback: приблизительная оценка по символам
            total_chars = 0
            for message in messages:
                for key, value in message.items():
                    if isinstance(value, str):
                        total_chars += len(value)
            return total_chars // 4  # ~1 токен на 4 символа

        tokens_per_message = 3
        tokens_per_name = 1

        total_tokens = 0
        for message in messages:
            total_tokens += tokens_per_message
            for key, value in message.items():
                if isinstance(value, str):
                    str_value = str(value) if value is not None else ""
                    total_tokens += len(encoder.encode(str_value))
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            if "text" in item:
                                text_value = (
                                    str(item["text"])
                                    if item["text"] is not None
                                    else ""
                                )
                                total_tokens += len(
                                    encoder.encode(text_value)
                                )
                if key == "name":
                    total_tokens += tokens_per_name
        total_tokens += 3
        return total_tokens

    def estimate_gemini_tokens(self, text: str) -> int:
        """
        Estimate tokens for Gemini models
        """
        return len(text) // 4

    def estimate_gemini_image_tokens(self, image_bytes: bytes) -> int:
        """
        Estimate tokens for images in Gemini (rough estimation)
        """
        return min(len(image_bytes) // 250, 200)


# Create a global instance
token_counter = TokenCounter()


def truncate_messages_for_token_limit(
    messages: List[Dict[str, str]],
    model: str,
    reserve_tokens: int = 1000,
) -> List[Dict[str, str]]:
    """
    Truncate messages to fit within token limit
    """
    if not messages:
        return []

    max_tokens = get_token_limit(model)
    # Reserve some tokens for response
    available_tokens = max_tokens - reserve_tokens
    if available_tokens <= 0:
        return []

    # For image generation models,
    # use character-based limits instead of token limits
    if "dall-e" in model or "gpt-image" in model:
        # For image generation, we just need to limit the prompt length
        # Usually image models have character limits for prompts
        # Combine all text content to check against character limit
        total_text = ""
        for msg in messages:
            for key, value in msg.items():
                if isinstance(value, str):
                    total_text += (
                        value + " "
                    )  # Add space between different message parts

        # If total text is within reasonable limits, return all messages
        # (image models usually have prompt
        # length limits around 1000-4000 chars)
        if len(total_text) <= max_tokens - reserve_tokens:
            return messages
        else:
            # For image models, we typically only
            # care about the last user message
            # as the prompt for image generation
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    return [msg]  # Return just the user prompt
            return messages  # If no user message found, return all

    # First check if the full message list fits
    total_tokens = token_counter.count_openai_messages_tokens(messages, model)
    if total_tokens <= available_tokens:
        return messages

    # If not, we need to truncate
    # Keep the system message if present,
    # and remove oldest user/assistant pairs
    # Separate system message from conversation
    system_message = None
    conversation_messages = []

    for msg in messages:
        if msg.get("role") == "system" and system_message is None:
            system_message = msg
        else:
            conversation_messages.append(msg)

    # Count tokens in system message
    system_tokens = 0
    if system_message:
        system_tokens = token_counter.count_openai_messages_tokens(
            [system_message], model
        )

    available_for_conversation = available_tokens - system_tokens
    if available_for_conversation <= 0:
        return []

    # Start from the end and keep the most recent messages
    truncated_conversation = []
    current_tokens = 0

    # Go through messages from newest to oldest
    for msg in reversed(conversation_messages):
        msg_tokens = token_counter.count_openai_messages_tokens([msg], model)

        if current_tokens + msg_tokens <= available_for_conversation:
            # Insert next string at beginning to maintain order
            truncated_conversation.insert(0, msg)
            current_tokens += msg_tokens
        else:
            break  # Can't fit more messages

    # Combine system message with truncated conversation
    result = []
    if system_message:
        result.append(system_message)
    result.extend(truncated_conversation)

    return result


def check_token_usage(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: int = None,
    reserve_tokens: int = 1000,
) -> Dict[str, Any]:
    """
    Check token usage and return information about it
    """
    if max_tokens is None:
        max_tokens = get_token_limit(model)

    # For image generation models, use character-based counting
    if "dall-e" in model or "gpt-image" in model:
        # Count total characters in all messages
        total_chars = 0
        for msg in messages:
            for key, value in msg.items():
                if isinstance(value, str):
                    total_chars += len(value)

        available_chars = max_tokens - reserve_tokens
        return {
            "total_tokens": total_chars,  # Characters for image models
            "max_tokens": max_tokens,
            "available_tokens": available_chars,  # Available characters
            "reserve_tokens": reserve_tokens,
            "is_within_limit": total_chars <= available_chars,
            "excess_tokens": max(0, total_chars - available_chars),
        }

    available_tokens = max_tokens - reserve_tokens
    total_tokens = token_counter.count_openai_messages_tokens(messages, model)

    return {
        "total_tokens": total_tokens,
        "max_tokens": max_tokens,
        "available_tokens": available_tokens,
        "reserve_tokens": reserve_tokens,
        "is_within_limit": total_tokens <= available_tokens,
        "excess_tokens": max(0, total_tokens - available_tokens),
    }
