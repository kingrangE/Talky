"""Conservative byte-fallback bound for text-only model inputs."""
from app.settings import get_settings


def input_budget():
    cfg = get_settings()
    # One UTF-8 byte per token is conservative for the supported text tokenizers.
    available = cfg.LLM_CONTEXT_TOKENS - cfg.LLM_MAX_OUTPUT_TOKENS - 2048
    if available < 1024:
        raise ValueError('context must reserve output and at least 1024 input tokens plus template/schema overhead')
    return min(cfg.LLM_MAX_INPUT_BYTES, available)
