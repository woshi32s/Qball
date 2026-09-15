// 会话落盘测试:聊天自动记录 / 列表 / 详情 / UI 历史会话载入
// 运行: node suites/sessions_test.js
const { launch } = require('../lib/common');
const A = process.env.EB_ADMIN || 'http://127.0.0.1:8462';
const FAKE = process.env.EB_FAKE || 'http://127.0.0.1:8484';
let fails = 0;
function log(ok, name, extra) {
  console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + (extra !== undefined ? '  (' + extra + ')' : ''));
  if (!ok) fails++;
}

async function j(url, opts) {
  try {
    const r = await fetch(url, opts);
    let d = null;
    try { d = await r.json(); } catch (e) {}
    return { status: r.status, d };
  } catch (e) {
    return { status: 0, d: null, err: String(e) };
  }
}

(async () => {
  const sid = 'sess-test-0001';
  const headers = {
    'Content-Type': 'application/json',
    'X-API-Base': FAKE + '/v1',
    'X-API-Key': 'sk-fake',
    'X-API-Model': 'fake-model-alpha',
    'X-Qball-Session': sid
  };
  const r = await fetch(A + '/api/chat_stream', {
    method: 'POST',
    headers,
    body: JSON.stringify({ message: '你好,这是会话落盘测试', history: [] })
  });
  const text = await r.text();
  log(r.status === 200 && /done/.test(text), 'chat stream completed for session');

  const list = await j(A + '/api/sessions');
  const item = ((list.d && list.d.sessions) || []).find((s) => s.id === sid);
  log(!!item && item.messages >= 2, 'session recorded with 2+ messages',
    JSON.stringify(item && { messages: item.messages, preview: item.preview }));

  const detail = await j(A + '/api/sessions/' + sid);
  const msgs = (detail.d && detail.d.messages) || [];
  const user = msgs.find((m) => m.role === 'user');
  const bot = msgs.find((m) => m.role === 'assistant');
  log(user && user.content.indexOf('会话落盘测试') >= 0, 'user message stored');
  log(!!bot && bot.content.length > 0 && !!bot.emotionId, 'assistant message stored with emotion',
    bot && (bot.content.slice(0, 18) + ' #' + bot.emotionId));

  log((await j(A + '/api/sessions/no-such-session-xyz')).status === 404, 'missing session -> 404');

  /* ---------- UI:设置里的历史会话列表 + 载入 ---------- */
  const browser = await launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  page.on('pageerror', (e) => console.log('PAGE-EXC', e.message));
  await page.addInitScript((fakeBase) => {
    try {
      localStorage.setItem('qball.muted', '1');
      localStorage.setItem('qball.api.v1', JSON.stringify({ base: fakeBase, key: 'sk-fake', model: 'fake-model-alpha' }));
    } catch (e) {}
  }, FAKE + '/v1');
  await page.goto(A + '/qball.html', { waitUntil: 'load' });
  await page.mouse.click(640, 430);
  await page.waitForTimeout(2500);

  await page.fill('#chat-input', '界面会话测试');
  await page.click('#chat-send');
  await page.waitForFunction(() => window.__demo.sending === false, null, { timeout: 30000 });
  await page.waitForTimeout(800);

  await page.click('#settings-handle');
  await page.waitForFunction(() => document.querySelectorAll('#session-list .session-item').length >= 1, null, { timeout: 10000 });
  const first = await page.evaluate(() => {
    const it = document.querySelector('#session-list .session-item');
    return { prev: it.querySelector('.s-prev').textContent, btn: (it.querySelector('button') || {}).textContent };
  });
  log(first.btn === '载入', 'UI: session list shown in settings', JSON.stringify(first));

  const before = await page.evaluate(() => window.__demo ? document.querySelectorAll('#chat-log .msg').length : 0);
  await page.click('#session-list .session-item button');
  await page.waitForFunction(() => !document.getElementById('settings-modal').classList.contains('open'), null, { timeout: 8000 });
  await page.waitForFunction((n) => document.querySelectorAll('#chat-log .msg').length >= 2, before, { timeout: 10000 }).catch(() => {});
  const loaded = await page.evaluate(() => document.querySelectorAll('#chat-log .msg').length);
  log(loaded >= 2, 'UI: session loaded into chat', loaded);
  await browser.close();

  console.log(fails === 0 ? 'ALL PASS' : fails + ' FAILURES');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
