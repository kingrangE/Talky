"""Compare CPU thread budgets on saved fixtures without taking application queue slots."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
from time import perf_counter

from faster_whisper import WhisperModel

from app.audio import stt_engine
from app.settings import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixtures', type=Path)
    parser.add_argument('--threads', nargs='+', type=int, default=[0, 2, 4, 6])
    parser.add_argument('--repeat', type=int, default=2)
    args = parser.parse_args()
    inputs = [(p.name, p.read_bytes()) for p in sorted(args.fixtures.glob('*.aiff'))]
    if not inputs or args.repeat < 1:
        parser.error('provide AIFF fixtures and at least one repeat')

    settings = get_settings()
    local = threading.local()
    recheck = stt_engine._recheck_language

    def model():
        return local.model

    def measured_recheck(*parts):
        started = perf_counter()
        try:
            return recheck(*parts)
        finally:
            local.recheck_ms = round((perf_counter() - started) * 1000, 1)

    stt_engine._model = model
    stt_engine._recheck_language = measured_recheck

    def load_model():
        started = perf_counter()
        local.model = WhisperModel(settings.WHISPER_MODEL, device=settings.WHISPER_DEVICE,
                                   compute_type=settings.WHISPER_COMPUTE_TYPE, cpu_threads=threads,
                                   download_root=settings.AUDIO_CACHE_DIR + '/whisper', local_files_only=True)
        return round((perf_counter() - started) * 1000, 1)

    def run(sample):
        name, data = sample
        started = perf_counter()
        result, language = stt_engine.transcribe.__wrapped__(data)
        return dict(sample=name, language=language, recheck_ms=local.recheck_ms,
                    signature=hashlib.sha256(result.encode()).hexdigest(),
                    stt_ms=round((perf_counter() - started) * 1000, 1))

    for threads in args.threads:
        for concurrency in (1, 2):
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                barrier = threading.Barrier(concurrency)

                def initialize():
                    load_ms = load_model()
                    barrier.wait(timeout=60)
                    cold = run(inputs[0])
                    barrier.wait(timeout=60)
                    return {'load_ms': load_ms, 'first': cold}

                jobs = [pool.submit(initialize) for _ in range(concurrency)]
                cold = [job.result() for job in jobs]
                print(json.dumps(dict(threads=threads, concurrency=concurrency, cold=cold)), flush=True)
                samples = inputs * args.repeat
                for start in range(0, len(samples), concurrency):
                    batch = samples[start:start + concurrency]
                    started = perf_counter()
                    results = list(pool.map(run, batch))
                    print(json.dumps(dict(threads=threads, concurrency=concurrency,
                                          batch_ms=round((perf_counter() - started) * 1000, 1),
                                          results=results)), flush=True)


if __name__ == '__main__':
    main()
