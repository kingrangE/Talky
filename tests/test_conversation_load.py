from operations.load_conversations import percentile, summary, isolated_database, run_level
import pytest


def test_percentiles_include_rejections_and_do_not_claim_small_sample_pass():
    rows=[{'accepted':True,'outcome':'completed','text_seconds':1,'completion_seconds':2} for _ in range(49)]
    rows.append({'accepted':False,'outcome':'rejected'})
    report=summary(rows,mode='real')
    assert report['text_p95_seconds']==1
    assert report['text_p99_seconds'] is None
    assert report['error_or_harness_timeout_rate']==.02
    assert report['qualification']=='fail' and not report['slo_pass']
    report=summary(rows[:49],mode='real')
    assert report['limits_met_in_sample']
    assert report['qualification']=='insufficient_samples' and not report['slo_pass']
    assert percentile([1,2,3,4,5],.99)==5


def test_runner_refuses_non_test_database():
    with pytest.raises(ValueError,match='dedicated'):
        with isolated_database('postgresql+psycopg://localhost/talky'):
            pass


def test_synthetic_load_uses_distinct_accounts_real_queue_and_cleans_schema(pg_db,tmp_path):
    dsn=pg_db[0].url.render_as_string(hide_password=False)
    result=run_level(dsn,users=3,workers=2,horizon=15,mode='synthetic',report_dir=tmp_path)
    assert result['summary']['completed']==3
    assert len({row['user_id'] for row in result['rows']})==3
    assert all(row['attempts']==1 for row in result['rows'])
    assert not result['summary']['slo_pass']


def test_completion_latency_excludes_fast_failures_but_error_rate_keeps_them():
    rows=[{'accepted':True,'outcome':'failed','completion_seconds':.01} for _ in range(19)]
    rows.append({'accepted':True,'outcome':'completed','text_seconds':1,'completion_seconds':10})
    report=summary(rows,mode='real')
    assert report['full_completion_p95_seconds']==10
    assert report['error_or_harness_timeout_rate']==.95
    assert not report['limits_met_in_sample']



def test_mixed_load_runs_graph_worker_and_cleans_its_source(pg_db,tmp_path,monkeypatch):
    import os
    from app.settings import get_settings
    from app.graph_db.neo4j_client import get_driver
    uri=os.environ.get('TALKY_TEST_NEO4J_URI')
    if not uri:
        pytest.skip('set dedicated TALKY_TEST_NEO4J_URI')
    monkeypatch.setenv('NEO4J_URI',uri)
    get_settings.cache_clear();get_driver.cache_clear()
    dsn=pg_db[0].url.render_as_string(hide_password=False)
    result=run_level(dsn,users=3,workers=2,horizon=15,mode='synthetic',report_dir=tmp_path,graph_worker=True,profile='mixed-memory')
    assert result['summary']['completed']==3
    assert result['graph_worker'] and result['profile']=='mixed-memory'
    assert all(row['source_conversation_id'] for row in result['rows'])
    assert result['queue_samples'][0]['graph_backlog'] is not None
    with get_driver().session() as graph:
        assert graph.run('MATCH (u:User) WHERE u.id IN $ids RETURN count(u) AS n',ids=[r['user_id'] for r in result['rows']]).single()['n']==0
    get_driver().close();get_driver.cache_clear()
