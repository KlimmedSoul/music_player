// Run with node tests/player-session.cjs; no browser or provider credentials.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class FakeAudio extends EventTarget {
  constructor() { super(); this.paused=true; this.ended=false; this.currentTime=0; this.duration=180; this.readyState=1; }
  pause() { this.paused=true; }
  play() { this.paused=false; return Promise.resolve(); }
  load() {}
  removeAttribute() { this.src=''; }
  getAttribute() { return this.src || null; }
  canPlayType() { return ''; }
}
function makePlayer(api) {
  const context={EventTarget,Event,CustomEvent:class extends Event {constructor(name, options){super(name);this.detail=options?.detail;}}, Audio:FakeAudio, AbortController, navigator:{}, window:{}, document:{querySelector:()=>({src:'https://example.test/cover.jpg'})}, Date, Math, URL};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('frontend/player.js','utf8'),context);
  return new context.window.MusicPlayer(api);
}
const tracks=[{source:'vk',external_id:'1',title:'One'},{source:'vk',external_id:'2',title:'Two'}];
test('reset aborts prefetch and ignores a late reply from the previous account',async()=>{
  let resolve, signal;
  const api={base:'https://a.example',token:'old',resolve:(_t,o)=>{signal=o.signal;return new Promise(r=>resolve=r);}};
  const player=makePlayer(api);player.queue=tracks;player.index=0;player.prewarmNext();
  player.reset(); api.base='https://b.example';api.token='new';
  resolve({kind:'direct',stream_url:'https://a.example/old.mp3'});
  await new Promise(r=>setImmediate(r));
  assert.equal(signal.aborted,true);assert.equal(player.prewarm.size,0);
});
test('next waits for the existing prefetch without issuing a second resolve',async()=>{
  let resolve, calls=0;
  const api={base:'https://a.example',token:'same',resolve:()=>{calls++;return new Promise(r=>resolve=r);}};
  const player=makePlayer(api);player.queue=tracks;player.index=0;player.prewarmNext();
  const loading=player.select(tracks,1);
  resolve({kind:'direct',stream_url:'https://a.example/full.mp3'});
  await loading;
  assert.equal(calls,1);assert.equal(player.audio.src,'https://a.example/full.mp3');
});
