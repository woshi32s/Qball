// 设置面板测试(8462,管理员):服务端配置预填 / 拉取模型 / 保存到本机配置文件 / 重载保持
// 运行: node suites/cfg_ui_test.js
const { launch, makeLogger } = require('../lib/common');
const { log, failCount } = makeLogger();
const A = process.env.EB_ADMIN || 'http://127.0.0.1:8462';
const FAKE = process.env.EB_FAKE || 'http://127.0.0.1:8484';

(async () => {
  await fetch(A + '/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_base: FAKE + '/v1', api_key: 'sk-fake', model: 'fake-model-alpha' })
  });

  const browser = await launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  page.on('pageerror', (e) => console.log('PAGE-EXC', e.message));

  await page.goto(A + '/qball.html?mock=1', { waitUntil: 'load' });
  await page.mouse.click(640, 430);
  await page.waitForTimeout(2500);
  await page.click('#settings-handle');
  await page.waitForTimeout(300);
  await page.waitForFunction(() => document.getElementById('cfg-base').value.length > 0, null, { timeout: 8000 }).catch(() => {});

  const pre = await page.evaluate(() => ({
    base: document.getElementById('cfg-base').value,
    model: document.getElementById('cfg-model').value,
    keyPh: document.getElementById('cfg-key').placeholder,
    status: document.getElementById('cfg-status').textContent
  }));
  log(pre.base === FAKE + '/v1', 'config prefill base', pre.base);
  log(pre.model === 'fake-model-alpha', 'config prefill model', pre.model);
  log(/已保存/.test(pre.keyPh), 'key placeholder masked state', pre.keyPh);

  // 管理员不输 Key 也能拉取(用本机已保存的 Key)——回归:之前误报"先填写 API 地址和 Key"
  await page.click('#cfg-fetch');
  await page.waitForFunction(() => /拉取成功|拉取失败/.test(document.getElementById('cfg-status').textContent), null, { timeout: 20000 }).catch(() => {});
  const adminFetch = await page.textContent('#cfg-status');
  log(/拉取成功/.test(adminFetch), 'admin fetch without typing key', adminFetch.trim());

  await page.fill('#cfg-key', 'sk-ui-test-123');
  await page.click('#cfg-fetch');
  await page.waitForFunction(() => document.querySelectorAll('#model-list option').length > 0, null, { timeout: 15000 }).catch(() => {});
  const fetched = await page.evaluate(() => ({
    options: Array.from(document.querySelectorAll('#model-list option')).map((o) => o.value),
    status: document.getElementById('cfg-status').textContent
  }));
  log(fetched.options.length >= 3, 'auto-fetch filled model datalist', fetched.options.length);
  log(/拉取成功/.test(fetched.status), 'fetch status feedback', fetched.status);

  await page.fill('#cfg-model', 'fake-model-beta');
  await page.click('#cfg-save');
  await page.waitForFunction(() => /已保存/.test(document.getElementById('cfg-status').textContent), null, { timeout: 10000 }).catch(() => {});
  const saved = await page.evaluate(() => ({
    status: document.getElementById('cfg-status').textContent,
    keyVal: document.getElementById('cfg-key').value,
    keyPh: document.getElementById('cfg-key').placeholder
  }));
  log(/已保存/.test(saved.status), 'save status feedback', saved.status);
  log(saved.keyVal === '', 'key field cleared after save');
  log(/已保存/.test(saved.keyPh), 'key placeholder updated');

  const cfg = await page.evaluate(() => fetch('/api/config').then((r) => r.json()));
  log(cfg.model === 'fake-model-beta', 'server persisted model', cfg.model);
  log(cfg.api_base === FAKE + '/v1', 'server persisted base', cfg.api_base);

  await page.reload({ waitUntil: 'load' });
  await page.waitForTimeout(2000);
  await page.click('#settings-handle');
  await page.waitForFunction(() => document.getElementById('cfg-model').value.length > 0, null, { timeout: 8000 }).catch(() => {});
  const reloaded = await page.evaluate(() => ({
    model: document.getElementById('cfg-model').value,
    base: document.getElementById('cfg-base').value
  }));
  log(reloaded.model === 'fake-model-beta', 'model persisted across reload', reloaded.model);

  /* ---------- 工具开关(管理员) ---------- */
  const tools = await page.evaluate(() => {
    const row = document.getElementById('tools-row');
    const input = document.getElementById('set-tools');
    return { visible: !!(row && row.style.display !== 'none'), checked: !!(input && input.checked) };
  });
  log(tools.visible && tools.checked, 'tools toggle shown & on', JSON.stringify(tools));
  await page.click('#tools-row .switch');
  await page.waitForTimeout(900);
  const toolsOff = await page.evaluate(() => fetch('/api/config').then((r) => r.json()).then((c) => c.tools_enabled));
  log(toolsOff === false, 'tools toggle off persists', toolsOff);
  await page.click('#tools-row .switch');
  await page.waitForTimeout(900);
  const toolsOn = await page.evaluate(() => fetch('/api/config').then((r) => r.json()).then((c) => c.tools_enabled));
  log(toolsOn === true, 'tools toggle back on', toolsOn);

  await browser.close();
  console.log(failCount() === 0 ? 'ALL PASS' : failCount() + ' FAILURES');
  process.exit(failCount() ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
