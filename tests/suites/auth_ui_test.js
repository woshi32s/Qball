// 访问码 UI 测试:访客(8463 需验证码)与管理员(8462 本机)
// 运行: node suites/auth_ui_test.js
const { launch, makeLogger } = require('../lib/common');
const { log, failCount } = makeLogger();
const B = process.env.EB_CODE || 'http://127.0.0.1:8463';
const A = process.env.EB_ADMIN || 'http://127.0.0.1:8462';
const FAKE = process.env.EB_FAKE || 'http://127.0.0.1:8484';

(async () => {
  const browser = await launch();

  /* ---------- 访客:验证码流程 + 聊天 + 个人接口配置 ---------- */
  {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    page.on('pageerror', (e) => console.log('PAGE-EXC', e.message));
    await page.addInitScript((fakeBase) => {
      try {
        localStorage.setItem('qball.api.v1', JSON.stringify({
          base: fakeBase, key: 'sk-fake', model: 'fake-model-alpha'
        }));
      } catch (e) {}
    }, FAKE + '/v1');
    // 伪装成远程访客(否则本机回环会被服务端视为管理员,跳过验证码)
    await page.route(B + '/**', (route) => {
      route.continue({ headers: Object.assign({}, route.request().headers(), { 'x-forwarded-for': '198.51.100.77' }) });
    });
    await page.goto(B + '/qball.html', { waitUntil: 'load' });
    await page.waitForFunction(() => document.getElementById('code-modal').classList.contains('open'), null, { timeout: 15000 });
    log(true, 'visitor sees access-code modal');

    await page.fill('#code-input', 'wrong-code');
    await page.click('#code-submit');
    await page.waitForTimeout(600);
    const err = await page.textContent('#code-error');
    log(/访问码不对/.test(err), 'wrong code shows error & stays', err.trim());
    log(await page.evaluate(() => document.getElementById('code-modal').classList.contains('open')), 'modal stays open after wrong code');

    await page.fill('#code-input', 'testcode123');
    await page.click('#code-submit');
    await page.waitForFunction(() => !document.getElementById('code-modal').classList.contains('open'), null, { timeout: 10000 });
    const st = await page.textContent('#status');
    log(true, 'right code accepted', st.trim());

    const auth = await page.evaluate(() => window.__demo.auth);
    log(auth.admin === false, 'visitor is not admin', JSON.stringify(auth));

    await page.fill('#chat-input', '你好');
    await page.click('#chat-send');
    await page.waitForFunction(() => document.querySelectorAll('#chat-log .msg-row').length >= 2, null, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(800);
    const chat = await page.evaluate(() => {
      const rows = document.querySelectorAll('#chat-log .msg-row');
      const last = rows[rows.length - 1];
      const txt = last && last.querySelector('.txt.md');
      return { rows: rows.length, bot: !!txt, strong: txt ? txt.querySelectorAll('strong').length : 0 };
    });
    log(chat.bot && chat.strong >= 1, 'visitor can chat with code', JSON.stringify(chat));

    await page.click('#settings-handle');
    await page.waitForTimeout(700);
    const panel = await page.evaluate(() => ({
      status: document.getElementById('cfg-status').textContent,
      base: document.getElementById('cfg-base').value
    }));
    log(/配置保存在本机浏览器/.test(panel.status) && panel.base === FAKE + '/v1',
      'visitor can configure own api (panel visible)', panel.status.trim());
    await page.close();
  }

  /* ---------- 本机管理员:无验证码 + 服务端配置面板 ---------- */
  {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    page.on('pageerror', (e) => console.log('PAGE-EXC', e.message));
    await page.goto(A + '/qball.html?mock=1', { waitUntil: 'load' });
    await page.waitForTimeout(2500);
    const st = await page.evaluate(() => ({
      modal: document.getElementById('code-modal').classList.contains('open'),
      auth: window.__demo.auth
    }));
    log(!st.modal && st.auth.admin === true, 'local is admin, no modal', JSON.stringify(st.auth));

    await page.click('#settings-handle');
    await page.waitForTimeout(300);
    await page.waitForFunction(() => document.getElementById('cfg-status').textContent.length > 0, null, { timeout: 8000 }).catch(() => {});
    const panel = await page.textContent('#cfg-status');
    log(/本机配置文件/.test(panel) || /本机/.test(panel), 'admin/local sees config panel', panel.trim());
    await page.close();
  }

  await browser.close();
  console.log(failCount() === 0 ? 'ALL PASS' : failCount() + ' FAILURES');
  process.exit(failCount() ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
