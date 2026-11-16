import React, { useEffect, useRef, useState } from 'react';
import { api, json } from './api.js';
import { playAudio, recordingMessage, startCapture, unlockAudio } from './audio.js';
import { Icon } from './icons.jsx';

function ExamReport({ report, refresh }) {
  const [score, setScore] = useState('120');
  const [month, setMonth] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const payload = report.payload || {};
  return <section className="exam-report"><span className="section-kicker">스피킹 모의고사 결과</span><h2>다음 연습의 방향을 찾아요.</h2>{payload.partial_reason && <p className="notice" role="status">{payload.partial_reason}</p>}
    <div className="score-summary"><div><span>예상 점수 범위</span><strong>{report.score_low ?? '–'}–{report.score_high ?? '–'}</strong></div><div><span>예상 수준</span><strong>{report.expected_level || '검토 중'}</strong></div><div><span>추정 신뢰도</span><strong>{report.confidence || '검토 중'}</strong></div></div>
    <p className="muted">공식 점수가 아닌 학습 참고용 베타 추정치입니다.</p><h3>먼저 연습할 점</h3>{payload.priority_improvements?.map((item, i) => <details open key={i}><summary>Q{item.question_number} · {item.advice}</summary><p>{item.minimal_revision}</p><p>{item.band_example}</p></details>)}
    <h3>문항별 피드백</h3>{payload.items?.map(item => <details key={item.question_number}><summary>Q{item.question_number} · {item.score == null ? '평가 근거 부족' : `${Number(item.score).toFixed(2)} / 4`}</summary><p lang="en">{item.transcript}</p><ul>{item.evidence?.map((e, i) => <li key={i}>{e}</li>)}</ul></details>)}<p className="muted">{payload.method_note}</p>
    {report.status === 'partial' && <button className="quiet" disabled={busy} onClick={async () => { setBusy(true); try { await api('/exam/retry-report', { method: 'POST' }); refresh(); } catch (err) { setNotice(err.message); } finally { setBusy(false); } }}>상세 평가 다시 요청</button>}
    <form className="calibration-form" onSubmit={async event => { event.preventDefault(); setBusy(true); try { await api('/exam/calibration', json('POST', { score: Number(score), month: month || null })); setNotice('익명 보정 표본을 저장했습니다.'); } catch (err) { setNotice(err.message); } finally { setBusy(false); } }}><h3>실제 공식 점수 비교 <span className="muted">(선택)</span></h3><p className="muted">익명 보정 표본으로만 저장하며 30건 전에는 채점식에 반영하지 않습니다.</p><label htmlFor="official-score">공식 점수</label><select id="official-score" value={score} onChange={e => setScore(e.target.value)}>{Array.from({ length: 21 }, (_, i) => <option key={i} value={i * 10}>{i * 10}</option>)}</select><label htmlFor="exam-month">응시 월 (선택)</label><input id="exam-month" type="month" value={month} onChange={e => setMonth(e.target.value)}/><button className="quiet" disabled={busy}>익명 표본 제출</button></form>{notice && <p role="status">{notice}</p>}
  </section>;
}

function Question({ data, auto, onStarted, refresh }) {
  const [phase, setPhase] = useState('ready');
  const [remaining, setRemaining] = useState(0);
  const [error, setError] = useState('');
  const [narrationNote, setNarrationNote] = useState('');
  const [upload, setUpload] = useState(null);
  const control = useRef(null);
  const capture = useRef(null);
  const stopAudio = useRef(null);
  const started = useRef(false);
  const q = data.question;
  const response = data.responses.find(r => r.question_number === q.number);
  const submitted = response && ['queued', 'processing', 'completed'].includes(response.status);
  async function wait(seconds, name, signal) {
    setPhase(name);
    const deadline = Date.now() + seconds * 1000;
    while (!signal.aborted && Date.now() < deadline) {
      setRemaining(Math.ceil((deadline - Date.now()) / 1000));
      await new Promise(resolve => setTimeout(resolve, Math.min(250, Math.max(1, deadline - Date.now()))));
    }
    if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
  }
  async function send(blob) {
    setPhase('upload'); setError(''); setUpload(blob);
    try {
      await api(`/exam/response/${q.number}`, { method: 'PUT', body: blob });
      setUpload(null); setPhase('waiting'); refresh();
    } catch (err) { setError(`${err.message} 녹음은 보관되어 있어 같은 파일로 다시 확인할 수 있습니다.`); setPhase('upload-error'); }
  }
  async function begin() {
    if (started.current || submitted) return;
    started.current = true;
    const controller = new AbortController(); control.current = controller;
    const signal = controller.signal;
    setPhase('loading'); setError('');
    try {
      await unlockAudio();
      const permission = await navigator.mediaDevices.getUserMedia({ audio: true });
      permission.getTracks().forEach(track => track.stop());
      if (signal.aborted) return;
      onStarted();
      if (q.group_read_seconds) await wait(q.group_read_seconds, 'review', signal);
      setPhase('prompt');
      for (let i = 0; i < q.prompt_repeat_count; i++) {
        try {
          await new Promise(async (resolve, reject) => {
            signal.addEventListener('abort', () => { stopAudio.current?.(); reject(new DOMException('Cancelled', 'AbortError')); }, { once: true });
            try { stopAudio.current = await playAudio('/api/exam/narration', resolve); if (signal.aborted) { stopAudio.current(); reject(new DOMException('Cancelled', 'AbortError')); } }
            catch (err) { reject(err); }
          });
        } catch (err) { if (signal.aborted) throw err; setNarrationNote('안내 음성을 재생하지 못해 화면의 영어 문항으로 진행합니다.'); break; }
      }
      await wait(q.preparation_seconds, 'prepare', signal);
      capture.current = await startCapture(() => {}, q.response_seconds);
      if (signal.aborted) { capture.current.cancel(); return; }
      await wait(q.response_seconds, 'record', signal);
      const blob = await capture.current.stop(); capture.current = null;
      if (!signal.aborted) await send(blob);
    } catch (err) {
      capture.current?.cancel(); capture.current = null;
      if (!signal.aborted) { setError(recordingMessage(err)); setPhase('technical-error'); started.current = false; }
    }
  }
  useEffect(() => { if (auto && !submitted) void begin(); return () => { control.current?.abort(); capture.current?.cancel(); stopAudio.current?.(); }; }, []);
  const labels = { ready: '문항을 준비했어요', loading: '마이크를 확인하고 있어요', review: '정보 읽기', prompt: '안내 음성을 듣고 있어요', prepare: '답변 준비', record: '답변 녹음', upload: '녹음을 보내고 있어요', waiting: '다음 문항을 준비하고 있어요', 'upload-error': '접수 확인이 필요해요', 'technical-error': '녹음 장치를 다시 확인해 주세요' };
  return <section className="exam-question"><div className="exam-progress"><span>QUESTION {q.number} / 11</span><span>{data.scoring_profile} 평가</span></div><progress value={q.number - 1} max={11} aria-label="완료한 문항 수"/>
    <h2 lang="en">{q.direction}</h2>{q.information_panel && <div className="information-panel"><h3>{q.information_panel.title}</h3><p>{q.information_panel.subtitle}</p><div className="table-scroll"><table><thead><tr>{q.information_panel.headers.map((h, i) => <th key={i}>{h}</th>)}</tr></thead><tbody>{q.information_panel.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody></table></div><p>{q.information_panel.note}</p></div>}
    {data.asset?.source_url && <figure><img src={data.asset.source_url} alt="영어로 묘사할 시험 사진" referrerPolicy="no-referrer"/><figcaption>{data.asset.author} · <a href={data.asset.license_url || data.asset.source_url} target="_blank" rel="noreferrer">{data.asset.license_name}</a></figcaption></figure>}
    <p className="exam-prompt" lang="en">{q.prompt}</p><div className={`exam-timer ${phase === 'record' ? 'is-recording' : ''}`} role="status"><Icon name={phase === 'record' ? 'mic' : 'book'} size={24}/><span>{submitted ? '응답을 처리하고 있어요' : labels[phase]}</span>{['review', 'prepare', 'record'].includes(phase) && <strong>{remaining}<small>초</small></strong>}</div>
    {response?.error_message && <p className="notice" role="status">{response.error_message}</p>}{error && <p className="notice" role="alert">{error}</p>}{narrationNote && <p className="muted">{narrationNote}</p>}
    {!submitted && ['ready', 'technical-error'].includes(phase) && <button className="primary" onClick={begin}><Icon name="mic"/>마이크 연결하고 시작</button>}
    {phase === 'upload-error' && upload && <button className="primary" onClick={() => send(upload)}>같은 녹음으로 접수 확인</button>}
    <p className="muted">시작 후 타이머가 자동으로 진행됩니다. 일시정지와 임의 재녹음은 제공하지 않습니다.</p>
  </section>;
}

export function Exam() {
  const [data, setData] = useState(null);
  const [quota, setQuota] = useState(null);
  const [loading, setLoading] = useState(true);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [auto, setAuto] = useState(false);
  const alive = useRef(true);
  async function refresh() {
    try { const value = await api('/exam/session'); if (alive.current) setData(value); }
    catch (err) { if (alive.current && ![404, 410].includes(err.status)) setError(err.message); }
    finally { if (alive.current) setLoading(false); }
  }
  useEffect(() => {
    alive.current = true;
    let timer;
    async function load() {
      const token = new URLSearchParams(location.search).get('exam');
      if (token) { try { await api('/exam/recover', json('POST', { token })); } catch (err) { if (alive.current) setError(err.message); } }
      try { const value = await api('/exam/quota'); if (alive.current) setQuota(value); } catch (err) { if (alive.current) setError(err.message); }
      await refresh();
      async function poll() { await refresh(); if (alive.current) timer = setTimeout(poll, 2000); }
      if (alive.current) timer = setTimeout(poll, 2000);
    }
    void load(); return () => { alive.current = false; clearTimeout(timer); };
  }, []);
  return <section className="exam-view"><header className="conversation-header"><div><span className="section-kicker">영어 말하기 연습</span><h1>스피킹 모의고사</h1></div>{data && <a className="quiet" href={`/?exam=${encodeURIComponent(data.recovery_token)}`}>시험 복구 링크</a>}</header><div className="exam-content"><p className="exam-disclaimer">TOEIC®은 ETS의 등록상표입니다. 이 모의고사는 ETS의 승인·보증을 받지 않은 자체 제작 연습이며, 결과는 비공식 베타 추정치입니다.</p>{error && <p className="notice" role="alert">{error}</p>}
    {loading ? <p role="status">시험 상태를 확인하고 있어요…</p> : data?.report && ['partial', 'completed'].includes(data.report.status) ? <ExamReport report={data.report} refresh={refresh}/> : data?.status === 'active' ? <Question key={`${data.id}:${data.current_question}:${data.responses.find(r => r.question_number === data.current_question && r.status === 'technical_error')?.attempts || 0}`} data={data} auto={auto} onStarted={() => setAuto(true)} refresh={refresh}/> : data ? <div className="exam-waiting" role="status"><h2>{data.status === 'failed' ? '시험을 완료하지 못했어요.' : '답변을 꼼꼼히 돌아보고 있어요.'}</h2><p>{data.status === 'failed' ? data.error_message || '시험 처리 중 문제가 생겼어요. 잠시 후 다시 확인해 주세요.' : '처리가 끝나면 이 화면에 결과가 표시됩니다.'}</p></div> : <section className="exam-intro"><div className="empty-mark"><Icon name="book" size={32}/></div><h2>실전처럼 말하고,<br/>다음 연습을 준비해요.</h2><p>11개 문항을 약 20분 동안 연속해서 답합니다.<br/>마이크를 연결하면 준비와 답변 시간이 자동으로 진행돼요.</p><dl><div><dt>진행 방식</dt><dd>일시정지·뒤로가기·임의 재녹음 없이 진행</dd></div><div><dt>이용 환경</dt><dd>마이크와 HTTPS 연결 · 데스크톱 Chrome·Edge 권장</dd></div><div><dt>개인정보 보관</dt><dd>녹음은 암호화하며 전사문·결과와 함께 72시간 후 정리</dd></div><div><dt>공개 데모 한도</dt><dd>IP 기준 하루 {quota?.daily_limit ?? 1}회 · 전체 동시 {quota?.global_limit ?? 1}건</dd></div></dl>
    {(!quota || !quota.allowed) && <button className="quiet" onClick={async () => { try { setQuota(await api('/exam/quota')); setError(''); } catch (err) { setError(err.message); } }}>이용 가능 여부 다시 확인</button>}
    {quota && !quota.allowed && <p className="notice" role="status">{quota.daily_used >= quota.daily_limit ? '오늘 이용 횟수를 모두 사용했습니다.' : '다른 사용자가 시험을 진행하고 있습니다. 잠시 후 다시 확인해 주세요.'}</p>}<label className="consent"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)}/><span>개인정보 처리, 72시간 보관, 비공식 베타 채점에 동의합니다.</span></label><button className="primary" disabled={!consent || !quota?.allowed || busy} onClick={async () => { setBusy(true); setError(''); try { await api('/exam/session', json('POST', { consent })); await refresh(); } catch (err) { setError(err.message); } finally { setBusy(false); } }}>{busy ? '시험을 준비하고 있어요…' : '모의고사 시작'}</button></section>}
  </div></section>;
}
