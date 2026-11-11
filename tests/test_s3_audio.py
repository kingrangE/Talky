import io
from datetime import datetime, timezone

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from cryptography.fernet import Fernet

from app.mock_exam.privacy import S3EncryptedAudioStore
from app.settings import get_settings


@pytest.fixture
def s3_store(monkeypatch):
    monkeypatch.setenv('AUDIO_S3_BUCKET', 'talky-test-audio')
    monkeypatch.setenv('MOCK_EXAM_AUDIO_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('MOCK_EXAM_SIGNING_SECRET', 'shared-test-only-secret')
    get_settings.cache_clear()
    client = boto3.client('s3', region_name='ap-northeast-2', aws_access_key_id='test', aws_secret_access_key='test')
    monkeypatch.setattr(boto3, 'client', lambda *_, **__: client)
    with Stubber(client) as stub:
        yield S3EncryptedAudioStore(), stub
        stub.assert_no_pending_responses()


def test_s3_encryption_roundtrip_across_instances_and_scoped_deletion(s3_store):
    store, stub = s3_store
    stub.add_response('put_object', {}, {'Bucket':store.bucket, 'Key':ANY, 'Body':ANY,
        'ServerSideEncryption':'AES256', 'ContentType':'application/octet-stream'})
    path = store.save('session', 1, b'private audio')
    sealed = store._fernet.encrypt(b'private audio')
    assert b'private audio' not in sealed
    stub.add_response('get_object', {'Body':StreamingBody(io.BytesIO(sealed), len(sealed))},
                      {'Bucket':store.bucket, 'Key':'audio/'+path})
    other = S3EncryptedAudioStore()
    assert other.load(path) == b'private audio'
    stub.add_response('delete_object', {}, {'Bucket':store.bucket, 'Key':'audio/'+path})
    other.delete(path)
    with pytest.raises(ValueError):
        store.load('../secret.fernet')
    with pytest.raises(ValueError):
        store.save('../escape', 1, b'audio')


def test_storage_error_is_retryable_and_listing_is_prefix_scoped(s3_store):
    store, stub = s3_store
    stub.add_client_error('delete_object', service_error_code='AccessDenied', http_status_code=403)
    with pytest.raises(OSError):
        store.delete('session/q01.fernet')
    stub.add_response('list_objects_v2', {'Contents':[{'Key':'audio/session/q01.fernet',
        'LastModified':datetime(2020,1,1,tzinfo=timezone.utc)}]}, {'Bucket':store.bucket, 'Prefix':'audio/'})
    assert list(store.older_than(1700000000)) == ['session/q01.fernet']


def test_s3_mode_requires_shared_keys(monkeypatch):
    monkeypatch.setenv('MOCK_EXAM_AUDIO_KEY', '')
    get_settings.cache_clear()
    with pytest.raises(ValueError, match='shared'):
        S3EncryptedAudioStore()
