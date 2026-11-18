"""Google Chirp 3 conversation transcription."""
import base64
from collections import Counter
from functools import lru_cache
import io
import logging

from app.audio.validation import validate_audio
from app.errors import UserFacingError
from app.settings import get_settings

RATE = 16000
MAX_SAMPLES = 59 * RATE
log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _session():
    import google.auth
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport.requests import AuthorizedSession

    try:
        credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
    except GoogleAuthError:
        raise UserFacingError('service_authentication') from None
    return AuthorizedSession(credentials, max_refresh_attempts=0)


def _chunks(samples):
    while len(samples) > MAX_SAMPLES:
        # Cut at a quiet point near the maximum length to keep short turns intact.
        step = RATE // 20
        cut = min(range(MAX_SAMPLES - 3 * RATE, MAX_SAMPLES, step),
                  key=lambda i: abs(samples[i-step:i+step]).mean())
        yield samples[:cut]
        samples = samples[cut:]
    yield samples


def _recognize(samples, project):
    from google.auth.exceptions import GoogleAuthError
    from requests import ConnectionError, Timeout

    url = f'https://us-speech.googleapis.com/v2/projects/{project}/locations/us/recognizers/_:recognize'
    body = {
        'config': {
            'explicitDecodingConfig': {'encoding': 'LINEAR16', 'sampleRateHertz': RATE, 'audioChannelCount': 1},
            'languageCodes': ['en-US', 'ko-KR'],
            'model': 'chirp_3',
        },
        'content': base64.b64encode((samples.clip(-1, 32767/32768) * 32768).astype('<i2').tobytes()).decode('ascii'),
    }
    try:
        response = _session().post(url, json=body, timeout=(5, 25))
    except GoogleAuthError:
        raise UserFacingError('service_authentication') from None
    except Timeout:
        raise UserFacingError('request_timeout') from None
    except ConnectionError:
        raise UserFacingError('connection_failed') from None
    if not response.ok:
        status = response.status_code
        log.warning('google_stt_request_failed status=%s', status)
        code = {401: 'service_authentication', 403: 'service_authentication',
                429: 'rate_limited'}.get(status, 'service_unavailable' if status >= 500 else 'service_configuration')
        raise UserFacingError(code)
    return response.json().get('results', [])


def transcribe(audio_bytes):
    from faster_whisper.audio import decode_audio

    project = get_settings().GOOGLE_CLOUD_PROJECT
    if not project:
        raise UserFacingError('service_configuration')
    validate_audio(audio_bytes)
    samples = decode_audio(io.BytesIO(audio_bytes), sampling_rate=RATE)
    parts = []
    languages = Counter()
    for chunk in _chunks(samples):
        for result in _recognize(chunk, project):
            alternatives = result.get('alternatives') or []
            text = alternatives[0].get('transcript', '').strip() if alternatives else ''
            if text:
                parts.append(text)
                language = result.get('languageCode', '').split('-')[0]
                if language in {'ko', 'en'}:
                    languages[language] += len(text.split())
    if languages:
        language = languages.most_common(1)[0][0]
    else:
        language = 'ko' if any('가' <= ch <= '힣' for part in parts for ch in part) else 'en'
    return ' '.join(parts), language
