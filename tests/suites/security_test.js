// 安全与限流测试(8463 = 访问码+限流+每日额度;8462 = 本机管理员/开放模式)
// 运行: node suites/security_test.js
const B = process.env.EB_CODE || 'http://127.0.0.1:8463';
const A = process.env.EB_ADMIN || 'http://127.0.0.1:8462';
const FAKE = process.env.EB_FAKE || 'http://127.0.0.1:8484';
const CODE = 'testcode123';
const ADMIN = 'admintoken456';
let n = 0;
let fails = 0;
function log(ok, name, extra) {
  console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + (extra !== undefined ? '  (' + extra + ')' : ''));
  if (!ok) fails++;
}
function ip(n2) { return { 'X-Forwarded-For': '198.51.100.' + n2 }; }

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

async function post(url, body, headers) {
  return j(url, {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, headers),
    body: JSON.stringify(body || {})
  });
}

async function streamText(url, headers) {
  const r = await fetch(url, {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, headers),
    body: JSON.stringify({ message: '你好', history: [] })
  });
  const text = await r.text();
  return { status: r.status, text };
}

(async () => {
  /* ---------- 静态文件白名单 ---------- */
  for (const p of ['/config.json', '/.env', '/server.py', '/DEPLOY.md']) {
    const r = await j(B + p, { headers: ip(1) });
    log(r.status === 404, 'blocked: ' + p, r.status);
  }
  log((await j(B + '/qball.html', { headers: ip(1) })).status === 200, 'page served');
  log((await j(B + '/js/engine.js', { headers: ip(1) })).status === 200, 'js served');
  log((await j(B + '/manifest.webmanifest', { headers: ip(1) })).status === 200, 'manifest served');
  log((await j(A + '/config.json', { headers: ip(1) })).status === 404, 'A: config.json blocked too');

  /* ---------- 访问码 ---------- */
  const h1 = await j(B + '/api/health', { headers: ip(2) });
  log(h1.d && h1.d.auth_required === true && h1.d.admin === false, 'visitor health',
    JSON.stringify({ auth: h1.d.auth_required, admin: h1.d.admin }));

  log((await post(B + '/api/chat', { message: 'hi' }, ip(3))).status === 401, 'chat without code -> 401');
  log((await post(B + '/api/chat', { message: 'hi' }, Object.assign(ip(4), { 'X-Access-Code': 'wrong' }))).status === 401,
    'chat with wrong code -> 401');

  const s = await streamText(B + '/api/chat_stream',
    Object.assign(ip(5), { 'X-Access-Code': CODE }));
  log(s.status === 200 && s.text.indexOf('"type": "emotion"') >= 0, 'chat with code -> SSE stream', s.status);
  log(s.text.indexOf('"type": "thinking"') >= 0, 'reasoning forwarded through new server');

  const authOk = await j(B + '/api/auth', { headers: Object.assign(ip(6), { 'X-Access-Code': CODE }) });
  const authBad = await j(B + '/api/auth', { headers: Object.assign(ip(7), { 'X-Access-Code': 'nope' }) });
  log(authOk.status === 200, 'auth check ok');
  log(authBad.status === 401, 'auth check rejected', authBad.status);

  /* ---------- 管理配置 ---------- */
  log((await j(B + '/api/config', { headers: ip(8) })).status === 403, 'config hidden from visitor', 403);
  const vis = await j(B + '/api/config', { headers: Object.assign(ip(9), { 'X-Admin-Token': ADMIN }) });
  log(vis.status === 200 && vis.d.model === 'gpt-4o-mini', 'config visible to admin token', vis.d && vis.d.model);
  const w = await post(B + '/api/config', { model: 'gpt-4o-mini' }, Object.assign(ip(10), { 'X-Admin-Token': ADMIN }));
  log(w.status === 200, 'config writable with admin token', w.status);
  log((await post(B + '/api/config', { model: 'x' }, ip(11))).status === 403, 'config blocked without token', 403);

  /* ---------- 限流(3/分/IP) ---------- */
  const same = Object.assign(ip(50), { 'X-Access-Code': CODE });
  const rs = [];
  for (let i = 0; i < 4; i++) {
    rs.push((await post(B + '/api/chat', { message: 'hi' }, same)).status);
  }
  log(rs[0] === 200 && rs[1] === 200 && rs[2] === 200 && rs[3] === 429, 'per-IP rate limit (3/min)', rs.join(','));

  /* ---------- 每日额度(5,服务端 Key 路径) ---------- */
  // 已有的服务端 Key 调用:上面 1 次 chat_stream(ip5)+ 3 次限流通过(ip50)= 4 次
  const d5 = await post(B + '/api/chat', { message: 'hi' }, Object.assign(ip(60), { 'X-Access-Code': CODE }));
  log(d5.status === 200, 'daily quota used #5 ok', d5.status);
  const d6 = await post(B + '/api/chat', { message: 'hi' }, Object.assign(ip(61), { 'X-Access-Code': CODE }));
  log(d6.status === 429, 'daily quota blocks #6', d6.status);

  /* ---------- TTS 关闭 ---------- */
  const tts = await post(B + '/api/tts', { text: '你好' }, Object.assign(ip(62), { 'X-Access-Code': CODE }));
  log(tts.status === 403, 'tts disabled -> 403', tts.status);

  /* ---------- 8462:本机管理员/开放模式 ---------- */
  const ha = await j(A + '/api/health');
  log(ha.d && ha.d.admin === true, 'A: loopback is admin');
  const ca = await post(A + '/api/chat', { message: '你好' });
  log(ca.status === 200 && ca.d && ca.d.reply, 'A: open mode chat without code', ca.status);
  log(ha.d.auth_required === false, 'A: health flags', JSON.stringify({ admin: ha.d.admin, auth: ha.d.auth_required }));

  console.log(fails === 0 ? 'ALL PASS' : fails + ' FAILURES');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
