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
const { default: App, Conversation, Composer, AudioReply, KoreanReply } = await vite.ssrLoadModule('/src/App.jsx');
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
    { id: 'opening', status: 'completed', starter_topic: 'travel', result: { assistant_message_id: 'opening-message', user_message_id: null } },
  ]).map(t => t.id), ['new']);
});

test('selecting a topic starts with an assistant message and leaves manual input available initially', async () => {
  let rid, topic;
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'PUT') {
      assert.ok(url.endsWith('/starter'));
      rid = url.split('/').at(-2); topic = JSON.parse(options.body).topic;
      return response({ id: rid, conversation_id: 'c1', status: 'queued', starter_topic: topic, result: {} });
    }
    if (url === `/api/turns/${rid}`) return response({ id: rid, conversation_id: 'c1', status: 'completed', starter_topic: topic,
      result: { assistant_message_id: 'opening' } });
    return response({ id: 'c1', title: '주제 대화', messages: rid ? [{ id: 'opening', role: 'assistant', content: 'Where would you like to go next?' }] : [],
      turns: rid ? [{ id: rid, conversation_id: 'c1', status: 'completed', starter_topic: topic,
        result: { user_message_id: null, assistant_message_id: 'opening' } }] : [] });
  };
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' }, onCreated() {}, onRefresh() {} })); await tick(); });
  assert.ok(document.querySelector('textarea'));
  const button = [...document.querySelectorAll('.starter-prompts button')].find(b => b.textContent.includes('여행'));
  await React.act(async () => { button.click(); await tick(); });
  assert.equal(topic, 'travel');
  assert.equal(document.querySelector('.user-message'), null);
  assert.match(document.querySelector('.assistant-message').textContent, /첫 질문/);
  await React.act(async () => { await new Promise(resolve => setTimeout(resolve, 1600)); });
  await React.act(async () => { await tick(); });
  assert.equal(document.querySelectorAll('.assistant-message').length, 1);
  assert.equal(document.querySelector('.user-message'), null);
  assert.match(document.querySelector('.assistant-message').textContent, /한국어로 확인하기/);
});

test('a stale screen receives an update notice without an automatic reload', async () => {
  const script = document.createElement('script'); script.type = 'module'; script.src = '/assets/index-previous.js'; document.head.append(script);
  globalThis.fetch = async () => new Response(JSON.stringify({ id: 'c1', title: '업데이트', messages: [], turns: [] }),
    { headers: { 'Content-Type': 'application/json', 'X-Talky-Web-Build': 'index-new.js' } });
  try {
    await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' }, onCreated() {}, onRefresh() {} })); await tick(); });
    assert.match(document.querySelector('.conversation-view').textContent, /새 기능이 업데이트/);
    assert.ok([...document.querySelectorAll('button')].some(b => b.textContent === '최신 화면 불러오기'));
  } finally { script.remove(); }
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

test('idle conversations reconcile other-tab changes on a 15s timer, focus and visibility', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  const advance = ms => React.act(async () => t.mock.timers.tick(ms));
  const calls = [];
  const snapshot = { id: 'c1', title: '첫 제목', messages: [], turns: [] };
  globalThis.fetch = async url => { calls.push(url); return response(snapshot); };
  await React.act(async () => root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' } })));
  for (let i = 0; i < 3; i++) {
    await advance(14999);
    assert.equal(calls.length, i + 1);
    await advance(1);
  }
  assert.deepEqual(calls, Array(4).fill('/api/conversations/c1'));
  snapshot.title = '다른 탭에서 바꾼 제목';
  window.dispatchEvent(new Event('focus'));
  await advance(0);
  assert.equal(calls.length, 5);
  assert.equal(document.querySelector('h1').textContent, snapshot.title);

  snapshot.messages = [{ id: 'tab-message', role: 'assistant', content: 'A reply from another tab.' }];
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  try {
    document.dispatchEvent(new Event('visibilitychange'));
    await advance(0);
  } finally {
    delete document.hidden;
  }
  assert.equal(calls.length, 6);
  assert.match(document.querySelector('.assistant-message').textContent, /A reply from another tab/);
});

test('voice polls only the turn at 400ms until transcription and reconciles later stages', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  const advance = ms => React.act(async () => t.mock.timers.tick(ms));
  const detailPath = '/api/conversations/c1', turnPath = '/api/turns/voice';
  const calls = [];
  let turn = { id: 'voice', conversation_id: 'c1', status: 'processing', has_input_audio: true, input_text: null, result: {} };
  const snapshot = { id: 'c1', title: '새로운 대화', messages: [] };
  globalThis.fetch = async url => {
    calls.push(url);
    return response(url === turnPath ? turn : { ...snapshot, turns: [turn] });
  };
  await React.act(async () => root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' } })));
  for (let i = 0; i < 4; i++) await advance(400);
  assert.deepEqual(calls, [detailPath, ...Array(4).fill(turnPath)]);
  assert.match(document.querySelector('.user-bubble').textContent, /인식하고 있어요/);
  turn = { ...turn, input_text: 'I heard my transcript.' };
  await advance(399);
  assert.equal(calls.length, 5);
  await advance(1);
  assert.equal(calls.at(-1), turnPath);
  assert.match(document.querySelector('.user-bubble').textContent, /I heard my transcript/);
  assert.equal(document.querySelector('.assistant-message'), null);

  turn = { ...turn, result: { user_message_id: 'u', assistant_message_id: 'a' } };
  snapshot.messages = [
    { id: 'u', role: 'user', content: turn.input_text },
    { id: 'a', role: 'assistant', content: 'Here is my reply.' },
  ];
  await advance(1500);
  assert.deepEqual(calls.slice(-2), [turnPath, detailPath]);
  assert.match(document.querySelector('.assistant-message').textContent, /Here is my reply/);

  snapshot.messages[0].better_expression = 'I heard the transcript.';
  snapshot.messages[1].korean_translation = '여기 내 답변이야.';
  snapshot.title = '자동으로 붙은 제목';
  turn = { ...turn, has_output_audio: true };
  await advance(1500);
  assert.equal(calls.at(-1), detailPath);
  assert.equal(document.querySelector('h1').textContent, snapshot.title);
  assert.match(document.querySelector('.expression-feedback').textContent, /I heard the transcript/);
  const translation = [...document.querySelectorAll('.assistant-message details')].find(d => d.querySelector('summary').textContent === '한국어로 확인하기');
  await React.act(async () => { translation.open = true; translation.dispatchEvent(new Event('toggle')); });
  assert.match(translation.textContent, /여기 내 답변이야/);
  assert.ok(document.querySelector('[aria-label="답변 음성 재생"]'));

  turn = { ...turn, status: 'completed' };
  await advance(1500);
  assert.equal(calls.at(-1), detailPath);
  await advance(14999);
  assert.equal(calls.length, 10);
});

test('a missing receipt stays retryable and reconnect reconciles it without a second submission', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  const advance = ms => React.act(async () => t.mock.timers.tick(ms));
  const calls = [];
  let rid;
  let turn;
  let checks = 0;
  globalThis.fetch = async (url, options = {}) => {
    calls.push([options.method || 'GET', url]);
    if (options.method === 'PUT') {
      rid = url.split('/').at(-1);
      throw new TypeError('connection lost after submission');
    }
    if (url === `/api/turns/${rid}`) {
      if (++checks === 1) throw new TypeError('still offline');
      return turn ? response(turn) : response({}, 404);
    }
    return response({ id: 'c1', title: '연습', messages: [], turns: turn ? [turn] : [] });
  };
  sessionStorage.setItem('talky:draft:u1:c1', 'I just sent this.');
  await React.act(async () => root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' } })));
  await React.act(async () => document.querySelector('form.text-composer').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  await advance(1500);
  assert.match(document.querySelector('[role="alert"]').textContent, /서버에 연결하지 못했어요/);
  await advance(1500);
  assert.deepEqual(calls.at(-1), ['GET', `/api/turns/${rid}`]);
  assert.match(document.querySelector('.message-status').textContent, /접수 여부/);
  assert.ok(document.querySelector('.user-message .text-button'));
  turn = { id: rid, conversation_id: 'c1', status: 'failed', input_text: 'I just sent this.', result: {}, error_message: '시간이 지나 응답하지 못했어요.' };
  window.dispatchEvent(new Event('online'));
  await advance(0);
  assert.deepEqual(calls.at(-1), ['GET', '/api/conversations/c1']);
  assert.equal(calls.filter(([method]) => method === 'PUT').length, 1);
  assert.equal(document.querySelectorAll('.user-message').length, 1);
  assert.equal(document.querySelector('.user-message .text-button'), null);
  assert.match(document.querySelector('.message-status').textContent, /시간이 지나/);
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

test('correction belongs to the user and missing Korean translation loads when opened then is reused', async () => {
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

test('expression feedback shows a bold rewrite then translation and useful expressions as bullets', async () => {
  const suggestion = "I usually can't get up early, so I end up getting up after 12.";
  const translation = '저는 보통 일찍 일어나지 못해서 결국 12시가 지나서 일어나게 돼요.';
  const expression = 'end up ~ing: 결국 ~하게 되다.';
  globalThis.fetch = async () => response({ id: 'c1', title: '표현', messages: [
    { id: 'u1', role: 'user', content: 'I wake late after 12.', better_expression: `**${suggestion}**\n\n- ${translation}\n- ${expression}` },
  ], turns: [] });
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' }, onCreated() {}, onRefresh() {} })); await tick(); });
  const feedback = document.querySelector('.expression-feedback');
  assert.equal(feedback.querySelector('p strong').textContent, suggestion);
  assert.deepEqual([...feedback.querySelectorAll('ul li')].map(item => item.textContent), [translation, expression]);
  assert.ok(feedback.querySelector('p').compareDocumentPosition(feedback.querySelector('ul')) & window.Node.DOCUMENT_POSITION_FOLLOWING);
  assert.doesNotMatch(feedback.textContent, /\*\*|Try:/);
});

test('stored inline feedback separates translation and each expression into real bullets', async () => {
  const suggestion = "The most memorable place I've visited is Fukuoka, Japan. I went there with my ex-girlfriend about three times, and I really loved her, so I miss those times.";
  const items = [
    '가장 기억에 남는 방문지는 일본 후쿠오카예요. 전 여자친구랑 세 번쯤 갔었고, 그녀를 정말 사랑해서 그 시간들이 그리워요.',
    "most memorable place I've visited: 'My Memorable Visit Place'보다 훨씬 자연스러운 표현이에요.",
    "I went there: 'I have been traveled'는 문법 오류(수동태 오용)로, 단순 과거 'went'가 맞아요.",
    "about three times: '세 번쯤'이라는 횟수를 자연스럽게 나타내요.",
    "miss those times: 'miss that experience'보다 '그 시간들/그때가 그리워요'라는 뉘앙스로 더 자주 써요.",
  ];
  globalThis.fetch = async () => response({ id: 'c1', title: '표현', messages: [
    { id: 'u1', role: 'user', content: 'My Memorable Visit Place is Fukuoka.', better_expression: `**${suggestion}**\n\n${items.join(' - ')}` },
  ], turns: [] });
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'u1' }, onCreated() {}, onRefresh() {} })); await tick(); });
  const feedback = document.querySelector('.expression-feedback');
  assert.equal(feedback.querySelector('strong').textContent, suggestion);
  assert.deepEqual([...feedback.querySelectorAll('li')].map(item => item.textContent), items);
});

test('present perfect continuous explanation appears below the translation', async () => {
  const suggestion = "I've been getting up around 11 lately and studying my major.";
  const translation = '저는 요즘 11시쯤 일어나서 전공 공부를 하고 있어요.';
  const grammar = 'have/has been ~ing: 현재완료진행형으로 최근부터 지금까지 이어지거나 반복되는 행동을 말해요. 여기서는 요즘의 생활 패턴이 이어진다는 뜻이에요.';
  globalThis.fetch = async () => response({ id:'c1', title:'표현', turns:[], messages:[
    {id:'u1', role:'user', content:'These days I get up at about 11 and study for my major.', better_expression:
      `**${suggestion}**\n\n- ${translation}\n- ${grammar}`},
  ] });
  await React.act(async () => { root.render(React.createElement(Conversation, {cid:'c1', user:{id:'u1'}, onCreated(){}, onRefresh(){}})); await tick(); });
  const feedback = document.querySelector('.expression-feedback');
  assert.equal(feedback.querySelector('strong').textContent, suggestion);
  assert.deepEqual([...feedback.querySelectorAll('li')].map(item=>item.textContent),[translation,grammar]);
});

test('natural user messages have no correction disclosure', async () => {
  globalThis.fetch = async () => response({ id: 'c1', title: '자연스러운 표현', turns: [], messages: [
    { id: 'u1', role: 'user', content: 'I went to the museum yesterday.', better_expression: null },
    { id: 'a1', role: 'assistant', content: 'What did you see?', better_expression: '**Try:** I went to the museum yesterday.\n\n*자연스러운 문장입니다.*' },
  ] });
  await React.act(async () => { root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'user' }, onCreated() {}, onRefresh() {} })); await tick(); });
  assert.doesNotMatch(document.querySelector('.user-message').textContent, /표현 다듬기/);
});

test('the English expression hides a repeated transcript and shows a real rewrite', async () => {
  globalThis.fetch = async () => response({ id: 'c1', title: '일상 대화', turns: [], messages: [
    { id: 'u1', role: 'user', content: "I feel tired and I'm not happy.", english_expression: " I feel tired and I'm not happy. " },
    { id: 'a1', role: 'assistant', content: 'Oh no. What happened?' },
    { id: 'u2', role: 'user', content: '일이 너무 많았어요.', english_expression: 'I had too much work.' },
    { id: 'u3', role: 'user', content: '라면을 좋아해요.', english_expression: 'I like ramen.',
      better_expression: '**I like ramen.**\n\n- 저는 라면을 좋아해요.' },
    { id: 'u4', role: 'user', content: '요즘 11시쯤 일어나요.', english_expression: "I've been getting up around 11 lately.",
      better_expression: "**I've been getting up around 11 lately.**\n\n- 요즘 11시쯤 일어나요.\n- have been ~ing: 요즘 반복되는 일을 나타내요." },
  ] });
  await React.act(async () => {
    root.render(React.createElement(Conversation, { cid: 'c1', user: { id: 'user' }, onCreated() {}, onRefresh() {} }));
    await tick();
  });
  const [repeated, rewritten, simple, withGrammar] = document.querySelectorAll('.user-message');
  assert.equal(repeated.querySelector('details'), null);
  assert.equal(rewritten.querySelector('details p').textContent, 'I had too much work.');
  assert.equal(simple.querySelectorAll('details').length, 1);
  assert.equal(simple.querySelector('summary').textContent, '영어로는 이렇게 말해요');
  assert.equal(simple.querySelector('strong').textContent, 'I like ramen.');
  assert.deepEqual([...simple.querySelectorAll('li')].map(item => item.textContent), ['저는 라면을 좋아해요.']);
  assert.deepEqual([...withGrammar.querySelectorAll('li')].map(item => item.textContent), [
    '요즘 11시쯤 일어나요.', 'have been ~ing: 요즘 반복되는 일을 나타내요.',
  ]);
});

test('opening during generation uses the polled translation without a duplicate request', async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return response({ translation: 'unexpected' }); };
  const message = { id: 'a1', content: 'Hello!' };
  await React.act(async () => { root.render(React.createElement(KoreanReply, { message, generating: true })); await tick(); });
  const details = document.querySelector('details');
  await React.act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')); await tick(); });
  assert.match(details.textContent, /한국어로 옮기고 있어요/);
  assert.equal(calls, 0);
  await React.act(async () => { root.render(React.createElement(KoreanReply, { message: { ...message, korean_translation: '안녕하세요!' }, generating: true })); await tick(); });
  assert.match(details.textContent, /안녕하세요!/);
  assert.doesNotMatch(details.textContent, /한국어로 옮기고 있어요/);
  assert.equal(calls, 0);
});

test('missing background translation falls back after generation finishes', async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return response({ translation: '안녕하세요!' }); };
  const message = { id: 'a1', content: 'Hello!' };
  await React.act(async () => { root.render(React.createElement(KoreanReply, { message, generating: true })); await tick(); });
  await React.act(async () => { document.querySelector('details').open = true; document.querySelector('details').dispatchEvent(new Event('toggle')); await tick(); });
  await React.act(async () => { root.render(React.createElement(KoreanReply, { message, generating: false })); await tick(); });
  assert.equal(calls, 1);
  assert.match(document.querySelector('details').textContent, /안녕하세요!/);
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
    if (url === `/api/turns/${rid}`) return response({ id: rid, conversation_id: 'c1', status: 'completed', has_output_audio: true,
      result: { user_message_id: 'user-new', assistant_message_id: 'assistant-new' } });
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

test('automatic title reaches the sidebar and a manual rename updates it without reopening', async () => {
  history.replaceState({}, '', '/?c=c1');
  let serverTitle = null;
  let patch;
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/auth/me') return response({ id: 'u1', name: 'Learner' });
    if (url.startsWith('/api/conversations?')) return response([{ id: 'c1', title: null }]);
    if (options.method === 'PATCH') {
      patch = JSON.parse(options.body);
      serverTitle = patch.title;
      return response({ ok: true });
    }
    return response({ id: 'c1', title: serverTitle, messages: [], turns: [] });
  };
  await React.act(async () => { root.render(React.createElement(App)); await tick(); });
  await React.act(async () => { await tick(); });
  assert.equal(document.querySelector('.history-list button span').textContent, '새로운 대화');
  serverTitle = '파스타 만들기';
  await React.act(async () => { window.dispatchEvent(new Event('focus')); await tick(); });
  assert.equal(document.querySelector('.history-list button span').textContent, '파스타 만들기');
  await React.act(async () => { document.querySelector('.conversation-title button').click(); });
  const input = document.getElementById('conversation-title');
  await React.act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, '내 파스타 연습');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await React.act(async () => {
    document.querySelector('.conversation-title-editor').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await tick();
  });
  assert.deepEqual(patch, { title: '내 파스타 연습' });
  assert.equal(document.querySelector('.conversation-title h1').textContent, '내 파스타 연습');
  assert.equal(document.querySelector('.history-list button span').textContent, '내 파스타 연습');
});
