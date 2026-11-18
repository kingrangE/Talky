"""Reserve Google synthesis usage without resetting the existing experiment ledger."""
import json
import os
from pathlib import Path
import tempfile

from sqlalchemy import text as sql

from app.db.postgres import get_engine
from app.errors import UserFacingError
from app.settings import get_settings

CHARACTER_LIMIT = 50000


def usage():
    # Keep the original ledger and counters so removing the comparison cannot reset paid usage.
    path = get_settings().mock_exam_data_path / 'tts-comparison-usage.json'
    values = json.loads(path.read_text()) if path.exists() else {'chirp': 0, 'neural2': 0}
    if set(values) != {'chirp', 'neural2'} or any(type(value) is not int or value < 0 for value in values.values()):
        raise UserFacingError('service_configuration')
    return values


def reserve(characters):
    if not 0 < characters <= 1000:
        raise ValueError('invalid speech input length')
    with get_engine().begin() as connection:
        connection.execute(sql('SELECT pg_advisory_xact_lock(:key)'), {'key': 74231932})
        values = usage()
        if values['neural2'] + characters > CHARACTER_LIMIT:
            raise UserFacingError('rate_limited')
        values['neural2'] += characters
        directory = get_settings().mock_exam_data_path
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as output:
            filename = Path(output.name)
            json.dump(values, output)
        try:
            os.replace(filename, directory / 'tts-comparison-usage.json')
        finally:
            filename.unlink(missing_ok=True)
    # Failures and cancellation conservatively consume the reservation made before the paid call.
