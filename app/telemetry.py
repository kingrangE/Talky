"""Stage latency without recording conversation text or audio."""
import json
import logging
import time
from functools import wraps

log = logging.getLogger('talky.metrics')


def timed_stage(name, operation):
    @wraps(operation)
    def run(state):
        from app.runtime import require_budget
        from app.settings import get_settings
        require_budget(get_settings().LLM_TIMEOUT_SECONDS if name in {'en_reply','ko_coach','better_expr'} else 1)
        started = time.monotonic()
        outcome = 'ok'
        try:
            return operation(state)
        except Exception:
            outcome = 'error'
            raise
        finally:
            log.info(json.dumps({'event': 'stage', 'stage': name, 'outcome': outcome,
                                 'duration_ms': round((time.monotonic()-started)*1000, 2)}, separators=(',', ':')))
    return run
