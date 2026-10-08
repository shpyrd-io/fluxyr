import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const [origin, runtime, output] = process.argv.slice(2);
const { createSessionCore } = await import(pathToFileURL(runtime));
const core = createSessionCore({headless: true, transport: 'pipe', stealth: false,
  userDataDir: `${output}/profile`, downloadDir: `${output}/downloads`});
const send = (method, params = {}) => core.browserSession.cdpClient.send(method, params, core.browserSession.sessionId);
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  assert.ok(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function wait(expression) {
  for (let i = 0; i < 100; i++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error(`Timed out: ${expression}`);
}
const scheme = value => send('Emulation.setEmulatedMedia', {features: [{name: 'prefers-color-scheme', value}]});
const viewport = (width, height) => send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: false});
const theme = value => wait(`document.documentElement.dataset.theme === ${JSON.stringify(value)}`);
async function choose(label) {
  await evaluate(`document.querySelector('.theme-trigger').click()`);
  await wait(`!!document.querySelector('.theme-menu [role="option"]')`);
  await evaluate(`Array.from(document.querySelectorAll('.theme-menu [role="option"]')).find(e => e.textContent.trim() === ${JSON.stringify(label)}).click()`);
  await wait(`document.querySelector('.theme-trigger').getAttribute('aria-label') === ${JSON.stringify('Theme: ' + label)}`);
}
async function screenshot(name) {
  await evaluate('document.fonts.ready.then(() => true)');
  // Wait for the existing 150 ms control transitions to finish.
  await new Promise(resolve => setTimeout(resolve, 200));
  const {data} = await send('Page.captureScreenshot', {format: 'png'});
  await writeFile(`${output}/theme-${name}.png`, Buffer.from(data, 'base64'));
}
try {
  await core.start();
  await viewport(1440, 1000);
  await scheme('light');
  await core.callTool('navigate', {url: origin});
  await wait(`!!document.querySelector('.theme-trigger')`);
  await theme('light');
  assert.equal(await evaluate(`document.querySelector('.theme-trigger').getAttribute('aria-label')`), 'Theme: System');
  await screenshot('light');
  await scheme('dark');
  await theme('dark');
  await screenshot('dark');
  await choose('Light');
  await theme('light');
  assert.equal(await evaluate(`localStorage.getItem('fluxyr-theme')`), 'light');
  await send('Page.reload');
  await wait(`document.querySelector('.theme-trigger')?.getAttribute('aria-label') === 'Theme: Light'`);
  await theme('light');
  await scheme('light');
  await choose('Dark');
  await theme('dark');
  await choose('System');
  await theme('light');
  // The same shared surfaces apply to forms and popovers, not just the landing page.
  await evaluate(`Array.from(document.querySelectorAll('nav button')).find(e => e.textContent.includes('Routines')).click()`);
  await wait(`Array.from(document.querySelectorAll('button')).some(e => /Add routine/i.test(e.textContent))`);
  await evaluate(`Array.from(document.querySelectorAll('button')).find(e => /Add routine/i.test(e.textContent)).click()`);
  await wait(`!!document.querySelector('[role="dialog"]')`);
  await screenshot('light-form');
  await send('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Escape', code: 'Escape'});
  await send('Input.dispatchKeyEvent', {type: 'keyUp', key: 'Escape', code: 'Escape'});
  await wait(`!document.querySelector('[role="dialog"]')`);
  await viewport(390, 844);
  await screenshot('mobile');
  assert.ok(await evaluate(`(() => { const r = document.querySelector('.theme-trigger').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0; })()`), 'Theme control must remain visible on mobile');
  // Keyboard selection remains available without a mouse.
  await evaluate(`document.querySelector('.theme-trigger').focus()`);
  for (const key of ['Enter', 'Home', 'ArrowDown', 'Enter']) {
    await send('Input.dispatchKeyEvent', {type: 'keyDown', key});
    await send('Input.dispatchKeyEvent', {type: 'keyUp', key});
    // Radix moves focus in a deferred callback after keyboard navigation.
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  await theme('dark');
  console.log('Theme: OS changes, explicit override, reload, keyboard and mobile checks passed');
} finally {
  await core.close();
}
