import { playbackMessage, setPlayback, unlockAudio } from './audio.js';

class PcmError extends Error {}
const rate = 24000;

export function streamPcm(url, options, { onStarted, onEnded, onError, onMetrics, onComplete } = {}) {
  const controller = new AbortController();
  const sources = new Set();
  let stopped = false, ended = false, downloaded = false, timer;
  const started = performance.now();
  const seconds = () => (performance.now() - started) / 1000;
  function finish() {
    if (ended) return;
    ended = true; release(); onEnded?.();
  }
  function stop() {
    if (stopped) return;
    stopped = true; controller.abort(); clearTimeout(timer);
    for (const source of sources) { source.onended = null; try { source.stop(); } catch {} }
    sources.clear(); finish();
  }
  const release = setPlayback({ stop });
  const done = (async () => {
    const ctx = await unlockAudio();
    if (stopped) return;
    if (ctx.state !== 'running') throw new PcmError('음성을 재생하려면 재생 버튼을 다시 눌러 주세요.');
    const response = await fetch(url, { credentials: 'same-origin', ...options, signal: controller.signal });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new PcmError(error.detail || '음성을 생성하지 못했어요. 잠시 후 다시 시도해 주세요.');
    }
    if (!response.headers.get('content-type')?.startsWith('audio/pcm') || !response.body) throw new PcmError('올바른 음성 데이터를 받지 못했어요.');
    const reader = response.body.getReader();
    const saved = [];
    let total = 0, tail = new Uint8Array(), next = ctx.currentTime + .03, first = true, received = false;
    let scheduled = false, scheduling = Promise.resolve(), schedulingError;
    async function schedule(value) {
        const data = new Uint8Array(tail.length + value.length);
        data.set(tail); data.set(value, tail.length);
        const size = data.length - data.length % 2;
        tail = data.slice(size);
        for (let offset = 0; offset < size; offset += 4800) {
          while (!stopped && next - ctx.currentTime > 3) await new Promise(resolve => setTimeout(resolve, 25));
          if (stopped) return;
          const count = Math.min(4800, size - offset) / 2;
          const buffer = ctx.createBuffer(1, count, rate);
          const floats = buffer.getChannelData(0);
          const view = new DataView(data.buffer, data.byteOffset + offset, count * 2);
          for (let index = 0; index < count; index++) floats[index] = view.getInt16(index * 2, true) / 32768;
          const source = ctx.createBufferSource();
          source.buffer = buffer; source.connect(ctx.destination); sources.add(source);
          source.onended = () => { sources.delete(source); if (downloaded && scheduled && !sources.size) finish(); };
          next = Math.max(next, ctx.currentTime + .03);
          source.start(next);
          if (first) {
            first = false;
            const startAt = next;
            const observeStart = () => {
              if (stopped) return;
              if (ctx.currentTime < startAt) { timer = setTimeout(observeStart, 10); return; }
              onMetrics?.({ firstPlayback: seconds() }); onStarted?.();
            };
            timer = setTimeout(observeStart, 10);
          }
          next += count / rate;
        }
    }
    try {
      while (!stopped) {
        const { value, done } = await reader.read();
        if (done) break;
        if (!value.length) continue;
        if (!received) { received = true; onMetrics?.({ firstChunk: seconds() }); }
        total += value.length;
        if (total > rate * 2 * 120) throw new PcmError('음성이 너무 길어 재생을 중단했어요. 문장을 줄여 주세요.');
        const packet = value.slice();
        saved.push(packet);
        scheduling = scheduling.then(() => schedule(packet)).catch(error => { schedulingError = error; controller.abort(); });
      }
      if (stopped) return;
      if (!total || total % 2) throw new PcmError('음성이 올바르게 완료되지 않았어요. 다시 시도해 주세요.');
      downloaded = true;
      onMetrics?.({ fullDownload: seconds(), audioSeconds: total / 2 / rate });
      onComplete?.(new Blob(saved, { type: 'audio/pcm' }));
      await scheduling;
      scheduled = true;
      if (schedulingError) throw schedulingError;
      if (!sources.size) finish();
    } finally {
      if (!downloaded) await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  })().catch(error => {
    if (!stopped) { onError?.(error instanceof PcmError ? error.message : playbackMessage(error)); stop(); }
  });
  return { stop, done };
}
