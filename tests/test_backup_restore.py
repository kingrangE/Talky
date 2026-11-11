"""Restore a real pg_dump into a new disposable database; never overwrite source."""
import os
import shutil
import subprocess
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select, text

from app.db import repo
from app.db.models import Conversation, ConversationTurn, GraphSyncEvent, Message, User, PromptSnapshot
from app.mock_exam.privacy import EncryptedAudioStore
from app.settings import get_settings


def test_real_dump_restore_preserves_history_pending_work_and_encrypted_audio(pg_db, tmp_path, monkeypatch):
    container = os.environ.get('TALKY_TEST_POSTGRES_CONTAINER')
    if not container:
        pytest.skip('set TALKY_TEST_POSTGRES_CONTAINER to the dedicated local PostgreSQL container')
    assert container.startswith('talky-ops-')
    engine, scope = pg_db
    url = engine.url
    assert url.database.endswith('_test')
    with engine.connect() as connection:
        schema = connection.scalar(text('SELECT current_schema()'))
    with scope() as session:
        user = User(display_name='restore-test')
        session.add(user)
        session.flush()
        conv = Conversation(user_id=user.id)
        session.add(conv)
        session.flush()
        uid, cid = str(user.id), str(conv.id)
    from app.llm.prompt_cache import save
    save('talky/en-reply', 7, 'Verified release prompt')
    repo.save_turn(cid, uid, 'Source survives restore', 'Saved reply')
    key = Fernet.generate_key().decode()
    monkeypatch.setenv('MOCK_EXAM_AUDIO_KEY', key)
    get_settings.cache_clear()
    store = EncryptedAudioStore(tmp_path/'original'/'audio')
    path = store.save(cid, 1, b'private input audio')
    from datetime import datetime, timedelta, timezone
    with scope() as session:
        session.add(ConversationTurn(id=uuid.uuid4(), conversation_id=uuid.UUID(cid), user_id=uuid.UUID(uid),
            input_digest='a'*64, input_audio_path=path, queue_deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
            audio_expires_at=datetime.now(timezone.utc)+timedelta(hours=72)))
    dump = subprocess.run(['docker','exec',container,'pg_dump','-U',url.username,'-d',url.database,
        '--schema',schema,'--format=custom','--no-owner','--no-acl'], capture_output=True, check=True).stdout
    assert dump.startswith(b'PGDMP')
    name = 'talky_restore_' + uuid.uuid4().hex + '_test'
    control = create_engine(url, isolation_level='AUTOCOMMIT')
    restored = None
    try:
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        subprocess.run(['docker','exec','-i',container,'pg_restore','-U',url.username,'-d',name,
            '--exit-on-error','--single-transaction','--no-owner','--no-acl'], input=dump,
            capture_output=True, check=True)
        restored = create_engine(url.set(database=name), connect_args={'options':f'-c search_path={schema}'})
        with restored.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(Message)) == 2
            assert connection.scalar(select(Message.content).where(Message.role=='user')) == 'Source survives restore'
            assert connection.scalar(select(func.count()).select_from(GraphSyncEvent)) == 1
            assert connection.scalar(select(ConversationTurn.input_audio_path)) == path
            assert connection.scalar(select(PromptSnapshot.body)) == 'Verified release prompt'
        shutil.copytree(store.root, tmp_path/'restored'/'audio')
        recovered = EncryptedAudioStore(tmp_path/'restored'/'audio')
        assert recovered.load(path) == b'private input audio'
        monkeypatch.setenv('MOCK_EXAM_AUDIO_KEY', Fernet.generate_key().decode())
        get_settings.cache_clear()
        with pytest.raises(ValueError, match='decrypted'):
            EncryptedAudioStore(tmp_path/'restored'/'audio').load(path)
    finally:
        if restored:
            restored.dispose()
        with control.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}"')
        control.dispose()
