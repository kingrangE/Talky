import pytest
from app.retry import retry_delay, error_name


@pytest.mark.parametrize('error', [ValueError('private input'), TypeError('invalid'), PermissionError('denied')])
def test_permanent_errors_do_not_retry(error):
    assert retry_delay(error, 1) is None
    assert 'private' not in error_name(error)


def test_transient_errors_have_bounded_increasing_delay():
    assert 2 <= retry_delay(TimeoutError(), 1).total_seconds() < 3
    assert 30 <= retry_delay(ConnectionError(), 10).total_seconds() < 31


def test_job_budget_rejects_stage_and_is_reset():
    from app.runtime import job_budget, require_budget
    with job_budget(1):
        with pytest.raises(TimeoutError):
            require_budget(60)
    require_budget(60)


def test_database_connection_loss_is_retryable():
    import psycopg
    assert retry_delay(psycopg.OperationalError('connection lost'), 1) is not None
