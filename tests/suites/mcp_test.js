// MCP 扩展测试(8462):配置加载 / 工具命名空间 / 调用 / 错误传播 / 审批与自动放行 / 离线容错
// 运行: node suites/mcp_test.js
const fs = require('fs');
const path = require('path');
const A = process.env.EB_ADMIN || 'http://127.0.0.1:8462';
const FAKE = process.env.EB_FAKE || 'http://127.0.0.1:8484';
const HOME = path.join(__dirname, '..', '.runtime', 'home_8462');
const MCP_JSON = path.join(HOME, 'mcp.json');
const FIXTURE = path.join(__dirname, '..', 'fixtures', 'fake_mcp_server.py');
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
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, args })
  });
}
async function streamEvents(message, onEvent, sid) {
  const headers = {
    'Content-Type': 'application/json',
    'X-API-Base': FAKE + '/v1', 'X-API-Key': 'sk-fake', 'X-API-Model': 'fake-model-alpha'
  };
  if (sid) headers['X-Qball-Session'] = sid;
  const r = await fetch(A + '/api/chat_stream', {
    method: 'POST', headers, body: JSON.stringify({ message, history: [] })
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
  return events;
}
function writeMcp(cfg) {
  fs.mkdirSync(HOME, { recursive: true });
  fs.writeFileSync(MCP_JSON, JSON.stringify(cfg, null, 2), 'utf8');
}

(async () => {
  /* ---------- 配置加载与工具发现 ---------- */
  writeMcp({
    servers: {
      fake: { command: 'python', args: [FIXTURE] },
      broken: { command: 'definitely-not-a-real-command-xyz', args: [] }
    }
  });
  const reload = await j(A + '/api/mcp/reload', { method: 'POST' });
  log(reload.status === 200 && reload.d.available && reload.d.tools >= 3,
    'mcp reload discovers tools', JSON.stringify({ tools: reload.d && reload.d.tools }));
  const st = await j(A + '/api/mcp/status');
  const fakeSrv = (st.d.servers || []).find((s) => s.name === 'fake');
  const brokenSrv = (st.d.servers || []).find((s) => s.name === 'broken');
  log(!!fakeSrv && fakeSrv.alive && fakeSrv.tools === 3, 'fake server alive with 3 tools');
  log(!!brokenSrv && !brokenSrv.alive && !!brokenSrv.error, 'broken server: offline with error', brokenSrv && brokenSrv.error);

  const list = await j(A + '/api/tools');
  const names = (list.d.tools || []).map((t) => t.name);
  log(names.indexOf('mcp.fake.echo') >= 0 && names.indexOf('mcp.fake.add') >= 0,
    'mcp tools exposed in tool list', names.filter((n) => n.indexOf('mcp.') === 0).join(','));

  /* ---------- 审批默认:直连被拒 + 对话内被询问且可拒绝 ---------- */
  const gated = await runTool('mcp.fake.echo', { text: 'x' });
  log(gated.status === 403, 'approval-gated mcp tool blocked from direct run', gated.status);
  let asked = false;
  const sid1 = 'mcp-appr-' + Date.now();
  const run1 = await streamEvents('帮我调用 echo 工具', async (ev) => {
    if (ev.type === 'approval_required' && ev.name === 'mcp.fake.echo') {
      asked = true;
      await j(A + '/api/approve', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: ev.id, allow: false })
      });
    }
  }, sid1);
  const r1 = run1.find((e) => e.type === 'tool_result');
  log(asked && r1 && r1.ok === false, 'mcp tool asks approval in chat; deny blocks', r1 && r1.text.slice(0, 30));

  /* ---------- auto_approve:直连放行 + 调用与错误传播 + 对话不再询问 ---------- */
  writeMcp({ servers: { fake: { command: 'python', args: [FIXTURE], auto_approve: ['echo', 'add', 'boom'] } } });
  await j(A + '/api/mcp/reload', { method: 'POST' });
  const echo = await runTool('mcp.fake.echo', { text: '你好-MCP' });
  log(echo.d && echo.d.ok && echo.d.text.indexOf('echo:你好-MCP') >= 0, 'mcp echo call works', echo.d && echo.d.text);
  const add = await runTool('mcp.fake.add', { a: 2, b: 3 });
  log(add.d && add.d.ok && /(^|\D)5(\D|$)/.test(add.d.text), 'mcp add call works', add.d && add.d.text);
  const boom = await runTool('mcp.fake.boom', {});
  log(boom.d && boom.d.ok === false && /intentional failure/.test(boom.d.text),
    'mcp error propagates', boom.d && boom.d.text.slice(0, 40));
  let asked2 = false;
  const run2 = await streamEvents('帮我调用 echo 工具', (ev) => {
    if (ev.type === 'approval_required') asked2 = true;
  }, 'mcp-auto-' + Date.now());
  const r2 = run2.find((e) => e.type === 'tool_result');
  log(!asked2 && r2 && r2.ok === true && /echo:hello-mcp/.test(r2.text),
    'auto_approve skips prompt and runs', r2 && r2.text.slice(0, 40));

  /* ---------- 清理:关闭 MCP(避免影响后续套件) ---------- */
  writeMcp({ servers: {} });
  const cleared = await j(A + '/api/mcp/reload', { method: 'POST' });
  const list2 = await j(A + '/api/tools');
  const names2 = (list2.d.tools || []).map((t) => t.name);
  log(cleared.status === 200 && !names2.some((n) => n.indexOf('mcp.') === 0),
    'mcp disabled -> tools removed', names2.length);

  console.log(fails === 0 ? 'ALL PASS' : fails + ' FAILURES');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
