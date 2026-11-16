import { test } from 'node:test';
import assert from 'node:assert/strict';
import { playAudio, playbackMessage, recordingMessage, startCapture } from '../src/audio.js';

test('microphone and playback exceptions use actionable messages without raw errors', () => {
  assert.match(recordingMessage({ name: 'NotAllowedError', message: 'SECRET' }), /권한/);
  assert.match(recordingMessage({ name: 'NotFoundError', message: 'SECRET' }), /마이크를 연결/);
  assert.match(recordingMessage({ name: 'NotReadableError', message: 'SECRET' }), /다른 앱/);
  assert.match(playbackMessage({ name: 'NotAllowedError', message: 'SECRET' }), /재생 버튼/);
  assert.match(playbackMessage({ name: 'EncodingError', message: 'SECRET' }), /자막/);
  assert.doesNotMatch(recordingMessage(new Error('SECRET raw error')), /SECRET/);
  assert.doesNotMatch(playbackMessage(new Error('SECRET raw error')), /SECRET/);
});

test('new playback and microphone capture stop the previous reply', async () => {
  let stops = 0, ended = 0, tracksStopped = 0;
  globalThis.window = {};
  globalThis.AudioContext = class {
    state = 'running'; destination = {};
    decodeAudioData() { return Promise.resolve({}); }
    createBufferSource() { return { connect() {}, start() {}, stop() { stops++; this.onended?.(); } }; }
  };
  globalThis.fetch = async () => new Response(new ArrayBuffer(16));
  await playAudio('/one', () => ended++);
  await playAudio('/two');
  assert.equal(stops, 1);
  assert.equal(ended, 1);
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() { tracksStopped++; } }] }) } } });
  globalThis.MediaRecorder = class {
    static isTypeSupported() { return true; }
    state = 'inactive';
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.onstop?.(); }
  };
  window.MediaRecorder = MediaRecorder;
  const recording = await startCapture(() => {});
  assert.equal(stops, 2);
  recording.cancel();
  assert.equal(tracksStopped, 2);
});
