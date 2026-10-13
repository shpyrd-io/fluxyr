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
    if(await evaluate(expression)) return;
    await new Promise(r=>setTimeout(r,30));
  }
  throw Error('Timed out: '+expression);
}
const text = `document.querySelector('.conversation .message.assistant .markdown')?.textContent`;
try {
  await core.start();
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`window.__streams=[];const Original=window.EventSource;window.EventSource=class extends Original{constructor(...args){super(...args);window.__streams.push(this)}};`});
  await core.callTool('navigate',{url:origin});
  await wait(`${text}?.trim()==='Persisted'`);
  // Readiness via a real SSE response, not a fixed delay after loading history.
  await wait(`window.__streams.some(s=>s.url.includes('text=live')&&s.readyState===1)`);
  await evaluate(`fetch('/_test/fragment',{method:'POST'})`);
  await wait(`${text}==='Persisted live 🌍!'`);
  assert.equal(await evaluate(`fetch('/_test/saved').then(r=>r.json()).then(r=>r.text)`),'Persisted ','Live content must render before it is saved');
  await evaluate(`fetch('/_test/finish',{method:'POST'})`);
  await wait(`!document.querySelector('.conversation .stream-spinner')`);
  assert.equal(await evaluate(text),'Persisted live 🌍!');
  assert.equal(await evaluate(`document.querySelectorAll('.conversation .message.assistant').length`),1);
  await core.callTool('navigate',{url:origin});
  await wait(`${text}==='Persisted live 🌍!'`);
  assert.equal(await evaluate(`document.querySelectorAll('.conversation .message.assistant').length`),1);
  console.log('Live rendering before commit, Unicode reconciliation, completion and reload passed');
} finally { await core.close(); }
