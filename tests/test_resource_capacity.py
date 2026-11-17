import threading
import time
import pytest
from concurrent.futures import ThreadPoolExecutor
from app.runtime import resource_slot
from app.settings import get_settings


@pytest.mark.parametrize('kind,setting', [('audio', 'AUDIO_CAPACITY'), ('inference', 'INFERENCE_CAPACITY')])
def test_capacity_two_allows_two_tasks_and_queues_the_third(pg_db, monkeypatch, kind, setting):
    monkeypatch.setenv(setting, '2')
    get_settings.cache_clear()
    entered, release, third = threading.Barrier(3), threading.Event(), threading.Event()

    def occupy():
        with resource_slot(kind):
            entered.wait(timeout=3)
            assert release.wait(3)

    def later():
        with resource_slot(kind):
            third.set()

    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = [pool.submit(occupy) for _ in range(2)]
        try:
            entered.wait(timeout=3)
            pending = pool.submit(later)
            assert not third.wait(.2)
        finally:
            release.set()
        for task in tasks:
            task.result()
        pending.result()
    assert third.is_set()


def test_cpu_stage_does_not_hold_inference_capacity(pg_db):
    entered, release = threading.Event(), threading.Event()
    def cpu_work():
        with resource_slot('audio'):
            entered.set()
            assert release.wait(3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(cpu_work)
        try:
            assert entered.wait(1)
            start = time.monotonic()
            with resource_slot('inference'):
                assert time.monotonic()-start < .5
        finally:
            release.set()
        pending.result()


def test_inference_capacity_serializes_generation(pg_db):
    entered, release, second = threading.Event(), threading.Event(), threading.Event()
    def first():
        with resource_slot('inference'):
            entered.set()
            assert release.wait(3)
    def later():
        with resource_slot('inference'):
            second.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        assert entered.wait(1)
        b = pool.submit(later)
        try:
            assert not second.wait(.2)
        finally:
            release.set()
        a.result(); b.result()
    assert second.is_set()


def test_factory_model_generation_enters_capacity_boundary(monkeypatch):
    from contextlib import contextmanager
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_ollama import ChatOllama
    from app.llm import capacity
    calls=[]
    @contextmanager
    def slot(kind):
        calls.append(kind)
        yield
    monkeypatch.setattr(capacity, 'resource_slot', slot)
    monkeypatch.setattr(ChatOllama, '_generate', lambda *_a, **_kw: ChatResult(generations=[ChatGeneration(message=AIMessage(content='OK'))]))
    model=capacity.SlotChatOllama(model='unused')
    assert model.bind(temperature=0).invoke('Hi').content == 'OK'
    assert calls == ['inference']


def test_router_can_abandon_capacity_wait_without_entering_model(pg_db):
    import pytest
    entered,release=threading.Event(),threading.Event()
    def occupy():
        with resource_slot('inference'):
            entered.set()
            assert release.wait(3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task=pool.submit(occupy)
        try:
            assert entered.wait(1)
            start=time.monotonic()
            with pytest.raises(TimeoutError,match='capacity wait'):
                with resource_slot('inference',max_wait_seconds=.1):
                    pytest.fail('capacity exceeded')
            assert time.monotonic()-start < 1
        finally:
            release.set()
        task.result()
