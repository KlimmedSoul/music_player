const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function makeAPI(){
 const context={EventTarget,window:{},localStorage:{getItem:()=>null},sessionStorage:{getItem:()=>null},location:{protocol:'https:',port:'',hostname:'site.example'},Promise,Map,JSON};
 vm.createContext(context);vm.runInContext(fs.readFileSync('frontend/api.js','utf8'),context);
 const api=new context.window.MusicAPI();api.token='old';return api;
}
const track={source:'vk',external_id:'1'};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('completion is written after an in-flight position for the same track',async()=>{
 const api=makeAPI(),calls=[];let complete;
 api.request=(path)=>{calls.push(path);return path==='/positions'?new Promise(r=>complete=r):Promise.resolve({ok:true});};
 const position=api.savePosition(track,20);const ended=api.scrobble(track,{position:180,duration:180,completed:true});
 await tick();assert.deepEqual(calls,['/positions']);
 complete({ok:true});await Promise.all([position,ended]);assert.deepEqual(calls,['/positions','/plays']);
});
test('queued history from a previous account is not posted to the new account',async()=>{
 const api=makeAPI(),calls=[];let complete;
 api.request=(path)=>{calls.push(path);return new Promise(r=>complete=r);};
 const first=api.savePosition(track,20);const old=api.scrobble(track,{position:30,duration:180});
 await tick();api.base='https://new.example';api.token='new';complete({ok:true});
 await Promise.all([first,old]);assert.deepEqual(calls,['/positions']);
});
test('refresh is serialized in the resolve request body',async()=>{
 const api=makeAPI();let body;
 api.request=(_path,options)=>{body=options.body;return Promise.resolve({});};
 await api.resolve(track,{refresh:true});
 assert.equal(body.refresh,true);assert.equal(body.full_only,true);
});
