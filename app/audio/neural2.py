"""English Neural2 sentence synthesis. No automatic paid retries."""
import io
import re
import wave
from functools import lru_cache

RATE = 24000
VOICE = 'en-US-Neural2-F'


def segments(text):
    if not text.strip() or len(text) > 4000:
        raise ValueError('invalid speech text length')
    # shortcut: conservative English punctuation rules; use a tokenizer if multilingual TTS is added.
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = []
    carry = ''
    for part in parts:
        carry += part
        if re.search(r'\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc)\.$|\b[A-Z]\.$|(?:\b[A-Za-z]\.){2,}$', carry):
            carry += ' '
            continue
        sentences.append(carry)
        carry = ''
    if carry.strip():
        sentences.append(carry.strip())
    output = []
    for sentence in sentences:
        while len(sentence) > 1000:
            cut = sentence.rfind(' ', 0, 1000)
            if cut <= 0:
                cut = 1000
            output.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if sentence:
            output.append(sentence)
    if len(output) > 32:
        raise ValueError('too many speech segments')
    return output


def decode(wav):
    with wave.open(io.BytesIO(wav), 'rb') as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (RATE, 1, 2):
            raise ValueError('invalid Neural2 audio format')
        data = source.readframes(source.getnframes())
    if not data or len(data) % 2 or len(data) > RATE * 2 * 120:
        raise ValueError('invalid Neural2 audio length')
    return data


def encode(pcm):
    target = io.BytesIO()
    with wave.open(target, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(pcm)
    return target.getvalue()


@lru_cache(maxsize=1)
def client():
    from google.cloud.texttospeech import TextToSpeechClient
    return TextToSpeechClient()


def synthesize(sentence):
    response = client().synthesize_speech(
        input={'text': sentence}, voice={'language_code': 'en-US', 'name': VOICE},
        audio_config={'audio_encoding': 'LINEAR16', 'sample_rate_hertz': RATE},
        retry=None, timeout=15,
    )
    return decode(response.audio_content)
