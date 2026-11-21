"""Durable DB workers, supervised so stuck model work can be killed and recovered."""

from contextlib import closing
from pathlib import Path
import logging
import json
from datetime import datetime, timezone
import multiprocessing
import signal
import threading
import time

from sqlalchemy import or_, select

from app.db.job_notifications import JobListener
from app.db.models import ConversationTurn, MockExamReport, MockExamResponse
from app.db.postgres import session_scope
from app.mock_exam.repository import claim_job, fail_job, recover_jobs
from app.mock_exam.service import _finalize_report, _process_response
from app.runtime import ModelBusy, job_budget
from app.settings import get_settings
from app.conversation import claim_turn, fail_turn, process_turn

log = logging.getLogger(__name__)


def _idle_timeout() -> float:
    # Keep the existing cadence while delayed retries, expiry or lease recovery need polling.
    with session_scope() as session:
        pending = session.scalar(select(or_(
            select(ConversationTurn.id).where(ConversationTurn.status.in_(("queued", "processing"))).exists(),
            select(MockExamResponse.id).where(MockExamResponse.status.in_(("queued", "processing"))).exists(),
            select(MockExamReport.id).where(MockExamReport.status.in_(("queued", "scoring"))).exists(),
        )))
    return 0.5 if pending else min(2.0, get_settings().CONVERSATION_QUEUE_WAIT_SECONDS / 2)


def _run_worker(channel) -> None:
    if get_settings().WORKER_WARMUP:
        from app.warmup import warmup
        warmup()
    with closing(JobListener()) as listener:
        _run_jobs(channel, listener)


def _run_jobs(channel, listener: JobListener) -> None:
    prefer_chat = False
    while True:
        # Commit LISTEN before checking the tables, including after reconnecting.
        listener.subscribe()
        recover_jobs()
        channel.send("heartbeat")
        job = None
        try:
            job = (claim_turn() or claim_job()) if prefer_chat else (claim_job() or claim_turn())
            if job:
                prefer_chat = not prefer_chat
                channel.send("started")
                started = time.monotonic()
                wait_ms = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(job["created_at"])).total_seconds()*1000)
                if job["kind"] == "conversation" and job.get("input_audio_path"):
                    log.info(json.dumps({"event": "voice_job_claimed", "request_id": job["id"],
                        "attempt": job["attempt"], "queue_wait_ms": round(wait_ms, 2)}))
                outcome = "ok"
                try:
                    with job_budget(get_settings().MOCK_EXAM_JOB_TIMEOUT_SECONDS):
                        _process_job(job)
                except Exception as exc:
                    outcome = "error"
                    log.warning("job failed: %s", type(exc).__name__)
                    (fail_turn if job["kind"] == "conversation" else fail_job)(job, exc)
                finally:
                    log.info(json.dumps({"event": "job", "kind": job["kind"], "outcome": outcome,
                        "attempt": job["attempt"], "queue_wait_ms": round(wait_ms, 2),
                        "duration_ms": round((time.monotonic()-started)*1000, 2)}))
                    channel.send("idle")
        except ModelBusy:
            pass
        if job is None:
            if listener.connection is None:
                time.sleep(0.5)
            else:
                listener.wait(_idle_timeout())


def _process_job(job):
    if job['kind'] == 'conversation':
        process_turn(job)
    elif job["kind"] == "response":
        _process_response(
            job["id"], job["session_id"], job["question_number"],
            job["encrypted_audio_path"], job["client_metrics"], job["attempt"],
        )
    else:
        _finalize_report(job["session_id"], job["attempt"])


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
                Path('/tmp/talky-worker-heartbeat').touch()
                if event == 'started':
                    deadline = time.monotonic() + timeout
                elif event == 'idle':
                    deadline = None
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
    from app.maintenance import start
    start(stop)
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
