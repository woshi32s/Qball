// 工具框架测试(8462 管理员):工具清单 / 直接执行 / 越界防护 / 原生工具轮 / 文本协议降级 / 审批允许与拒绝 / UI 卡片
// 运行: node suites/tools_test.js
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

function runTool(name, args) {
  return j(A + '/api/tools/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, args })
  });
}

async function streamEvents(message, model, onEvent) {
  const r = await fetch(A + '/api/chat_stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Base': FAKE + '/v1',
      'X-API-Key': 'sk-fake',
      'X-API-Model': model
    },
    body: JSON.stringify({ message, history: [] })
  });
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  const events = [];
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = chunk.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      try {
        const ev = JSON.parse(line.slice(5).trim());
        events.push(ev);
        if (onEvent) await onEvent(ev);
      } catch (e) {}
    }
  }
  return { status: r.status, events };
}

const types = (events) => events.map((e) => e.type).join(',');

(async () => {
  /* ---------- 工具清单 ---------- */
  const list = await j(A + '/api/tools');
  const names = (list.d && list.d.tools || []).map((t) => t.name);
  log(list.status === 200 && names.length >= 10, 'tools listed', names.length + ' tools');
  log(names.indexOf('shell.run') >= 0 && names.indexOf('web.search') >= 0, 'key tools present', names.join(','));

  /* ---------- 文件读写 ---------- */
  const w = await runTool('fs.write', { path: 'tests-work/hello.txt', content: '你好,这是工具写的文件' });
  log(w.status === 200 && w.d.ok, 'fs.write works', w.d && w.d.text);
  const r = await runTool('fs.read', { path: 'tests-work/hello.txt' });
  log(r.d && r.d.ok && r.d.text.indexOf('工具写的文件') >= 0, 'fs.read roundtrip', r.d && r.d.text.slice(0, 30));
  const ls = await runTool('fs.list', { path: 'tests-work' });
  log(ls.d && ls.d.ok && ls.d.text.indexOf('hello.txt') >= 0, 'fs.list shows file', ls.d && ls.d.text.replace(/\n/g, ' | '));

  /* ---------- 越界防护 ---------- */
  const evil = await runTool('fs.read', { path: '../../../config.json' });
  log(evil.d && evil.d.ok === false && /超出工作区/.test(evil.d.text), 'path escape blocked', evil.d && evil.d.text);
  const evilWrite = await runTool('fs.write', { path: '../evil.txt', content: 'x' });
  log(evilWrite.d && evilWrite.d.ok === false, 'write escape blocked');

  /* ---------- 笔记 ---------- */
  const note = await runTool('notes.add', { text: '测试笔记:工具框架已就绪' });
  log(note.d && note.d.ok, 'notes.add works', note.d && note.d.text);
  const noteList = await runTool('notes.list', {});
  log(noteList.d && noteList.d.ok && /\.md/.test(noteList.d.text), 'notes.list works');

  /* ---------- 原生工具轮(fake-model-alpha) ---------- */
  const nativeRun = await streamEvents('帮我列一下工作区', 'fake-model-alpha');
  const nTypes = types(nativeRun.events);
  const nCall = nativeRun.events.find((e) => e.type === 'tool_call');
  const nResult = nativeRun.events.find((e) => e.type === 'tool_result');
  log(/tool_call/.test(nTypes) && /tool_result/.test(nTypes) && /done/.test(nTypes), 'native tool round', nTypes.slice(0, 120));
  log(nCall && nCall.name === 'fs.list', 'native tool name', nCall && nCall.name);
  log(nResult && nResult.ok && nResult.text.indexOf('tests-work/') >= 0, 'native tool result has dir', nResult && nResult.text.slice(0, 40));
  const nText = nativeRun.events.filter((e) => e.type === 'text').map((e) => e.delta).join('');
  log(nText.length > 0, 'final answer after tool round', nText.slice(0, 40));

  /* ---------- 产物卡片:fs.write 轮自动产生 deliverable ---------- */
  const dlvRun = await streamEvents('帮我生成 fib.py', 'fake-model-alpha');
  const dlv = dlvRun.events.find((e) => e.type === 'deliverable');
  log(!!dlv && dlv.path === 'fib.py' && dlv.chars > 0, 'deliverable event emitted', JSON.stringify(dlv));
  const dlvListRun = await streamEvents('列一下工作区', 'fake-model-alpha');
  log(!dlvListRun.events.some((e) => e.type === 'deliverable'), 'no deliverable for read-only round');

  const sid = 'test-dlv-' + Date.now();
  const sres = await fetch(A + '/api/chat_stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Base': FAKE + '/v1',
      'X-API-Key': 'sk-fake',
      'X-API-Model': 'fake-model-alpha',
      'X-Qball-Session': sid
    },
    body: JSON.stringify({ message: '帮我生成 fib.py', history: [] })
  });
  await sres.text();
  const sess = await j(A + '/api/sessions/' + sid);
  const asst = ((sess.d && sess.d.messages) || []).filter((m) => m.role === 'assistant').pop();
  log(!!asst && (asst.deliverables || []).some((d) => d.path === 'fib.py'),
    'session stores deliverables', JSON.stringify(asst && asst.deliverables));

  /* ---------- /api/open 校验 ---------- */
  const oBad = await j(A + '/api/open', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: '../outside.txt' })
  });
  log(oBad.status === 400, 'open: escape blocked', oBad.status);
  const oMissing = await j(A + '/api/open', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: 'nope-missing.txt' })
  });
  log(oMissing.status === 404, 'open: missing -> 404', oMissing.status);
  const oPath = await j(A + '/api/open', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: '.', action: 'path' })
  });
  log(oPath.status === 200 && /workspace/.test(oPath.d.path || ''), 'open: path mode', oPath.d && oPath.d.path);

  /* ---------- 文本协议降级(fake-model-notools) ---------- */
  const textRun = await streamEvents('帮我列一下工作区', 'fake-model-notools');
  const tTypes = types(textRun.events);
  const tCall = textRun.events.find((e) => e.type === 'tool_call');
  const tResult = textRun.events.find((e) => e.type === 'tool_result');
  log(/tool_call/.test(tTypes) && /tool_result/.test(tTypes), 'text-protocol fallback tool round', tTypes.slice(0, 120));
  log(tCall && tCall.name === 'fs.list' && tResult && tResult.ok, 'fallback executed fs.list', tCall && tCall.name);

  /* ---------- 审批:允许 ---------- */
  let allowSeen = false;
  const allowRun = await streamEvents('跑一条命令', 'fake-model-alpha', async (ev) => {
    if (ev.type === 'approval_required') {
      allowSeen = true;
      await j(A + '/api/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: ev.id, allow: true })
      });
    }
  });
  const allowResult = allowRun.events.find((e) => e.type === 'tool_result');
  log(allowSeen, 'approval requested for shell.run');
  log(allowResult && allowResult.ok && /hi-from-shell/.test(allowResult.text), 'approved shell ran', allowResult && allowResult.text.slice(0, 60));

  /* ---------- 审批:拒绝 ---------- */
  const denyRun = await streamEvents('跑一条命令', 'fake-model-alpha', async (ev) => {
    if (ev.type === 'approval_required') {
      await j(A + '/api/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: ev.id, allow: false })
      });
    }
  });
  const denyResult = denyRun.events.find((e) => e.type === 'tool_result');
  log(denyResult && denyResult.ok === false && /拒绝/.test(denyResult.text), 'denied shell blocked', denyResult && denyResult.text);
  log(/done/.test(types(denyRun.events)), 'done after denial');

  /* ---------- 关闭工具 ---------- */
  await j(A + '/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tools_enabled: false })
  });
  const offRun = await streamEvents('帮我列一下工作区', 'fake-model-alpha');
  log(!/tool_call/.test(types(offRun.events)), 'tools disabled -> no tool calls', types(offRun.events).slice(0, 60));
  await j(A + '/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tools_enabled: true })
  });

  /* ---------- UI:工具卡片与审批条 ---------- */
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

  await page.fill('#chat-input', '帮我列一下工作区');
  await page.click('#chat-send');
  await page.waitForFunction(() => document.querySelectorAll('.tool-card').length >= 1, null, { timeout: 20000 });
  log(true, 'UI: tool card appears on tool_call');
  await page.waitForFunction(() => document.querySelectorAll('.tool-card.done').length >= 1, null, { timeout: 25000 });
  const card = await page.evaluate(() => {
    const c = document.querySelector('.tool-card.done');
    return { name: c.querySelector('.tool-name').textContent, body: c.querySelector('.tool-body').textContent.slice(0, 60) };
  });
  log(card.name === 'fs.list' && /tests-work\//.test(card.body), 'UI: tool card shows result', JSON.stringify(card));

  await page.waitForFunction(() => window.__demo.sending === false, null, { timeout: 30000 });
  await page.fill('#chat-input', '跑一条命令');
  await page.click('#chat-send');
  await page.waitForFunction(() => !!document.querySelector('.tool-approval'), null, { timeout: 20000 });
  log(true, 'UI: approval bar shown for shell.run');
  await page.click('.tool-approval .mini-btn.primary', { force: true });
  const approved = await page.waitForFunction(() => {
    return Array.from(document.querySelectorAll('.tool-card.done .tool-body'))
      .some((b) => b.textContent.indexOf('hi-from-shell') >= 0);
  }, null, { timeout: 30000 }).then(() => true).catch(() => false);
  log(approved, 'UI: approve click executes shell and shows output');

  /* ---------- UI:产物卡片 ---------- */
  await page.waitForFunction(() => window.__demo.sending === false, null, { timeout: 30000 });
  await page.fill('#chat-input', '帮我生成 fib.py');
  await page.click('#chat-send');
  const dlvShown = await page.waitForFunction(() => {
    const c = document.querySelector('.dlv-card');
    return c && c.dataset.path === 'fib.py';
  }, null, { timeout: 30000 }).then(() => true).catch(() => false);
  const dlvInfo = await page.evaluate(() => {
    const c = document.querySelector('.dlv-card');
    if (!c) return null;
    const btns = Array.from(c.querySelectorAll('button')).map((b) => b.textContent);
    return { name: c.querySelector('.dlv-name').textContent, path: c.querySelector('.dlv-path').textContent, btns };
  });
  log(dlvShown && dlvInfo && dlvInfo.name === 'fib.py' &&
    dlvInfo.btns.indexOf('打开') >= 0 && dlvInfo.btns.indexOf('定位') >= 0,
    'UI: deliverable card with open/reveal buttons', JSON.stringify(dlvInfo));
  await browser.close();

  console.log(fails === 0 ? 'ALL PASS' : fails + ' FAILURES');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
