'use strict'
const { test } = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')
const built = fs.readFileSync(path.join(__dirname, '../dist/background.js'), 'utf8')
const URL = 'https://www.zhipin.com/job_detail/ocr-test.html'
const candidate = () => ({title: '工程师', source_url: URL, salary_text: null, missing_fields:['salary_text'], warnings:['薪资无法识别'], matched_selectors:{}})
const frame = () => ({canonicalUrl: URL, title:'工程师', raw:'\ue039-\ue032\ue033K', nodeId:1, pageUrl:URL,
  x:100, y:100, width:100, height:30, viewportWidth:1000, viewportHeight:600, scrollX:0, scrollY:0})
function event() {
  const listeners = new Set()
  return {addListener:f=>listeners.add(f), removeListener:f=>listeners.delete(f), fire:(...a)=>listeners.forEach(f=>f(...a)), listeners}
}
function harness(options={}) {
  const activations=event(), updates=event(), focus=event()
  const state={captures:0, reads:0, posts:[], crops:[], closed:0}
  const chrome={runtime:{onMessage:event()}, storage:{session:{get:async()=>({}),set:async()=>{},remove:async()=>{}},onChanged:event()},
    tabs:{onActivated:activations,onUpdated:updates,
      get:async()=>({id:7,active:options.active!==false,windowId:2,url:URL}),
      query:async()=>[{id:7,active:true,windowId:2,url:URL}],
      captureVisibleTab:async(id,fmt)=>{assert.equal(id,2);assert.equal(fmt.format,'png');state.captures++;options.onCapture?.({activations,updates,focus});return 'data:image/png;base64,'+btoa('FULL_IMAGE')},
      sendMessage:async(id,msg)=>{assert.equal(id,7); assert.equal(msg.type,'jobagent:salary-frame');state.reads++; return {ok:true,result: options.frame?.(state.reads) || {status:'ok',frame:frame()}}}},
    windows:{get:async()=>({focused:options.focused!==false}),onFocusChanged:focus}}
  class Canvas { constructor(w,h){this.width=w;this.height=h} getContext(){return {drawImage:(...args)=>state.crops.push(args.slice(1))}} async convertToBlob(){return new Blob(['CROP_ONLY'])} }
  const ctx=vm.createContext({chrome,console,setTimeout,clearTimeout,Date,URL:globalThis.URL,Uint8Array,Blob,atob,btoa,AbortController,OffscreenCanvas:Canvas,
    createImageBitmap:async()=>({width:1000,height:600,close:()=>state.closed++}),
    fetch:async(url,init)=>{assert.equal(url,'http://127.0.0.1:8000/api/extension/salary-ocr');state.posts.push(JSON.parse(init.body));options.onFetch?.({activations,updates,focus});return {ok:true,json:async()=>({salary_text:options.salary===undefined?'8-13K':options.salary})}}})
  vm.runInContext(built,ctx)
  return {ctx,state,activations,updates,focus,run:(c=candidate(),allowed=async()=>true)=>ctx.supplementSalary(7,c,allowed)}
}
test('OCR sends only cropped pixels to localhost, repairs missing field and preserves identity',async()=>{
  const h=harness();const result=await h.run()
  assert.equal(result.salary_text,'8-13K');assert.equal(result.source_url,URL)
  assert.equal(result.missing_fields.length,0);assert.match(result.matched_selectors.salary_text,/local_screenshot_ocr/)
  assert.deepEqual(h.state.posts,[{image:btoa('CROP_ONLY')}]);assert.deepEqual(h.state.crops,[[100,100,100,30,0,0,200,60]])
  assert.equal(h.state.closed,1);assert.equal(h.state.captures,1)
  assert.equal(h.activations.listeners.size,0);assert.equal(h.focus.listeners.size,0)
})
test('already usable salary does not take a screenshot',async()=>{const h=harness();assert.equal((await h.run({...candidate(),salary_text:'20-30K'})).salary_text,'20-30K');assert.equal(h.state.captures,0)})
test('missing canonical identity does not capture',async()=>{const h=harness();await h.run({...candidate(),source_url:null});assert.equal(h.state.captures,0)})
test('no safe DOM region remains unknown without capture',async()=>{const h=harness({frame:()=>({status:'no_safe_region',frame:null})});assert.equal((await h.run()).salary_text,null);assert.equal(h.state.captures,0)})
for(const status of ['verification']) test(status+' stops without capture',async()=>{const h=harness({frame:()=>({status,frame:null})});await assert.rejects(h.run(),/verification/);assert.equal(h.state.captures,0)})
for(const options of [{active:false},{focused:false}]) test('background tab/window cannot capture '+JSON.stringify(options),async()=>{const h=harness(options);await assert.rejects(h.run());assert.equal(h.state.captures,0)})
test('job changes during screenshot: discard before sending even the crop',async()=>{
  const h=harness({frame:(n)=>({status:'ok',frame:{...frame(),nodeId:n===1?1:2}})})
  await assert.rejects(h.run(),/identity_or_region_changed/);assert.equal(h.state.posts.length,0)
})
test('tab switches away and back invalidate the screenshot',async()=>{const h=harness({onCapture:({activations})=>{activations.fire();activations.fire()}});await assert.rejects(h.run(),/tab_changed/);assert.equal(h.state.posts.length,0)})
test('navigation during OCR discards result and releases lock/listeners',async()=>{const h=harness({onFetch:({updates})=>updates.fire(7,{status:'loading'})});await assert.rejects(h.run(),/tab_changed/);assert.equal(h.updates.listeners.size,0);assert.equal(vm.runInContext('salaryOcrBusy',h.ctx),false)})
test('cancellation during OCR discards result',async()=>{let allowed=true;const h=harness({onFetch:()=>{allowed=false}});await assert.rejects(h.run(candidate(),async()=>allowed),/cancelled/)})
for (const phase of ['onCapture', 'onFetch']) test('acknowledged stop takes precedence over popup focus change '+phase, async()=>{
  let allowed=true
  const h=harness({[phase]:({focus})=>{allowed=false;focus.fire(-1)}})
  await assert.rejects(h.run(candidate(),async()=>allowed),/salary_cancelled/)
  assert.equal(h.focus.listeners.size,0)
  assert.equal(vm.runInContext('salaryOcrBusy',h.ctx),false)
  if(phase==='onCapture') assert.equal(h.state.posts.length,0)
})
for(const salary of [null,'8-13','8-13K·15薪','8-13万','not salary']) test('unusable/dropped or invented units/suffix rejected '+salary,async()=>{const h=harness({salary});assert.equal((await h.run()).salary_text,null)})
test('DOM month suffix cannot silently disappear in OCR',async()=>{const h=harness({frame:()=>({status:'ok',frame:{...frame(),raw:'\ue039-\ue032\ue033K·15薪'}})});assert.equal((await h.run()).salary_text,null)})
test('one request at a time; a concurrent call cannot screenshot',async()=>{let resolve;const gate=new Promise(r=>resolve=r);const h=harness();const first=h.run(candidate(),async()=>{await gate;return true});await h.run();resolve();await first;assert.equal(h.state.captures,1)})
test('request timeout has bounded lifetime',async()=>{const h=harness();await assert.rejects(h.ctx.salaryBounded(new Promise(()=>{}),5),/salary_timeout/)})
test('new/resumed runner cannot start during manual OCR',async()=>{const h=harness();vm.runInContext('salaryOcrBusy=true',h.ctx);assert.equal((await h.ctx.startRunner(1,3)).error,'salary_ocr_busy');assert.equal((await h.ctx.resumeRunner()).error,'salary_ocr_busy')})

test('back-to-back candidates both get a salary instead of one being rate-limited',async()=>{
  // The runner processes candidates one after another, so the second reliably
  // lands inside the capture interval. Skipping it there cost 71 of 176 jobs
  // their salary in one real run - they never attempted a capture at all.
  const h=harness()
  const first=await h.run()
  assert.equal(first.salary_text,'8-13K')
  const started=Date.now()
  const second=await h.run()
  const waited=Date.now()-started
  assert.equal(second.salary_text,'8-13K','the second candidate must not lose its salary')
  assert.equal(h.state.captures,2,'it waits for the interval rather than skipping the capture')
  assert.ok(waited>=500,`it must still space captures out, waited ${waited}ms`)
  assert.ok(!(second.warnings||[]).some(w=>w.includes('rate_limited')))
})

test('a stop during the capture-interval wait is honoured before the screenshot',async()=>{
  // The wait is a gap in which the human may have hit stop. Nothing may be
  // captured after that.
  const h=harness()
  await h.run()
  let allowed=true
  setTimeout(()=>{allowed=false},50)
  await assert.rejects(h.run(candidate(),async()=>allowed),/salary_cancelled/)
  assert.equal(h.state.captures,1,'no capture after the stop')
})

test('a failure is recorded by category, not as a single unusable label',async()=>{
  // 105 jobs in one run all said "unavailable": a missing permission, a
  // timeout and an unreachable backend were indistinguishable.
  const h=harness({frame:()=>{throw new Error('Could not establish connection. Receiving end does not exist.')}})
  const result=await h.run()
  assert.equal(result.salary_text,null)
  assert.ok((result.warnings||[]).some(w=>w.includes('content_script_not_ready')),
    'the note must name the actual failure: '+JSON.stringify(result.warnings))
})
