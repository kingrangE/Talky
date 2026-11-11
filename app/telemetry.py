"""Stage latency without recording conversation text or audio."""
import json
import logging
import time
from functools import wraps

log = logging.getLogger('talky.metrics')


def timed_stage(name, operation):
    @wraps(operation)
    def run(state):
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
