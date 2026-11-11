"""Preload audio assets at image build and model weights before claiming work."""
import argparse
import json
import urllib.request
from app.audio.stt_engine import _model
from app.audio.tts_engine import _voice
from app.settings import get_settings


def warmup(*, audio_only=False):
    _model()
    if _voice('en') is None:
        raise RuntimeError('English TTS assets are not ready')
    cfg = get_settings()
    if not audio_only and not cfg.use_openai:
        request = urllib.request.Request(cfg.OLLAMA_BASE_URL.rstrip('/')+'/api/generate',
            data=json.dumps({'model':cfg.MODEL_NAME, 'prompt':'', 'stream':False,
                             'keep_alive':'10m'}).encode(), headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=cfg.LLM_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
            if not payload.get('done') or payload.get('error'):
                raise RuntimeError('model warmup failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--audio-only', action='store_true')
    warmup(audio_only=parser.parse_args().audio_only)
