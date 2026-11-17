import { test, after, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'HTMLElement', 'HTMLTextAreaElement', 'Event', 'MouseEvent', 'KeyboardEvent', 'sessionStorage', 'location', 'history']) globalThis[key] = dom.window[key];
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
HTMLElement.prototype.scrollIntoView = () => {};
const React = await import('react');
const { createRoot } = await import('react-dom/client');
const vite = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom', logLevel: 'error' });
const { default: App, Conversation, Composer, AudioReply } = await vite.ssrLoadModule('/src/App.jsx');
const { pendingTurns } = await vite.ssrLoadModule('/src/api.js');
let root;
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const tick = () => new Promise(resolve => setTimeout(resolve, 15));
beforeEach(() => { sessionStorage.clear(); document.getElementById('root').replaceChildren(); root = createRoot(document.getElementById('root')); });
afterEach(async () => { await React.act(async () => root.unmount()); });
after(async () => { await vite.close(); dom.window.close(); });

test('older completed receipts do not reappear as pending bubbles outside the history window', () => {
  assert.deepEqual(pendingTurns([
    { id: 'old', status: 'completed', result: { user_message_id: 'outside-history' } },
    { id: 'new', status: 'processing', result: {} },
  ]).map(t => t.id), ['new']);
});

test('anonymous visitors can open the existing mock exam without registering', async () => {
  globalThis.fetch = async url => {
    if (url === '/api/auth/me') return response({ detail: '로그인 필요' }, 401);
    if (url === '/api/exam/session') return response({ detail: '시험 없음' }, 404);
    return response({ allowed: true, daily_limit: 1, global_limit: 1 });
  };
  await React.act(async () => { root.render(React.createElement(App)); await tick(); });
  const button = [...document.querySelectorAll('button')].find(b => b.textContent === '로그인 없이 모의고사 체험');
  assert.ok(button);
  await React.act(async () => { button.click(); await tick(); });
  assert.match(document.querySelector('.exam-intro').textContent, /11개 문항/);
  assert.equal(document.querySelector('#login-name'), null);
});

test('Enter during Korean composition does not submit', async () => {
  let sends = 0;
  await React.act(async () => root.render(React.createElement(Composer, {
    mode: 'text', setMode() {}, draft: '한글 조합', setDraft() {}, onSend() { sends++; }, disabled: false,
  })));
  await React.act(async () => document.querySelector('textarea').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true })));
  assert.equal(sends, 0);
  await React.act(async () => document.querySelector('textarea').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: false, bubbles: true, cancelable: true })));
  assert.equal(sends, 1);
});

test('only the selected input mode is mounted; switching keeps the text draft', async () => {
  function TestComposer() {
    const [draft, setDraft] = React.useState('내가 입력한 문장');
    const [mode, setMode] = React.useState('text');
    return React.createElement(Composer, { draft, setDraft, mode, setMode, onSend() {}, disabled: false });
  }
  await React.act(async () => root.render(React.createElement(TestComposer)));
  assert.equal(document.querySelector('textarea').value, '내가 입력한 문장');
  assert.equal(document.querySelector('[aria-label="녹음 시작"]'), null);
  await React.act(async () => [...document.querySelectorAll('.input-modes button')].find(b => b.textContent === '음성').click());
  assert.equal(document.querySelector('textarea'), null);
  assert.ok(document.querySelector('[aria-label="녹음 시작"]'));
  await React.act(async () => document.querySelector('.input-modes button').click());
  assert.equal(document.querySelector('textarea').value, '내가 입력한 문장');
});

test('outgoing text appears before network acceptance or translation; lost ack retries the same ID', async () => {
  const snapshot = { id: 'c1', title: '연습', messages: [], turns: [], ended_at: null };
  const puts = [];
  let rejectFirst;
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'PUT') {
      puts.push(url);
      if (puts.length === 1) return new Promise((_, reject) => { rejectFirst = reject; });
      return response({ id: url.split('/').at(-1), conversation_id: 'c1', status: 'queued', input_text: '즉시 보여야 하는 메시지', result: {} });
    }
    return response(snapshot);
  };
  sessionStorage.setItem('talky:draft:u1:c1', '즉시 보여야 하는 메시지');
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' }, onCreated() {}, onRefresh() {} })); await tick(); });
  await React.act(async () => document.querySelector('form.text-composer').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  assert.match(document.querySelector('.user-bubble').textContent, /즉시 보여야 하는 메시지/);
  assert.match(document.querySelector('.message-status').textContent, /보내는 중/);
  assert.equal(puts.length, 1);
  await React.act(async () => { rejectFirst(new TypeError('연결이 끊겼습니다.')); await tick(); });
  assert.match(document.querySelector('.user-bubble').textContent, /즉시 보여야 하는 메시지/);
  assert.equal(document.querySelector('textarea').value, '즉시 보여야 하는 메시지');
  await React.act(async () => { document.querySelector('.user-message .text-button').click(); await tick(); });
  assert.equal(puts.length, 2);
  assert.equal(puts[0], puts[1]);
});

test('failed voice message explains the cause and next action', async () => {
  const reason = '녹음에서 목소리를 인식하지 못했어요. 마이크를 확인하고 다시 녹음해 주세요.';
  globalThis.fetch = async () => response({ id: 'c1', title: '음성 연습', messages: [], turns: [{
    id: 'silent', conversation_id: 'c1', status: 'failed', input_text: '', result: {},
    has_input_audio: true, has_output_audio: false, error_code: 'speech_not_detected', error_message: reason,
  }] });
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' }, onCreated() {}, onRefresh() {} })); await tick(); });
  assert.match(document.querySelector('.user-message').textContent, /목소리를 인식하지 못했어요/);
  assert.match(document.querySelector('.user-message').textContent, /마이크를 확인하고 다시 녹음/);
  assert.doesNotMatch(document.querySelector('.user-message').textContent, /문장을 인식하고 있어요/);
});

test('a definitive input rejection shows its cause and allows a corrected submission', async () => {
  globalThis.fetch = async (_url, options = {}) => options.method === 'PUT'
    ? response({ detail: '음성 파일을 읽을 수 없어요. 다시 녹음해 주세요.' }, 400)
    : response({ id: 'c1', title: '입력 오류', messages: [], turns: [] });
  sessionStorage.setItem('talky:draft:u1:c1', '다시 고칠 메시지');
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' }, onCreated() {}, onRefresh() {} })); await tick(); });
  await React.act(async () => { document.querySelector('form.text-composer').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await tick(); });
  assert.equal(document.querySelector('.user-message .text-button'), null);
  assert.match(document.querySelector('.user-message').textContent, /음성 파일을 읽을 수 없어요/);
  assert.equal(document.querySelector('[aria-label="메시지 보내기"]').disabled, false);
  assert.equal(document.querySelector('textarea').value, '다시 고칠 메시지');
});

test('translation and assistant captions are collapsed and autoplay starts only once', async () => {
  let starts = 0, played = 0;
  globalThis.AudioContext = class {
    state = 'running'; destination = {};
    resume() { return Promise.resolve(); }
    decodeAudioData() { return Promise.resolve({}); }
    createBufferSource() { return { connect() {}, start() { starts++; }, stop() {} }; }
  };
  globalThis.fetch = async () => new Response(new ArrayBuffer(16));
  const turn = { id: 't1', status: 'completed', has_output_audio: true };
  await React.act(async () => { root.render(React.createElement(AudioReply, { turn, autoPlay: true, onPlayed() { played++; } })); await tick(); });
  assert.equal(starts, 1);
  assert.equal(played, 1);
  await React.act(async () => { root.render(React.createElement(AudioReply, { turn: { ...turn }, autoPlay: false, onPlayed() { played++; } })); await tick(); });
  assert.equal(starts, 1);
  await React.act(async () => document.querySelector('[aria-label="답변 음성 일시정지"]').click());
  assert.ok(document.querySelector('[aria-label="답변 음성 재생"]'));
  const snapshot = { id: 'c1', title: '연습', turns: [], messages: [
    { id: 'm1', role: 'user', content: '오늘 발표했어요.', english_expression: 'I gave a presentation today.' },
    { id: 'm2', role: 'assistant', content: 'How did it go?' },
  ] };
  globalThis.fetch = async () => response(snapshot);
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' }, onCreated() {}, onRefresh() {} })); await tick(); });
  const disclosures = [...document.querySelectorAll('.message-disclosure')];
  assert.equal(disclosures.length, 3);
  assert.ok(disclosures.every(detail => !detail.open));
  assert.match(disclosures[0].querySelector('summary').textContent, /영어로는/);
  assert.equal(disclosures[1].querySelector('summary').textContent, '자막 보기');
  assert.equal(disclosures[2].querySelector('summary').textContent, '한국어로 확인하기');
});

test('correction belongs to the user and Korean translation is loaded only when opened then reused', async () => {
  let translations = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (url.endsWith('/translation')) {
      assert.equal(options.method, 'POST'); translations++;
      return response({ translation: '박물관에서 무엇이 가장 좋았어요?' });
    }
    return response({ id: 'c1', title: '표현 연습', turns: [], messages: [
      { id: 'u1', role: 'user', content: 'I go museum yesterday.' },
      { id: 'a1', role: 'assistant', content: 'What did you like most at the museum?', better_expression: '**Try:** I went to the museum yesterday.\n\n*과거형을 사용해요.*' },
    ] });
  };
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'user' }, onCreated() {}, onRefresh() {} })); await tick(); });
  assert.equal(translations, 0);
  assert.match(document.querySelector('.user-message').textContent, /표현 다듬기/);
  assert.doesNotMatch(document.querySelector('.user-message').textContent, /\*\*Try/);
  assert.doesNotMatch(document.querySelector('.assistant-message').textContent, /표현 다듬기/);
  const details = [...document.querySelectorAll('details')].find(d => d.querySelector('summary')?.textContent === '한국어로 확인하기');
  await React.act(async () => { details.open = true; await tick(); });
  assert.equal(translations, 1);
  assert.match(details.textContent, /박물관에서 무엇이 가장 좋았어요/);
  await React.act(async () => { details.open = false; await tick(); details.open = true; await tick(); });
  assert.equal(translations, 1);
});

test('natural user messages have no correction disclosure', async () => {
  globalThis.fetch = async () => response({ id: 'c1', title: '자연스러운 표현', turns: [], messages: [
    { id: 'u1', role: 'user', content: 'I went to the museum yesterday.', better_expression: null },
    { id: 'a1', role: 'assistant', content: 'What did you see?', better_expression: '**Try:** I went to the museum yesterday.\n\n*자연스러운 문장입니다.*' },
  ] });
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'user' }, onCreated() {}, onRefresh() {} })); await tick(); });
  assert.doesNotMatch(document.querySelector('.user-message').textContent, /표현 다듬기/);
});

test('failed Korean translation keeps the English caption and can be retried', async () => {
  let attempts = 0;
  globalThis.fetch = async url => url.endsWith('/translation')
    ? ++attempts === 1 ? response({ detail: '잠시 후 다시 시도해 주세요.' }, 503) : response({ translation: '오늘은 어땠어요?' })
    : response({ id: 'c1', title: '번역 재시도', turns: [], messages: [{ id: 'a1', role: 'assistant', content: 'How was your day?' }] });
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'user' }, onCreated() {}, onRefresh() {} })); await tick(); });
  const details = [...document.querySelectorAll('details')].find(d => d.querySelector('summary')?.textContent === '한국어로 확인하기');
  await React.act(async () => { details.open = true; await tick(); });
  assert.match(document.querySelector('.assistant-message').textContent, /How was your day/);
  assert.match(details.textContent, /잠시 후 다시 시도/);
  await React.act(async () => { details.querySelector('button').click(); await tick(); });
  assert.match(details.textContent, /오늘은 어땠어요/);
  assert.equal(attempts, 2);
});

test('autoplay is acknowledged only after playback starts and a failed attempt can be retried', async () => {
  let played = 0, accept;
  globalThis.fetch = () => new Promise(resolve => { accept = resolve; });
  const turn = { id: 'delayed-audio', status: 'completed', has_output_audio: true };
  await React.act(async () => { root.render(React.createElement(AudioReply, { turn, autoPlay: true, onPlayed() { played++; } })); await tick(); });
  assert.equal(played, 0);
  await React.act(async () => { accept(new Response(null, { status: 503 })); await tick(); });
  assert.equal(played, 0);
  assert.match(document.querySelector('.audio-error').textContent, /음성을 불러올 수 없/);
  globalThis.fetch = async () => new Response(new ArrayBuffer(16));
  await React.act(async () => { document.querySelector('[aria-label="답변 음성 재생"]').click(); await tick(); });
  assert.equal(played, 1);
  assert.ok(document.querySelector('[aria-label="답변 음성 일시정지"]'));
});

test('a newly generated conversation reply plays without clicking the play button', async () => {
  let rid, audioRequests = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (url.endsWith('/audio')) { audioRequests++; return new Response(new ArrayBuffer(16)); }
    if (options.method === 'PUT') {
      rid = url.split('/').at(-1);
      return response({ id: rid, conversation_id: 'c1', status: 'queued', input_text: '자동으로 답변을 듣고 싶어요.', result: {} });
    }
    return response({ id: 'c1', title: '자동 재생', messages: rid ? [
      { id: 'user-new', role: 'user', content: '자동으로 답변을 듣고 싶어요.' },
      { id: 'assistant-new', role: 'assistant', content: 'Let’s practice together.' },
    ] : [], turns: rid ? [{ id: rid, conversation_id: 'c1', status: 'completed', has_output_audio: true,
      result: { user_message_id: 'user-new', assistant_message_id: 'assistant-new' } }] : [] });
  };
  sessionStorage.setItem('talky:draft:u1:c1', '자동으로 답변을 듣고 싶어요.');
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' }, onCreated() {}, onRefresh() {} })); await tick(); });
  await React.act(async () => { document.querySelector('form.text-composer').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await tick(); });
  await React.act(async () => { await new Promise(resolve => setTimeout(resolve, 1600)); });
  await React.act(async () => { await tick(); });
  assert.equal(audioRequests, 1);
  assert.ok(document.querySelector('[aria-label="답변 음성 일시정지"]'));
});

test('mode cannot change during recording and tracks are released on unmount', async () => {
  let released = 0;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() { released++; } }] }) } } });
  globalThis.MediaRecorder = class {
    static isTypeSupported() { return true; }
    state = 'inactive'; mimeType = 'audio/webm';
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.onstop?.(); }
  };
  window.MediaRecorder = globalThis.MediaRecorder;
  function Recorder() {
    const [mode, setMode] = React.useState('voice');
    return React.createElement(Composer, { mode, setMode, draft: '', setDraft() {}, onSend() {}, disabled: false });
  }
  await React.act(async () => root.render(React.createElement(Recorder)));
  await React.act(async () => { document.querySelector('[aria-label="녹음 시작"]').click(); await tick(); });
  assert.ok(document.querySelector('[aria-label="녹음 중지"]'));
  assert.ok([...document.querySelectorAll('.input-modes button')].every(button => button.disabled));
  await React.act(async () => root.render(null));
  assert.ok(released >= 1);
});
