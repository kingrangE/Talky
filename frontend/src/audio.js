let context;
let activeSource;
class RecordingError extends Error {}
class PlaybackError extends Error {}

export function recordingMessage(error) {
  if (error instanceof RecordingError) return error.message;
  const messages = {
    NotAllowedError: '마이크 권한이 필요해요. 주소창에서 마이크를 허용한 뒤 다시 녹음해 주세요.',
    SecurityError: '브라우저가 마이크 접근을 막았어요. HTTPS 연결과 마이크 권한을 확인해 주세요.',
    NotFoundError: '연결된 마이크를 찾지 못했어요. 마이크를 연결한 뒤 다시 눌러 주세요.',
    NotReadableError: '마이크를 사용할 수 없어요. 다른 앱에서 사용 중인지 확인하고 다시 녹음해 주세요.',
    OverconstrainedError: '이 마이크는 요청한 녹음 설정을 지원하지 않아요. 다른 마이크나 브라우저로 시도해 주세요.',
    NotSupportedError: '이 브라우저는 녹음 형식을 지원하지 않아요. 최신 Chrome·Safari·Edge로 시도해 주세요.',
    AbortError: '녹음 장치 연결이 중단됐어요. 마이크를 확인하고 다시 녹음해 주세요.',
  };
  return messages[error.name] || '녹음을 완료하지 못했어요. 마이크와 브라우저 연결을 확인한 뒤 다시 녹음해 주세요.';
}

export function playbackMessage(error) {
  if (error instanceof PlaybackError) return error.message;
  if (error.name === 'NotAllowedError') return '브라우저가 자동 재생을 막았어요. 재생 버튼을 눌러 들어 주세요.';
  if (error.name === 'EncodingError') return '음성 파일을 재생할 수 없어요. 자막 보기로 답변을 확인해 주세요.';
  return '음성을 재생하지 못했어요. 재생 버튼으로 다시 시도하거나 자막 보기로 답변을 확인해 주세요.';
}

export function stopPlayback() {
  if (activeSource) { try { activeSource.stop(); } catch {} activeSource = null; }
}

export function setPlayback(source) {
  stopPlayback();
  activeSource = source;
  return () => { if (activeSource === source) activeSource = null; };
}

export async function unlockAudio() {
  const Audio = globalThis.AudioContext || window.webkitAudioContext;
  if (!Audio) throw new PlaybackError('이 브라우저에서 음성을 재생할 수 없습니다. 자막으로 확인해 주세요.');
  if (!context || context.state === 'closed') context = new Audio();
  if (context.state === 'running') return Promise.resolve(context);
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new PlaybackError('음성을 재생하려면 재생 버튼을 눌러 주세요.')), 1500);
    context.resume().then(() => { clearTimeout(timeout); resolve(context); }, error => { clearTimeout(timeout); reject(error); });
  });
}

export async function startCapture(onLimit, seconds = 90) {
  stopPlayback();
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    throw new RecordingError('이 브라우저에서는 녹음할 수 없습니다. HTTPS 또는 localhost에서 Chrome·Safari·Edge를 사용해 주세요.');
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  let recorder;
  try {
    const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(type => MediaRecorder.isTypeSupported(type));
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  } catch (error) {
    stream.getTracks().forEach(track => track.stop());
    throw error;
  }
  const chunks = [];
  let size = 0;
  recorder.ondataavailable = event => {
    chunks.push(event.data);
    size += event.data.size;
    if (size > 5000000 && recorder.state === 'recording') { recorder.stop(); onLimit(); }
  };
  let resolve, reject;
  const recording = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  recording.catch(() => {});
  let timer;
  const release = () => { clearTimeout(timer); stream.getTracks().forEach(track => track.stop()); };
  recorder.onerror = () => { release(); reject(new RecordingError('녹음 장치 연결이 끊겼습니다. 다시 녹음해 주세요.')); };
  recorder.onstop = () => {
    release();
    if (size > 5000000) reject(new RecordingError('녹음 파일이 너무 큽니다. 짧게 나누어 말씀해 주세요.'));
    else resolve(new Blob(chunks, { type: recorder.mimeType }));
  };
  recorder.start(250);
  timer = setTimeout(() => { if (recorder.state === 'recording') { recorder.stop(); onLimit(); } }, seconds * 1000);
  return {
    stop: () => { if (recorder.state === 'recording') recorder.stop(); return recording; },
    cancel: () => { recording.catch(() => {}); if (recorder.state === 'recording') recorder.stop(); release(); },
  };
}

export async function playAudio(url, onEnded = () => {}) {
  const ctx = await unlockAudio();
  if (ctx.state !== 'running') throw new PlaybackError('음성을 재생하려면 재생 버튼을 눌러 주세요.');
  const response = await fetch(url, { credentials: 'same-origin' });
  if (!response.ok) throw new PlaybackError('음성을 불러올 수 없거나 보관 기간이 지났습니다. 자막으로 답변을 확인해 주세요.');
  const buffer = await ctx.decodeAudioData(await response.arrayBuffer());
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  source.onended = () => { if (activeSource === source) activeSource = null; onEnded(); };
  stopPlayback();
  activeSource = source;
  source.start();
  return () => { if (activeSource === source) activeSource = null; source.onended = null; try { source.stop(); } catch {} };
}
