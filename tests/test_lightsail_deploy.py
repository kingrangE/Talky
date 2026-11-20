import pytest

from infrastructure.lightsail.deploy import ci_passed


COMMIT = 'a' * 40
PASSING_PUSH = {
    'head_sha': COMMIT, 'head_branch': 'main', 'event': 'push',
    'status': 'completed', 'conclusion': 'success',
}


def test_deploy_gate_allows_a_successful_push_of_the_exact_commit():
    assert ci_passed([PASSING_PUSH], COMMIT)


@pytest.mark.parametrize('changed', [
    {'head_sha': 'b' * 40}, {'head_branch': 'feature'}, {'event': 'pull_request'},
    {'status': 'in_progress', 'conclusion': None}, {'conclusion': 'failure'},
])
def test_deploy_gate_rejects_other_commits_or_nonpassing_runs(changed):
    assert not ci_passed([PASSING_PUSH | changed, PASSING_PUSH], COMMIT)


def test_deploy_gate_waits_until_a_workflow_run_exists():
    assert not ci_passed([], COMMIT)
