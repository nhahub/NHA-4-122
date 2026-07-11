"""
llm/prompt.py — Chat template formatting and token counting.

Responsibilities:
  - Load and cache the HuggingFace AutoTokenizer from TOKENIZER_PATH.
  - Format a list of message dicts into the ChatML prompt string using
    the Jinja2 template from settings.llm_chat_template (config-driven fallback
    for tokenizers that omit the template from tokenizer_config.json).
  - Count tokens in a raw string for storage in the messages.token_count column.

Nothing in this file touches llama_cpp or the GGUF file.
"""
from __future__ import annotations

from functools import lru_cache

from transformers import AutoTokenizer, PreTrainedTokenizerFast

from core.config import settings

# ── Private ──────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _load_tokenizer() -> PreTrainedTokenizerFast:
    """
    Load the AutoTokenizer from TOKENIZER_PATH exactly once.

    The @lru_cache(maxsize=1) guarantees a single load regardless of how many
    times get_tokenizer() is called — even under concurrent async tasks.
    AutoTokenizer reads tokenizer.json, tokenizer_config.json,
    special_tokens_map.json, and optionally config.json from the directory.
    """
    tokenizer = AutoTokenizer.from_pretrained(
        settings.tokenizer_path,
        trust_remote_code=False,
    )
    return tokenizer  # type: ignore[return-value]


# ── Public API ───────────────────────────────────────────────────────────────


def get_tokenizer() -> PreTrainedTokenizerFast:
    """Return the cached tokenizer instance, loading it on the first call."""
    return _load_tokenizer()


def format_chat_prompt(
    messages: list[dict[str, str]],
    system_prompt: str | None = None,
    tools: list[dict] | None = None,
) -> str:
    """
    Convert a list of {"role": ..., "content": ...} dicts into the
    ChatML-formatted string the Qwen3 model expects.

    Args:
        messages:      Conversation history, oldest first. Must include at
                       least the most recent user message.
        system_prompt: If provided, prepended as a {"role": "system"} message.
                       Falls back to settings.llm_system_prompt if None.
        tools:         If provided, the tokenizer's native Jinja template is
                       used (which renders Hermes-style <tool_call> blocks).
                       If None, the fallback chat_template override is used.

    Returns:
        A single string ending with '<|im_start|>assistant\n', which is the
        signal for the model to begin generating its response.
    """
    tokenizer = get_tokenizer()

    effective_system_prompt = system_prompt or settings.llm_system_prompt
    full_messages = [{"role": "system", "content": effective_system_prompt}] + messages

    if tools is not None:
        # Use the tokenizer's native Jinja template — it knows the Hermes
        # tool-call format the model was instruction-tuned on.
        # Do NOT pass chat_template= override here; the fallback has no tool support.
        prompt: str = tokenizer.apply_chat_template(
            full_messages,
            tools=tools,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    else:
        prompt = tokenizer.apply_chat_template(
            full_messages,
            chat_template=settings.llm_chat_template,
            tokenize=False,              # Return string, not token ID list
            add_generation_prompt=True,  # Append the <|im_start|>assistant\n suffix
            enable_thinking=settings.llm_enable_thinking,
        )
    return prompt


def count_tokens(text: str) -> int:
    """
    Return the number of tokens in `text` using the loaded tokenizer.

    Called by chat_service before persisting a message so that token_count
    is always stored. Never call this inside the generation hot-path —
    it tokenizes the entire text on each call.
    """
    tokenizer = get_tokenizer()
    return len(tokenizer.encode(text))
