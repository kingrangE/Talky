import React, { useEffect, useRef, useState } from 'react';
import { api, json, mergeTurns, pendingTurns } from './api.js';
import { playAudio, playbackMessage, recordingMessage, startCapture, stopPlayback, unlockAudio } from './audio.js';
import { Icon, Mark } from './icons.jsx';
import { Exam } from './Exam.jsx';

export function Notice({ children }) {
  return children ? <div className="notice" role="alert">{children}</div> : null;
}

function ExpressionFeedback({ value, original }) {
  if (!value) return null;
  const parts = value.match(/^\*\*Try:\*\* ([\s\S]*?)\n\n\*([\s\S]*)\*$/);
  const normalize = text => text.trim().replace(/[.!?]+$/, '').trim();
  if (parts && normalize(parts[1]) === normalize(original)) return null;
  return <details className="message-disclosure"><summary>표현 다듬기</summary>
    {parts ? <><p lang="en">{parts[1]}</p><p lang="ko">{parts[2]}</p></> : <p>{value}</p>}
  </details>;
}

export function KoreanReply({ message }) {
  const [translation, setTranslation] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const value = translation || message.korean_translation;
  async function load() {
    if (value || pending.current) return;
    pending.current = true; setLoading(true); setError('');
    try { setTranslation((await api(`/messages/${message.id}/translation`, { method: 'POST' })).translation); }
    catch (err) { setError(err.message); }
    finally { pending.current = false; setLoading(false); }
  }
  return <details className="message-disclosure" onToggle={event => { if (event.currentTarget.open) void load(); }}>
    <summary>한국어로 확인하기</summary>
    {value && <p lang="ko">{value}</p>}{loading && <p role="status">한국어로 옮기고 있어요…</p>}
    {error && <div><p className="error-text" role="alert">{error}</p><button className="text-button" onClick={() => void load()}>번역 다시 시도</button></div>}
  </details>;
}

export function AudioReply({ turn, autoPlay, onPlayed }) {
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const stop = useRef(null);
  const alive = useRef(true);
  const run = useRef(0);
  async function play() {
    const attempt = ++run.current;
    setLoading(true); setError('');
    try {
      const cancel = await playAudio(`/api/turns/${turn.id}/audio`, () => { if (alive.current && attempt === run.current) setPlaying(false); });
      if (!alive.current || attempt !== run.current) { cancel(); return; }
      stop.current = cancel;
      setPlaying(true);
      onPlayed(turn.id);
    } catch (err) { if (alive.current) setError(playbackMessage(err)); }
    finally { if (alive.current && attempt === run.current) setLoading(false); }
  }
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; run.current++; stop.current?.(); };
  }, []);
  useEffect(() => {
    if (turn.has_output_audio && autoPlay) void play();
  }, [turn.has_output_audio, autoPlay]);
  return <div className="reply-audio">
    <button className="audio-button" aria-label={playing ? '답변 음성 일시정지' : '답변 음성 재생'} disabled={!turn.has_output_audio || loading}
      onClick={() => { if (playing) { run.current++; stop.current?.(); setPlaying(false); } else void play(); }}>
      <Icon name={playing ? 'pause' : 'play'} size={17}/>
    </button>
    <div><strong>{playing ? '답변을 듣고 있어요' : loading ? '음성을 불러오는 중' : 'Talky의 답변'}</strong>
      <span>{turn.has_output_audio ? '다시 듣고 싶으면 재생해 주세요.' : turn.status === 'failed' ? turn.error_message || '음성을 제공하지 못했어요. 자막으로 확인해 주세요.' : turn.status === 'completed' ? '음성을 제공하지 못했어요. 자막으로 확인해 주세요.' : '음성이 준비되면 자동으로 재생돼요.'}</span></div>
    {playing && <span className="sound-bars" aria-hidden="true"><i/><i/><i/><i/></span>}
    {error && <p className="audio-error" role="status">{error}</p>}
  </div>;
}

export function Composer({ disabled, onSend, draft, setDraft, mode, setMode }) {
  const [recording, setRecording] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [clip, setClip] = useState(null);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState('');
  const capture = useRef(null);
  const mounted = useRef(true);
  const started = useRef(0);
  const finishRef = useRef(null);
  const url = useRef(null);
  function discard() { if (url.current) URL.revokeObjectURL(url.current); url.current = null; setClip(null); }
  async function finish() {
    const current = capture.current;
    if (!current) return;
    capture.current = null; setRecording(false); setConnecting(true);
    try {
      const blob = await current.stop();
      if (!mounted.current) return;
      if (blob.size < 128) { setError('녹음된 음성이 없습니다. 다시 녹음해 주세요.'); return; }
      discard(); url.current = URL.createObjectURL(blob); setClip({ blob, url: url.current });
    } catch (err) { if (mounted.current) setError(recordingMessage(err)); }
    finally { if (mounted.current) setConnecting(false); }
  }
  finishRef.current = finish;
  async function begin() {
    setConnecting(true); setError('');
    void unlockAudio().catch(() => {});
    try {
      const next = await startCapture(() => finishRef.current(), 90);
      if (!mounted.current) { next.cancel(); return; }
      capture.current = next; started.current = Date.now(); setSeconds(0); setRecording(true);
    } catch (err) {
      setError(recordingMessage(err));
    } finally { if (mounted.current) setConnecting(false); }
  }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; capture.current?.cancel(); if (url.current) URL.revokeObjectURL(url.current); }; }, []);
  useEffect(() => {
    if (!recording) return;
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started.current) / 1000)), 250);
    return () => clearInterval(timer);
  }, [recording]);
  function changeMode(next) {
    if (recording || connecting) return;
    if (clip && !window.confirm('녹음한 음성을 지우고 입력 방식을 바꿀까요?')) return;
    discard(); setError(''); setMode(next);
  }
  return <div className="composer-wrap">
    <div className="input-modes" role="group" aria-label="입력 방식">
      <button type="button" aria-pressed={mode === 'text'} disabled={recording || connecting} onClick={() => changeMode('text')}><Icon name="text" size={17}/>텍스트</button>
      <button type="button" aria-pressed={mode === 'voice'} disabled={recording || connecting} onClick={() => changeMode('voice')}><Icon name="mic" size={17}/>음성</button>
    </div>
    <Notice>{error}</Notice>
    {mode === 'text' ? <form className="text-composer" onSubmit={event => { event.preventDefault(); if (!disabled && draft.trim()) { void unlockAudio().catch(() => {}); onSend(draft); } }}>
      <label className="sr-only" htmlFor="message">보낼 메시지</label>
      <textarea id="message" rows={2} value={draft} maxLength={4000} placeholder="한국어도, 영어도 괜찮아요. 편하게 이야기해 보세요."
        onChange={event => setDraft(event.target.value)} onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
            event.preventDefault(); if (!disabled && draft.trim()) { void unlockAudio().catch(() => {}); onSend(draft); }
          }
        }}/>
      <button className="send-button" aria-label="메시지 보내기" disabled={disabled || !draft.trim()}><Icon name="send"/></button>
    </form> : <div className="voice-composer">
      {clip ? <><audio controls src={clip.url} onPlay={stopPlayback} aria-label="보낼 녹음 미리 듣기"/><div className="button-row"><button className="quiet" disabled={disabled} onClick={discard}>다시 녹음</button><button className="primary" disabled={disabled} onClick={() => { void unlockAudio().catch(() => {}); onSend(clip.blob, clip.url, () => discard()); }}><Icon name="send" size={17}/>음성 보내기</button></div></>
      : <><button type="button" className={`record-button ${recording ? 'recording' : ''}`} disabled={disabled || connecting} aria-label={recording ? '녹음 중지' : '녹음 시작'} onClick={recording ? finish : begin}><Icon name={recording ? 'stop' : 'mic'} size={25}/></button><div><strong>{connecting ? '마이크를 준비하고 있어요' : recording ? `녹음 중 · ${seconds}초` : '버튼을 눌러 이야기해 주세요'}</strong><p>{recording ? '한 번 더 누르면 녹음이 끝나요.' : '최대 90초 · 녹음 후 확인하고 보낼 수 있어요.'}</p></div></>}
    </div>}
    <p className="composer-note" role="status">{disabled ? '이전 요청을 확인하고 있어요. 입력한 내용은 그대로 보관됩니다.' : mode === 'text' ? 'Enter로 전송 · Shift + Enter로 줄바꿈' : '녹음은 보내기 전까지 기기에만 있어요.'}</p>
  </div>;
}

function Auth({ onLogin, onGuest }) {
  const [signup, setSignup] = useState(false);
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [success, setSuccess] = useState('');
  async function submit(event) {
    event.preventDefault(); setError(''); setSuccess('');
    if (signup && password !== repeat) { setError('비밀번호가 일치하지 않습니다.'); return; }
    setBusy(true);
    try {
      if (signup) {
        await api('/auth/register', json('POST', { login_name: name, password }));
        setSignup(false); setSuccess('계정을 만들었어요. 같은 정보로 로그인해 주세요.'); setRepeat('');
      } else {
        await api('/auth/login', json('POST', { login_name: name, password }));
        onLogin(await api('/auth/me'));
      }
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  return <main className="auth-layout"><section className="auth-story"><a className="brand" href="/"><Mark/>talky<span className="brand-period">.</span></a><div className="auth-copy"><span className="intro-line">나답게 말하는 영어</span><h1>하고 싶은 이야기부터<br/>시작해 보세요.</h1><p>잠깐의 일상도, 길었던 하루도.<br/>Talky와 나누며 나만의 영어를 만들어 가요.</p><div className="conversation-preview" aria-hidden="true"><span>오늘은 새로운 도전을 했어요.</span><span>I tried something new today.</span></div></div><p className="auth-foot">조금 서툴러도 괜찮아요.</p></section>
    <section className="auth-form-panel"><form className="auth-form" onSubmit={submit}><h2>{signup ? '반가워요, 시작해 볼까요?' : '다시 만나서 반가워요'}</h2><p>{signup ? '대화와 학습 기록을 계정에 보관해요.' : '어제의 이야기에서 오늘의 영어로 이어가요.'}</p><Notice>{error}</Notice>{success && <p className="success" role="status">{success}</p>}
      <label htmlFor="login-name">아이디</label><input id="login-name" autoComplete="username" value={name} onChange={e => setName(e.target.value)} minLength={3} maxLength={64} required spellCheck={false}/>
      {signup && <small>영문 소문자·숫자·_.-를 사용해 3~64자로 입력해 주세요.</small>}
      <label htmlFor="password">비밀번호</label><input id="password" type="password" autoComplete={signup ? 'new-password' : 'current-password'} value={password} onChange={e => setPassword(e.target.value)} minLength={signup ? 8 : 1} maxLength={signup ? 24 : 128} required/>
      {signup && <><small>8~24자로 입력해 주세요.</small><label htmlFor="repeat">비밀번호 확인</label><input id="repeat" type="password" autoComplete="new-password" value={repeat} onChange={e => setRepeat(e.target.value)} minLength={8} maxLength={24} required/></>}
      <button className="primary auth-submit" disabled={busy}>{busy ? '확인하고 있어요…' : signup ? '계정 만들기' : '로그인'}</button><p className="auth-switch">{signup ? '이미 계정이 있나요?' : '처음 오셨나요?'} <button type="button" disabled={busy} onClick={() => { setSignup(!signup); setError(''); setSuccess(''); }}>{signup ? '로그인' : '계정 만들기'}</button></p><button type="button" className="guest-exam text-button" onClick={onGuest}>로그인 없이 모의고사 체험</button>
    </form></section></main>;
}

function Report({ report, rating, onSave }) {
  const [stars, setStars] = useState(rating?.stars || 0);
  const [comment, setComment] = useState(rating?.comment || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  return <section className="session-report"><span className="section-kicker">오늘의 대화 돌아보기</span><h2>말한 만큼, 한 걸음 더.</h2><p>{report.summary}</p><div className="report-columns">{[['잘한 점', report.strengths], ['다음에 연습할 점', report.weaknesses]].map(([title, items]) => <div key={title}><h3>{title}</h3><ul>{items?.map((item, i) => <li key={i}>{item}</li>)}</ul></div>)}</div>
    {report.vocab_learned?.length > 0 && <><h3>기억할 표현</h3><div className="vocabulary">{report.vocab_learned.map((word, i) => <span key={i}>{word}</span>)}</div></>}
    <form onSubmit={async e => { e.preventDefault(); setBusy(true); setError(''); try { await onSave({ stars, comment }); setSaved(true); } catch (err) { setError(err.message); } finally { setBusy(false); } }}><fieldset><legend>오늘 대화는 어땠나요?</legend><div className="rating-options">{[1,2,3,4,5].map(n => <label key={n}><input type="radio" name="stars" value={n} checked={stars === n} onChange={() => { setStars(n); setSaved(false); }}/><span>{n}점</span></label>)}</div></fieldset><label htmlFor="feedback">남기고 싶은 이야기 <span className="muted">(선택)</span></label><textarea id="feedback" value={comment} maxLength={2000} onChange={e => { setComment(e.target.value); setSaved(false); }}/><Notice>{error}</Notice><button className="primary" disabled={!stars || busy}>{busy ? '저장 중…' : '평가 저장'}</button>{saved && <p className="success" role="status">평가를 저장했어요.</p>}</form>
  </section>;
}

export function Conversation({ cid, onRefresh, onCreated, user }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [locals, setLocals] = useState([]);
  const [draft, setDraft] = useState('');
  const [mode, setMode] = useState('text');
  const [ending, setEnding] = useState(false);
  const [loading, setLoading] = useState(false);
  const [title, setTitle] = useState('');
  const bottom = useRef(null);
  const current = useRef(cid);
  const inFlight = useRef(false);
  const autoIds = useRef(new Set());
  const played = useRef(new Set());
  const scroll = useRef(null);
  const nearBottom = useRef(true);
  current.current = cid;
  useEffect(() => {
    const key = `talky:draft:${user.id}:${cid || 'new'}`;
    setDraft(sessionStorage.getItem(key) || '');
    setData(null); setError(''); setLoading(Boolean(cid));
    if (!cid) return;
    let alive = true, timer, controller;
    async function refresh() {
      controller = new AbortController();
      try {
        const next = await api(`/conversations/${cid}`, { signal: controller.signal });
        if (!alive) return;
        setData(next); setError(''); setLoading(false);
        const received = new Set(next.turns.map(turn => turn.id));
        setLocals(old => old.filter(turn => !received.has(turn.id)));
      } catch (err) { if (alive && err.name !== 'AbortError') { setError(err.message); setLoading(false); } }
      if (alive) timer = setTimeout(refresh, 1500);
    }
    void refresh();
    return () => { alive = false; clearTimeout(timer); controller?.abort(); };
  }, [cid, user.id]);
  useEffect(() => { setTitle(data?.title || ''); }, [data?.title]);
  function updateDraft(value) { setDraft(value); sessionStorage.setItem(`talky:draft:${user.id}:${cid || 'new'}`, value); }
  const turns = mergeTurns(data?.turns || [], locals.filter(turn => turn.conversation_id === cid || (!cid && !turn.conversation_id)));
  const messages = data?.messages || [];
  const outstanding = turns.some(turn => ['queued', 'processing', 'sending', 'unconfirmed'].includes(turn.status));
  useEffect(() => { if (nearBottom.current) bottom.current?.scrollIntoView({ block: 'end', behavior: 'instant' }); }, [messages.length, turns.length, turns.filter(t => t.has_output_audio).length, cid]);
  async function send(content, preview, clearClip, previous) {
    if (inFlight.current || (outstanding && !previous)) return;
    inFlight.current = true; setError('');
    const voice = content instanceof Blob;
    const topic = !voice && typeof content === 'object' ? content.starter_topic : null;
    const rid = previous?.id || crypto.randomUUID();
    let destination = cid;
    const local = previous || { id: rid, conversation_id: cid, status: 'sending', input_text: voice || topic ? '' : content, starter_topic: topic,
      result: {}, has_input_audio: voice, has_output_audio: false, blob: voice ? content : null, preview };
    if (!previous) setLocals(old => [...old, local]);
    else setLocals(old => old.map(t => t.id === rid ? { ...t, status: 'sending' } : t));
    autoIds.current.add(rid); nearBottom.current = true;
    try {
      if (!destination) {
        destination = (await api('/conversations', { method: 'POST' })).id;
        if (topic && draft) sessionStorage.setItem(`talky:draft:${user.id}:${destination}`, draft);
        setLocals(old => old.map(t => t.id === rid ? { ...t, conversation_id: destination } : t));
        onCreated(destination); void onRefresh();
      }
      const received = await api(`/conversations/${destination}/turns/${rid}${voice ? '/voice' : topic ? '/starter' : ''}`, voice ? { method: 'PUT', body: content } : json('PUT', topic ? { topic } : { text: content }));
      setLocals(old => old.map(t => t.id === rid ? { ...received, preview } : t));
      const draftKey = `talky:draft:${user.id}:${cid || 'new'}`;
      if (!voice && sessionStorage.getItem(draftKey) === content) sessionStorage.removeItem(draftKey);
      if (!voice && current.current === destination) setDraft(value => value === content ? '' : value);
      clearClip?.();
    } catch (err) {
      const rejected = [400, 401, 403, 404, 409, 410, 413, 422, 429].includes(err.status);
      setLocals(old => old.map(t => t.id === rid ? { ...t, conversation_id: destination,
        status: rejected ? 'failed' : 'unconfirmed', error_message: rejected ? err.message : null,
        failure: rejected ? null : err.message } : t));
    } finally { inFlight.current = false; }
  }
  function pending(turn) {
    if (turn.starter_topic) return <article className="message assistant-message" key={turn.id}><span className="message-label"><Mark/>Talky</span><p role="status">{turn.status === 'failed' ? turn.error_message || '대화를 시작하지 못했어요. 새 대화에서 다시 시도해 주세요.' : turn.status === 'unconfirmed' ? '주제 선택의 접수 여부를 확인하지 못했어요.' : '주제에 맞는 첫 질문을 준비하고 있어요…'}</p>{turn.failure && <p className="error-text">{turn.failure}</p>}{turn.status === 'unconfirmed' && <button className="text-button" onClick={() => send({ starter_topic: turn.starter_topic }, null, null, turn)}>다시 확인하고 시작하기</button>}</article>;
    return <article className="message user-message" key={turn.id}><span className="message-label">나</span><div className="user-bubble">{turn.input_text || <><Icon name="mic" size={18}/>보낸 음성{!turn.result?.user_text && ['queued', 'processing'].includes(turn.status) && <span className="muted"> · 문장을 인식하고 있어요</span>}</>}</div>
      <p className="message-status" role="status">{turn.status === 'unconfirmed' ? '접수 여부를 확인하지 못했어요. 같은 요청으로 다시 확인할 수 있습니다.' : turn.status === 'sending' ? '보내는 중…' : turn.status === 'failed' ? turn.error_message || '처리 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요.' : '답변을 준비하고 있어요…'}</p>
      {turn.failure && <p className="error-text">{turn.failure}</p>}
      {turn.status === 'unconfirmed' && <button className="text-button" onClick={() => send(turn.blob || turn.input_text, turn.preview, null, turn)}>다시 확인하고 보내기</button>}
    </article>;
  }
  async function end() {
    setEnding(true); setError('');
    try { const report = await api(`/conversations/${cid}/report`, { method: 'POST' }); if (current.current === cid) setData(old => ({ ...old, report, ended_at: new Date().toISOString() })); void onRefresh(); }
    catch (err) { setError(err.message); }
    finally { setEnding(false); }
  }
  return <section className="conversation-view">
    <header className="conversation-header"><div><span className="section-kicker">영어 회화</span><h1>{data?.title && data.title !== 'New Conversation' ? data.title : '나의 이야기가 영어가 되는 시간'}</h1></div>
      {cid && data && <div className="header-actions"><button className="quiet" disabled={outstanding || ending || !messages.length || Boolean(data.report)} onClick={end}>{ending ? '돌아보는 중…' : data.ended_at && !data.report ? '보고서 다시 만들기' : '대화 마치기'}</button><details className="conversation-menu"><summary aria-label="대화 관리"><Icon name="more"/></summary><form onSubmit={async event => { event.preventDefault(); try { await api(`/conversations/${cid}`, json('PATCH', { title })); void onRefresh(); } catch (err) { setError(err.message); } }}><label htmlFor="title">대화 이름</label><input id="title" value={title} onChange={e => setTitle(e.target.value)} maxLength={100} required/><button className="quiet">이름 저장</button><button type="button" className="danger" onClick={async () => { if (!window.confirm(`‘${data.title}’ 대화와 학습 기록을 삭제할까요? 이 작업은 되돌릴 수 없습니다.`)) return; try { await api(`/conversations/${cid}`, { method: 'DELETE' }); onCreated(null); await onRefresh(); } catch (err) { setError(err.message); } }}>대화 삭제</button></form></details></div>}
    </header>
    <div className="chat-scroll" ref={scroll} onScroll={() => { const el = scroll.current; nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120; }}><div className="chat-content"><Notice>{error}</Notice>
      {loading && <p className="muted" role="status">대화를 불러오고 있어요…</p>}
      {!loading && !messages.length && !turns.length && <div className="empty-conversation"><div className="empty-mark"><Mark/></div><h2>오늘은 어떤 이야기를 해볼까요?</h2><p>주제를 고르면 Talky가 먼저 말을 걸어요.<br/>아래에서 직접 이야기하며 시작해도 좋아요.</p><div className="starter-prompts" aria-label="AI가 먼저 시작할 대화 주제">{[['daily','오늘의 일상'],['travel','여행'],['food','음식'],['hobbies','취미'],['work','일과 학교'],['random','아무 주제나']].map(([topic,label]) => <button key={topic} disabled={outstanding || inFlight.current} onClick={() => { void unlockAudio().catch(() => {}); void send({ starter_topic: topic }); }}>{label}<span aria-hidden="true">↗</span></button>)}</div></div>}
      {messages.map((message, index) => {
        const turn = turns.find(t => t.result?.[message.role === 'user' ? 'user_message_id' : 'assistant_message_id'] === message.id);
        const reply = turn ? messages.find(m => m.id === turn.result?.assistant_message_id) : messages[index + 1]?.role === 'assistant' ? messages[index + 1] : null;
        const correction = message.better_expression || turn?.result?.better_expression || reply?.better_expression;
        return message.role === 'user' ? <article key={message.id} className="message user-message"><span className="message-label">나</span><div className="user-bubble">{message.content}</div>{message.english_expression && <details className="message-disclosure"><summary>영어로는 이렇게 말해요</summary><p lang="en">{message.english_expression}</p></details>}<ExpressionFeedback value={correction} original={message.content}/></article>
        : <article key={message.id} className="message assistant-message"><span className="message-label"><Mark/>Talky</span>{turn ? <AudioReply turn={turn} autoPlay={autoIds.current.has(turn.id) && !played.current.has(turn.id)} onPlayed={id => played.current.add(id)}/> : <p className="muted">저장된 답변</p>}<details className="message-disclosure"><summary>자막 보기</summary><p lang="en">{message.content}</p></details><KoreanReply message={message}/></article>;
      })}
      {pendingTurns(turns).map(pending)}
      {data?.report && <Report key={cid} report={data.report} rating={data.rating} onSave={body => api(`/conversations/${cid}/rating`, json('PUT', body))}/>}
      <div ref={bottom}/>
    </div></div>
    {!data?.ended_at && <Composer key={cid || 'new'} disabled={outstanding || ending || loading || Boolean(cid && !data)} onSend={send} draft={draft} setDraft={updateDraft} mode={mode} setMode={setMode}/>}
    {data?.ended_at && !data.report && <div className="ended-note" role="status">대화를 마쳤어요. 위의 버튼으로 보고서를 다시 만들 수 있습니다.</div>}
  </section>;
}

export default function App() {
  const [user, setUser] = useState(undefined);
  const [error, setError] = useState('');
  const [list, setList] = useState([]);
  const [page, setPage] = useState(0);
  const [cid, setCid] = useState(new URLSearchParams(location.search).get('c'));
  const [view, setView] = useState(new URLSearchParams(location.search).has('exam') ? 'exam' : 'chat');
  const [navOpen, setNavOpen] = useState(false);
  async function checkLogin() {
    setError('');
    try { setUser(await api('/auth/me')); }
    catch (err) { if (err.status === 401) setUser(null); else setError(err.message); }
  }
  useEffect(() => { void checkLogin(); const expired = () => setUser(null); window.addEventListener('talky:unauthorized', expired); return () => window.removeEventListener('talky:unauthorized', expired); }, []);
  async function refresh() {
    if (!user) return;
    try { setList(await api(`/conversations?offset=${page * 50}`)); }
    catch (err) { setError(err.message); }
  }
  useEffect(() => { setList([]); if (user) void refresh(); }, [user?.id, page]);
  function choose(id) { setCid(id); setView('chat'); setNavOpen(false); const url = new URL(location.href); url.searchParams.delete('exam'); if (id) url.searchParams.set('c', id); else url.searchParams.delete('c'); history.replaceState({}, '', url); }
  if (user === undefined) return <main className="loading-screen"><div className="brand"><Mark/>talky.</div><p role="status">대화를 준비하고 있어요…</p><Notice>{error}</Notice>{error && <button className="primary" onClick={checkLogin}>다시 연결</button>}</main>;
  if (!user && view === 'exam') return <main className="guest-layout"><header className="guest-header"><a className="brand" href="/"><Mark/>talky.</a><button className="quiet" onClick={() => setView('chat')}>로그인으로 돌아가기</button></header><Exam/></main>;
  if (!user) return <Auth onGuest={() => setView('exam')} onLogin={value => { setUser(value); setList([]); choose(null); }}/>;
  return <div className="app-shell"><button className="mobile-nav-button quiet" onClick={() => setNavOpen(!navOpen)} aria-expanded={navOpen} aria-controls="sidebar">{navOpen ? '메뉴 닫기' : '메뉴 열기'}</button><aside id="sidebar" className={`sidebar ${navOpen ? 'open' : ''}`}><a className="brand" href="/" onClick={e => { e.preventDefault(); choose(null); }}><Mark/>talky<span className="brand-period">.</span></a><button className="primary new-conversation" onClick={() => choose(null)}><Icon name="plus" size={18}/>새 대화</button><nav aria-label="학습 메뉴"><button className={view === 'chat' ? 'selected' : ''} aria-current={view === 'chat' ? 'page' : undefined} onClick={() => { setView('chat'); setNavOpen(false); }}><Icon name="chat"/>영어 회화</button><button className={view === 'exam' ? 'selected' : ''} aria-current={view === 'exam' ? 'page' : undefined} onClick={() => { setView('exam'); setNavOpen(false); }}><Icon name="book"/>스피킹 모의고사</button></nav><section className="history"><h2>내 대화</h2><Notice>{error}</Notice>{!list.length && <p className="empty-history">새로운 이야기를 시작해 보세요.</p>}<div className="history-list">{list.map(item => <button key={item.id} className={cid === item.id && view === 'chat' ? 'active' : ''} onClick={() => choose(item.id)}><Icon name="chat" size={16}/><span>{item.title === 'New Conversation' ? '새로운 대화' : item.title}</span>{item.ended_at && <span className="sr-only">종료됨</span>}</button>)}</div>{(page > 0 || list.length === 50) && <div className="history-pages"><button disabled={!page} onClick={() => setPage(page - 1)}>이전</button><span>{page + 1}</span><button disabled={list.length < 50} onClick={() => setPage(page + 1)}>다음</button></div>}</section><footer className="sidebar-footer"><span className="user-avatar">{(user.name || '나')[0].toUpperCase()}</span><span>{user.name}</span><button className="icon-button" aria-label="로그아웃" onClick={async () => { try { await api('/auth/logout', { method: 'POST' }); setUser(null); setCid(null); setList([]); } catch (err) { setError(err.message); } }}><Icon name="logout" size={18}/></button></footer></aside><main className="workspace">{view === 'chat' ? <Conversation cid={cid} onCreated={choose} onRefresh={refresh} user={user}/> : <Exam/>}</main></div>;
}
