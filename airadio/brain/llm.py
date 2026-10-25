"""Local AI model calls through Ollama, with automatic fallback model."""
import json
import logging
import threading

from .. import config

log = logging.getLogger("airadio.llm")

# One model call at a time: renders, safety checks and WhatsApp chat all share the GPU,
# and Ollama queues anyway. The lock keeps timing predictable.
_LLM_LOCK = threading.Lock()


class LLMError(RuntimeError):
    pass


def _client():
    import ollama
    try:
        return ollama.Client(timeout=config.LLM_TIMEOUT_SEC)
    except TypeError:  # very old client without timeout kwarg
        return ollama.Client()


def chat(prompt: str, *, json_mode: bool = False, temperature: float = 0.8) -> str:
    """Send one prompt, return the reply text. Raises LLMError if both models fail."""
    messages = [{"role": "user", "content": prompt}]
    kwargs = {"options": {"temperature": temperature}}
    if json_mode:
        kwargs["format"] = "json"
    models = [config.LLM_MODEL]
    if config.LLM_FALLBACK_MODEL and config.LLM_FALLBACK_MODEL != config.LLM_MODEL:
        models.append(config.LLM_FALLBACK_MODEL)

    last_err = None
    with _LLM_LOCK:
        for model in models:
            try:
                resp = _client().chat(model=model, messages=messages, **kwargs)
                text = resp["message"]["content"]
                if text and text.strip():
                    return text.strip()
                last_err = LLMError(f"{model} returned empty text")
            except Exception as e:  # connection refused, model missing, timeout...
                last_err = e
                log.warning("model %s failed: %s", model, e)
    raise LLMError(str(last_err))


def chat_json(prompt: str) -> dict:
    raw = chat(prompt, json_mode=True, temperature=0.1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Some models wrap JSON in prose; grab the first {...} block.
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start:end + 1])
        raise LLMError(f"not JSON: {raw[:120]}")


def is_available() -> bool:
    try:
        _client().list()
        return True
    except Exception:
        return False
