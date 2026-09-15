// BYOK 模式测试(端口 8464:TTS 关、每日额度 1、限流 30/分)
// 运行: node suites/byok_test.js
const C = process.env.EB_BYOK || 'http://127.0.0.1:8464';
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

async function stream(message, headers) {
  const r = await fetch(C + '/api/chat_stream', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, headers),
    body: JSON.stringify({ message, history: [] })
  });
  const text = await r.text();
  const events = [];
  text.split('\n').filter((l) => l.startsWith('data:')).forEach((l) => {
    try { events.push(JSON.parse(l.slice(5).trim())); } catch (e) {}
  });
  return { status: r.status, events, text };
}

(async () => {
  const visitor = { 'X-Forwarded-For': '198.51.100.9' };
  const h = await j(C + '/api/health');
  log(h.status === 200 && h.d.key === false && h.d.auth_required === false,
    'BYOK server: no builtin key, no code', JSON.stringify({ key: h.d.key, auth: h.d.auth_required }));

  const m403 = await j(C + '/api/models', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, visitor),
    body: JSON.stringify({})
  });
  log(m403.status === 403, 'models blocked without own creds', m403.status);

  const mOK = await j(C + '/api/models', {
    method: 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, visitor),
    body: JSON.stringify({ api_base: FAKE + '/v1', api_key: 'sk-user' })
  });
  log(mOK.status === 200 && (mOK.d.models || []).length >= 3, 'models fetchable with own creds',
    JSON.stringify((mOK.d.models || []).length));

  const noKey = await stream('你好', visitor);
  log(noKey.status === 200 && noKey.text.indexOf('no API key') >= 0 || noKey.text.indexOf('API Key') >= 0,
    'no creds -> helpful SSE error', noKey.status);

  const quota = await stream('你好', visitor);
  log(quota.status === 429, 'server-key path hits daily quota (limit=1)', quota.status);

  const userHeaders = {
    'X-API-Base': FAKE + '/v1',
    'X-API-Key': 'sk-user-key',
    'X-API-Model': 'fake-model-beta'
  };
  const s1 = await stream('你好', userHeaders);
  const types1 = s1.events.map((e) => e.type);
  log(s1.status === 200 && types1.indexOf('text') >= 0 && types1.indexOf('done') >= 0,
    'user-key chat #1 streams', types1.join(','));

  const s2 = await stream('再讲一个', userHeaders);
  log(s2.status === 200 && s2.events.some((e) => e.type === 'text'), 'user-key chat #2 streams (daily quota skipped)', s2.status);

  const hdrs = await j(FAKE + '/debug/last_headers');
  log(hdrs.status === 200 && !!hdrs.d['x-opencode-session'], 'upstream receives session header', hdrs.d && (hdrs.d['x-opencode-session'] || '').slice(0, 12));

  const h2 = await j(C + '/api/health', { headers: userHeaders });
  log(h2.status === 200, 'health fine with user headers');
  console.log(fails === 0 ? 'ALL PASS' : fails + ' FAILURES');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
