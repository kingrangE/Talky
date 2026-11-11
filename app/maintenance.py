"""One elected background cleaner; request workers never wait for bucket scans."""
import logging
import threading
from itertools import islice
from sqlalchemy import text
from app.db.postgres import get_engine
from app.mock_exam.service import run_expiry_cleanup
from app.mock_exam.privacy import audio_store
from app.mock_exam.repository import referenced_audio_paths
from app.settings import get_settings

log = logging.getLogger(__name__)
CLEANUP_LOCK = 74201980


def sweep_batch(iterator, store, limit=100):
    paths = list(islice(iterator, limit))
    referenced = referenced_audio_paths(paths) if paths else set()
    for path in paths:
        if path not in referenced:
            store.delete(path)
    return bool(paths)


def run(stop):
    while not stop.is_set():
        try:
            with get_engine().connect() as connection:
                elected = connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': CLEANUP_LOCK})
                connection.commit()
                if not elected:
                    stop.wait(5)
                    continue
                try:
                    iterator = None
                    while not stop.is_set():
                        run_expiry_cleanup(sweep=False)
                        if iterator is None:
                            import time
                            store = audio_store()
                            iterator = iter(store.older_than(time.time() - 3600))
                        if not sweep_batch(iterator, store):
                            iterator = None
                        stop.wait(get_settings().MOCK_EXAM_CLEANUP_INTERVAL_SECONDS)
                finally:
                    connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': CLEANUP_LOCK})
                    connection.commit()
        except Exception:
            log.exception('background cleanup deferred')
            stop.wait(5)


def start(stop):
    thread = threading.Thread(target=run, args=(stop,), daemon=True, name='audio-maintenance')
    thread.start()
    return thread
