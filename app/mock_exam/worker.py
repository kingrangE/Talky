"""One durable DB worker, supervised so stuck model work can be killed and recovered."""

import logging
import multiprocessing
import signal
import threading
import time

from sqlalchemy import text

from app.db.postgres import get_engine
from app.mock_exam.repository import claim_job, fail_job, recover_jobs
from app.mock_exam.service import _finalize_report, _process_response, run_expiry_cleanup
from app.runtime import ModelBusy, inference_slot
from app.settings import get_settings
from app.conversation import claim_turn, fail_turn, process_turn

log = logging.getLogger(__name__)
WORKER_LOCK = 74201933


def _run_worker(channel) -> None:
    # The session lock makes recovery safe even if several worker containers start.
    with get_engine().connect() as connection:
        while True:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK}
            ).scalar()
            connection.commit()
            if acquired:
                break
            time.sleep(1)
        recover_jobs()
        cleanup_at = 0.0
        while True:
            connection.execute(text("SELECT 1"))
            connection.commit()
            if time.monotonic() >= cleanup_at:
                try:
                    run_expiry_cleanup()
                except Exception:
                    log.exception("exam cleanup failed; will retry")
                cleanup_at = time.monotonic() + get_settings().MOCK_EXAM_CLEANUP_INTERVAL_SECONDS
            job = None
            try:
                with inference_slot(for_exam=True):
                    job = claim_job() or claim_turn()
                    if job:
                        channel.send("started")
                        try:
                            if job['kind'] == 'conversation':
                                process_turn(job)
                            elif job["kind"] == "response":
                                _process_response(
                                    job["id"], job["session_id"], job["question_number"],
                                    job["encrypted_audio_path"], job["client_metrics"],
                                    job["attempt"],
                                )
                            else:
                                _finalize_report(job["session_id"], job["attempt"])
                        except Exception as exc:
                            log.warning("exam job failed: %s", type(exc).__name__)
                            (fail_turn if job['kind']=='conversation' else fail_job)(job, type(exc).__name__)
                        finally:
                            channel.send("idle")
            except ModelBusy:
                pass
            if job is None:
                time.sleep(0.5)


def _stop_process(process) -> None:
    if process.is_alive():
        process.terminate()
        process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)
    else:
        process.join()


def _monitor(process, channel, timeout: float, stop: threading.Event) -> bool:
    deadline = None
    try:
        while process.is_alive() and not stop.is_set():
            if channel.poll(0.2):
                try:
                    event = channel.recv()
                except EOFError:
                    break
                deadline = time.monotonic() + timeout if event == "started" else None
            if deadline is not None and time.monotonic() >= deadline:
                return True
        return False
    finally:
        _stop_process(process)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_args: stop.set())
    context = multiprocessing.get_context("spawn")
    while not stop.is_set():
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_run_worker, args=(sender,))
        process.start()
        sender.close()
        try:
            if _monitor(process, receiver, get_settings().MOCK_EXAM_JOB_TIMEOUT_SECONDS, stop):
                log.warning("exam job exceeded deadline; restarting worker")
        finally:
            receiver.close()
            process.close()
        stop.wait(1)


if __name__ == "__main__":
    main()
