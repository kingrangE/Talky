import { test } from 'node:test';
import assert from 'node:assert/strict';
import { streamPcm } from '../src/pcm.js';
import { stopPlayback } from '../src/audio.js';

const sources = [];
let clock = 0;
globalThis.window = {};
globalThis.AudioContext = class {
  state = 'running'; destination = {};
  get currentTime() { return clock; }
  createBuffer(_channels, count) { return { values: new Float32Array(count), getChannelData() { return this.values; } }; }
  createBufferSource() {
    const source = { connect() {}, start(at) { this.at = at; sources.push(this); }, stop() { this.stopped = true; } };
    return source;
  }
};
const tick = () => new Promise(resolve => setTimeout(resolve, 20));

test('PCM starts before EOF, keeps split samples in order, and caches only complete audio', async () => {
  let network, complete = false, started = false;
  const metrics = [];
  globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) { network = controller; } }), { headers: { 'content-type': 'audio/pcm' } });
  const task = streamPcm('/preview', {}, { onStarted() { started = true; }, onComplete() { complete = true; }, onMetrics(value) { metrics.push(value); }, onError(error) { assert.fail(error); } });
  await tick();
  network.enqueue(new Uint8Array([0]));
  network.enqueue(new Uint8Array([128, 255, 127]));
  await tick();
  assert.equal(sources.length, 1);
  assert.deepEqual([...sources[0].buffer.values], [-1, 32767/32768]);
  assert.equal(complete, false);
  clock = .05; await tick();
  assert.equal(started, true);
  assert.equal(metrics.filter(value => 'firstChunk' in value).length, 1);
  network.enqueue(new Uint8Array([0, 0])); network.close();
  await task.done;
  assert.equal(complete, true);
  assert.equal(sources.length, 2);
  assert.ok(sources[1].at >= sources[0].at);
  stopPlayback();
});

test('stop cancels fetch and queued sources; an incomplete response is never cached', async () => {
  let network, signal, cached = false, ended = 0, cancelled = false;
  globalThis.fetch = async (_url, options) => {
    signal = options.signal;
    return new Response(new ReadableStream({ start(controller) { network = controller; }, cancel() { cancelled = true; } }), { headers: { 'content-type': 'audio/pcm' } });
  };
  const task = streamPcm('/preview', {}, { onComplete() { cached = true; }, onEnded() { ended++; }, onError(error) { assert.fail(error); } });
  await tick(); network.enqueue(new Uint8Array([0,0])); await tick();
  const source = sources.at(-1);
  stopPlayback();
  // A mock stream does not implement fetch's AbortSignal handling; release its pending read.
  network.close();
  await task.done;
  assert.equal(signal.aborted, true);
  assert.equal(source.stopped, true);
  assert.equal(cached, false);
  assert.equal(ended, 1);
  assert.equal(cancelled, false);
});
