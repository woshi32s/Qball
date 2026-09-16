// 工具框架测试(8462 管理员):工具清单 / 直接执行 / 越界防护 / 原生工具轮 / 文本协议降级 / 审批允许与拒绝 / UI 卡片
// 运行: node suites/tools_test.js
const { launch } = require('../lib/common');
const fs = require('fs');
const path = require('path');
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

async function streamEvents(message, model, onEvent, opts) {
  const o = opts || {};
  const headers = {
    'Content-Type': 'application/json',
    'X-API-Base': FAKE + '/v1',
    'X-API-Key': 'sk-fake',
    'X-API-Model': model
  };
  if (o.sid) headers['X-Qball-Session'] = o.sid;
  const body = { message, history: o.history || [] };
  if (o.mode) body.mode = o.mode;
  const r = await fetch(A + '/api/chat_stream', { method: 'POST', headers, body: JSON.stringify(body) });
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

  /* ---------- Agent 2.0:fs.edit 基础 ---------- */
  await runTool('fs.write', { path: 'edit-src.txt', content: 'alpha\nbeta\ngamma\n' });
  const e0 = await runTool('fs.edit', { path: 'edit-src.txt', find: 'nope-not-here', replace: 'x' });
  log(e0.d && e0.d.ok === false && /未找到/.test(e0.d.text), 'fs.edit: 0-match rejected', e0.d && e0.d.text.slice(0, 40));
  const eM = await runTool('fs.edit', { path: 'edit-src.txt', find: 'a', replace: 'A' });
  log(eM.d && eM.d.ok === false && /不唯一/.test(eM.d.text), 'fs.edit: multi-match rejected', eM.d && eM.d.text.slice(0, 40));
  const e1 = await runTool('fs.edit', { path: 'edit-src.txt', find: 'beta', replace: 'BETA' });
  const rdE = await runTool('fs.read', { path: 'edit-src.txt' });
  log(e1.d && e1.d.ok && /- 原/.test(e1.d.text) && rdE.d.text.indexOf('BETA') >= 0,
    'fs.edit: unique replace works', e1.d && e1.d.text.slice(0, 60));
  const seg = await runTool('fs.read', { path: 'edit-src.txt', offset: 2, limit: 1 });
  log(seg.d && seg.d.ok && seg.d.text.indexOf('BETA') >= 0 && /共 3 行/.test(seg.d.text),
    'fs.read: offset/limit window', seg.d && seg.d.text.slice(0, 50));

  /* ---------- Agent 2.0:会话防呆(读过但外部已改 → 拒写) ---------- */
  const sidG = 'guard-' + Date.now();
  await runTool('fs.write', { path: 'fib.py', content: 'a, b = 0, 1\nfor _ in range(11):\n    print(a)\n    a, b = b, a + b\n' });
  await streamEvents('读一下 fib.py', 'fake-model-alpha', null, { sid: sidG });
  await runTool('fs.write', { path: 'fib.py', content: 'a, b = 0, 1\nfor _ in range(11):\n    print(a)\n    a, b = b, a + b\n# external\n' });
  const blockedRun = await streamEvents('改一下 fib.py', 'fake-model-alpha', null, { sid: sidG });
  const blockedRes = blockedRun.events.find((e) => e.type === 'tool_result');
  log(blockedRes && blockedRes.ok === false && /已被其他改动修改/.test(blockedRes.text),
    'stale guard blocks edit after external change', blockedRes && blockedRes.text.slice(0, 46));
  await streamEvents('读一下 fib.py', 'fake-model-alpha', null, { sid: sidG });
  const passRun = await streamEvents('改一下 fib.py', 'fake-model-alpha', null, { sid: sidG });
  const passRes = passRun.events.find((e) => e.type === 'tool_result');
  log(passRes && passRes.ok === true, 're-read then edit passes', passRes && passRes.text.slice(0, 40));

  /* ---------- Agent 2.0:待办 ---------- */
  const sidT = 'todo-' + Date.now();
  const todoRun = await streamEvents('帮我建个待办', 'fake-model-alpha', null, { sid: sidT });
  const todoEv = todoRun.events.find((e) => e.type === 'todos');
  log(!!todoEv && todoEv.items.length === 3 && todoEv.items[1].status === 'in_progress',
    'todo.write emits todo card data', JSON.stringify(todoEv && todoEv.items[0]));

  /* ---------- Agent 2.0:检查点与撤销 ---------- */
  const sidC = 'ckpt-' + Date.now();
  await runTool('fs.write', { path: 'fib.py', content: 'BEFORE_MARKER\n' });
  const ckRun = await streamEvents('帮我生成 fib.py', 'fake-model-alpha', null, { sid: sidC });
  const ckEv = ckRun.events.find((e) => e.type === 'checkpoint');
  const afterWrite = await runTool('fs.read', { path: 'fib.py' });
  log(!!ckEv && /^[0-9a-f]{7,40}$/.test(ckEv.id || '') && /print\(a\)/.test(afterWrite.d.text),
    'checkpoint event + write applied', ckEv && ckEv.id.slice(0, 8));
  const sessC = await j(A + '/api/sessions/' + sidC);
  const asstC = ((sessC.d && sessC.d.messages) || []).filter((m) => m.role === 'assistant').pop();
  log(!!asstC && !!asstC.checkpoint && !!asstC.checkpoint.commit, 'session stores checkpoint');
  const rest = await j(A + '/api/checkpoints/restore', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ commit: ckEv && ckEv.id, scope: 'files' })
  });
  const afterRestore = await runTool('fs.read', { path: 'fib.py' });
  log(rest.status === 200 && rest.d.ok && afterRestore.d.text.indexOf('BEFORE_MARKER') >= 0,
    'checkpoint restore brings files back', afterRestore.d.text.slice(0, 20));

  /* ---------- Agent 2.0:计划模式(只读) ---------- */
  const planWrite = await streamEvents('帮我生成 fib.py', 'fake-model-alpha', null, { mode: 'plan' });
  const pwRes = planWrite.events.find((e) => e.type === 'tool_result');
  log(!!pwRes && pwRes.ok === false && /计划模式/.test(pwRes.text),
    'plan mode blocks write tools', pwRes && pwRes.text.slice(0, 40));
  log(!planWrite.events.some((e) => e.type === 'deliverable'), 'plan mode: no deliverable emitted');
  const planRead = await streamEvents('帮我列一下工作区', 'fake-model-alpha', null, { mode: 'plan' });
  const prCall = planRead.events.find((e) => e.type === 'tool_call');
  log(!!prCall && prCall.name === 'fs.list', 'plan mode allows read-only tools', prCall && prCall.name);

  /* ---------- Agent 2.0:项目规则文件注入 ---------- */
  await runTool('fs.write', { path: 'QBALL.md', content: '# 项目规则\nQBALL_RULE_MARKER 测试标记必须被注入\n' });
  const ruleRun = await streamEvents('请用一句话回复我', 'fake-model-alpha');
  const ruleText = ruleRun.events.filter((e) => e.type === 'text').map((e) => e.delta).join('');
  log(ruleText.indexOf('RULE_SEEN') >= 0, 'project rules injected into system', ruleText.slice(0, 30));
  await runTool('fs.write', { path: 'QBALL.md', content: '' });

  /* ---------- Agent 2.0:上下文自动压缩 ---------- */
  const sidZ = 'compact-' + Date.now();
  const bigHistory = [];
  for (let i = 0; i < 24; i++) {
    bigHistory.push({ role: i % 2 ? 'assistant' : 'user', content: '历史消息' + i + ' ' + 'x'.repeat(420) });
  }
  const cRun = await streamEvents('继续吧', 'fake-model-alpha', null, { sid: sidZ, history: bigHistory });
  const sessFile = path.join(__dirname, '..', '.runtime', 'home_8462', 'data', 'sessions', sidZ + '.jsonl');
  const sessText = fs.existsSync(sessFile) ? fs.readFileSync(sessFile, 'utf8') : '';
  log(/done/.test(types(cRun.events)) && /"role": "summary"/.test(sessText),
    'long history compressed into summary record', sessText.split('\n').filter((l) => /summary/.test(l)).length);

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

  /* ---------- UI:撤销按钮 / 待办卡片 / 计划开关 ---------- */
  const undoShown = await page.waitForFunction(() => !!document.querySelector('.undo-btn'), null, { timeout: 20000 })
    .then(() => true).catch(() => false);
  log(undoShown, 'UI: undo button rendered for checkpointed turn');

  await page.waitForFunction(() => window.__demo.sending === false, null, { timeout: 30000 });
  await page.fill('#chat-input', '帮我建个待办');
  await page.click('#chat-send');
  const todoShown = await page.waitForFunction(() => {
    const c = document.getElementById('todo-card');
    return c && c.querySelectorAll('.todo-item').length === 3;
  }, null, { timeout: 30000 }).then(() => true).catch(() => false);
  log(todoShown, 'UI: todo card appears with items');

  const planInfo = await page.evaluate(() => {
    const b = document.getElementById('chat-plan');
    if (!b) return null;
    b.click();
    const active = b.classList.contains('active');
    b.click();
    return { exists: true, toggles: active && !b.classList.contains('active') };
  });
  log(planInfo && planInfo.toggles, 'UI: plan toggle switches on/off');
  await browser.close();

  console.log(fails === 0 ? 'ALL PASS' : fails + ' FAILURES');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
