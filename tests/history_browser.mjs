import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const [origin, runtime, output] = process.argv.slice(2);
const { createSessionCore } = await import(pathToFileURL(runtime));
const core = createSessionCore({headless: true, transport: 'pipe', stealth: false, userDataDir: `${output}/profile`, downloadDir: `${output}/downloads`});
const send = (method, params = {}) => core.browserSession.cdpClient.send(method, params, core.browserSession.sessionId);
async function evaluate(expression) {
  const r = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  assert.ok(!r.exceptionDetails, JSON.stringify(r.exceptionDetails));
  return r.result.value;
}
async function wait(expression) {
  for(let i=0;i<150;i++) {
    if(await evaluate(expression)) return;
    await new Promise(r => setTimeout(r, 50));
  }
  throw Error('Timed out: '+expression);
}
const historyRequests = `performance.getEntriesByType('resource').filter(r=>r.name.includes('/history'))`;
try {
  await core.start();
  await send('Emulation.setDeviceMetricsOverride', {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await core.callTool('navigate',{url:origin});
  await wait(`document.querySelector('.conversation')?.textContent.includes('Answer 59: finished.')`);
  assert.equal(await evaluate(`${historyRequests}.filter(r=>r.name.includes('before=')).length`),0,'Opening must not eagerly fetch old pages');
  assert.ok(!(await evaluate(`document.body.textContent.includes('DETAIL-SENTINEL')`)),'Tool body must be lazy');
  await evaluate(`Array.from(document.querySelectorAll('.tool-event summary')).at(-1).click()`);
  await wait(`document.body.textContent.includes('DETAIL-SENTINEL')`);
  await evaluate(`Array.from(document.querySelectorAll('.tool-event summary')).at(-1).click()`);
  await evaluate(`fetch('/_test/turn',{method:'POST'})`);
  await wait(`document.querySelector('.conversation')?.textContent.includes('Answer 60: finished.')`);
  assert.equal(await evaluate(`Array.from(document.querySelectorAll('.conversation .message.assistant')).filter(e=>e.textContent.includes('Answer 60: finished.')).length`),1,'Live final response must not duplicate');
  // Windowing first reveals cached rows. Only reaching their oldest edge fetches.
  for(let i=0;i<20;i++) {
    await evaluate(`document.querySelector('.conversation').scrollTop=0`);
    await new Promise(r=>setTimeout(r,100));
    if(await evaluate(`${historyRequests}.some(r=>r.name.includes('before='))`)) break;
  }
  await wait(`${historyRequests}.some(r=>r.name.includes('before='))`);
  await wait(`!document.querySelector('.conversation').textContent.includes('Loading earlier')`);
  const state = await evaluate(`(()=>{const e=document.querySelector('.conversation');return {top:e.scrollTop,rows:e.querySelectorAll('[data-history-id]').length};})()`);
  assert.ok(state.rows<=90,'DOM window must remain bounded');
  assert.ok(state.top>0,'Prepending history must preserve the visible anchor');
  console.log('History: lazy bootstrap, tool details, live continuation, older-page loading and scroll anchor passed');
} finally { await core.close(); }
