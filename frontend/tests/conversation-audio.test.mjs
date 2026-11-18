import { test, after, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';

const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/' });
for (const key of ['window','document','HTMLElement','Event','sessionStorage','location','history']) globalThis[key] = dom.window[key];
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
HTMLElement.prototype.scrollIntoView = () => {};
globalThis.AudioContext = class {
  state = 'running'; destination = {};
  get currentTime() { return performance.now()/1000; }
  createBuffer(_channels, count, rate) { return { duration:count/rate, getChannelData:() => new Float32Array(count) }; }
  createBufferSource() {
    return { connect() {}, start(at) { this.timer = setTimeout(() => this.onended?.(), Math.max(0, at-performance.now()/1000)*1000 + this.buffer.duration*1000); }, stop() { clearTimeout(this.timer); } };
  }
};
const React = await import('react');
const { createRoot } = await import('react-dom/client');
const vite = await createServer({ server:{middlewareMode:true,hmr:false,ws:false}, appType:'custom', logLevel:'error' });
const { default:App, AudioReply } = await vite.ssrLoadModule('/src/App.jsx');
const json = value => new Response(JSON.stringify(value),{headers:{'content-type':'application/json'}});
const tick = (ms=20) => new Promise(resolve => setTimeout(resolve,ms));
let root;
beforeEach(() => { document.getElementById('root').replaceChildren(); root=createRoot(document.getElementById('root')); });
afterEach(async () => { await React.act(async () => root.unmount()); });
after(async () => { await vite.close(); dom.window.close(); });

test('the retired comparison URL and legacy account flag show conversation without a comparison menu', async () => {
  history.replaceState({}, '', '/?tts=1');
  globalThis.fetch = async url => {
    assert.ok(!url.includes('/tts-comparison'));
    return url.endsWith('/auth/me') ? json({id:'u1',name:'Learner',tts_comparison:true}) : json([]);
  };
  await React.act(async () => { root.render(React.createElement(App)); await tick(); });
  assert.ok(document.querySelector('.conversation-view'));
  assert.ok(!document.querySelector('.tts-comparison'));
  assert.ok(!document.querySelector('nav').textContent.includes('목소리 비교'));
});

test('conversation starts partial speech while coaching is processing and does not restart on completion', async () => {
  let network, requests=0, acknowledged=0, signal;
  globalThis.fetch = async (url, options={}) => {
    assert.equal(url,'/api/turns/turn-1/audio-stream');
    requests++; signal=options.signal;
    return new Response(new ReadableStream({start(controller){network=controller;}}),{headers:{'content-type':'audio/pcm'}});
  };
  const turn={id:'turn-1',status:'processing',has_output_audio:false,has_audio_stream:true};
  const render = value => root.render(React.createElement(AudioReply,{turn:value,autoPlay:true,onPlayed:()=>acknowledged++}));
  await React.act(async()=>{render(turn);await tick();});
  await React.act(async()=>{network.enqueue(new Uint8Array(4800));await tick(60);});
  assert.match(document.body.textContent,/답변을 듣고 있어요/);
  assert.equal(acknowledged,1);
  await React.act(async()=>{render({...turn,status:'completed',has_output_audio:true});await tick();});
  assert.equal(requests,1);
  await React.act(async()=>{document.querySelector('.audio-button').click();await tick();});
  assert.equal(signal.aborted,true);
  await React.act(async()=>{render({...turn,status:'completed',has_output_audio:true});await tick();});
  assert.equal(requests,1); // Stopping never replays the already heard prefix automatically.
  network.close();
});
