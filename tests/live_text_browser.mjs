import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const [origin, runtime, output] = process.argv.slice(2);
const { createSessionCore } = await import(pathToFileURL(runtime));
const core = createSessionCore({headless:true,transport:'pipe',stealth:false,userDataDir:`${output}/profile`,downloadDir:`${output}/downloads`});
const send = (method, params={}) => core.browserSession.cdpClient.send(method,params,core.browserSession.sessionId);
async function evaluate(expression) {
  const r = await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  assert.ok(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));
  return r.result.value;
}
async function wait(expression) {
  for(let i=0;i<100;i++) {
    if(await evaluate(expression)) {
      // Wait through layout/ResizeObserver and the resulting scroll event;
      // a transient pre-paint position is not proof that following settled.
      await evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
      if(await evaluate(expression)) return;
    }
    await new Promise(r=>setTimeout(r,30));
  }
  throw Error('Timed out: '+expression+' '+JSON.stringify(await evaluate(`(()=>{const e=document.querySelector('.conversation');return {top:e.scrollTop,height:e.clientHeight,content:e.scrollHeight,latest:!!document.querySelector('.jump-latest')}})()`)));
}
const text = `document.querySelector('.conversation .message.assistant .markdown')?.textContent`;
const atBottom = `(()=>{const el=document.querySelector('.conversation');return el.scrollHeight-el.scrollTop-el.clientHeight<2})()`;
try {
  await core.start();
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`window.__streams=[];const Original=window.EventSource;window.EventSource=class extends Original{constructor(...args){super(...args);window.__streams.push(this)}};`});
  await core.callTool('navigate',{url:origin});
  await wait(`${text}?.trim()==='Persisted'`);
  // Readiness via a real SSE response, not a fixed delay after loading history.
  await wait(`window.__streams.some(s=>s.url.includes('text=live')&&s.readyState===1)`);
  await evaluate(`fetch('/_test/fragment',{method:'POST'})`);
  await wait(`${text}==='Persisted live 🌍!'`);
  assert.equal(await evaluate(`fetch('/_test/saved').then(r=>r.json()).then(r=>r.text)`),'Persisted ','Live content must render before it is saved');
  await evaluate(`fetch('/_test/grow',{method:'POST'})`);
  await wait(`${text}?.includes('More streamed text.')`);
  await wait(atBottom);
  const heightBefore = await evaluate(`document.querySelector('.conversation').clientHeight`);
  await evaluate(`fetch('/_test/activity',{method:'POST'})`);
  await wait(`document.querySelector('.activity-toggle')?.getAttribute('aria-expanded')==='true'`);
  await wait(`document.querySelector('.conversation').clientHeight<${heightBefore}`);
  await wait(atBottom);
  await evaluate(`document.querySelector('.activity-toggle').click()`);
  await wait(`document.querySelector('.activity-toggle').getAttribute('aria-expanded')==='false'`);
  await wait(atBottom);
  await evaluate(`document.querySelector('.activity-toggle').click()`);
  await wait(`document.querySelector('.activity-toggle').getAttribute('aria-expanded')==='true'`);
  await wait(atBottom);
  // Reading older text intentionally suspends following, including live-only
  // growth and viewport changes. The explicit Latest button restores it.
  await evaluate(`document.querySelector('.conversation').scrollTop-=200`);
  await wait(`!!document.querySelector('.jump-latest')`);
  const readingTop = await evaluate(`document.querySelector('.conversation').scrollTop`);
  await evaluate(`fetch('/_test/grow',{method:'POST'})`);
  await wait(`${text}.split('More streamed text.').length===81`);
  await evaluate(`document.querySelector('.activity-toggle').click()`);
  await wait(`document.querySelector('.activity-toggle').getAttribute('aria-expanded')==='false'`);
  assert.ok(Math.abs(await evaluate(`document.querySelector('.conversation').scrollTop`)-readingTop)<2,'Resizing must preserve the position when reading older text');
  await evaluate(`document.querySelector('.jump-latest').click()`);
  await wait(atBottom);
  const expected = await evaluate(text);
  await evaluate(`fetch('/_test/finish',{method:'POST'})`);
  await wait(`!document.querySelector('.conversation .stream-spinner')`);
  assert.equal(await evaluate(text),expected);
  assert.equal(await evaluate(`document.querySelectorAll('.conversation .message.assistant').length`),1);
  await core.callTool('navigate',{url:origin});
  await wait(`${text}===${JSON.stringify(expected)}`);
  assert.equal(await evaluate(`document.querySelectorAll('.conversation .message.assistant').length`),1);
  await wait(atBottom);
  console.log('Live rendering, activity resize following, manual scroll, completion and reload passed');
} finally { await core.close(); }
