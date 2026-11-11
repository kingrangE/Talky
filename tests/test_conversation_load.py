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
