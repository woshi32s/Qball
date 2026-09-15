// 开场引导流程测试(8464,BYOK 空配置):场景0打字 → 场景1图标 → 表单 → 场景3模型气泡/列表
// → 选中特写 → 吞入小球 → 场景5连接测试 → 完成;含返回用户跳过 / 坏地址手动输入 / 失败重试
// 运行: node suites/onboard2_test.js
const { launch, makeLogger, artifacts } = require('../lib/common');
const { log, failCount } = makeLogger();
const C = process.env.EB_BYOK || 'http://127.0.0.1:8464';
const FAKE = process.env.EB_FAKE || 'http://127.0.0.1:8484';
const OUT = artifacts('onboard');

async function enterScene2(page) {
  await page.click('#bot');
  await page.waitForFunction(() => window.__demo.onboard.scene === 1, null, { timeout: 8000 });
  await page.click('.ob-ico');
  await page.waitForFunction(() => window.__demo.onboard.scene === 2, null, { timeout: 12000 });
}

(async () => {
  const browser = await launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  page.on('pageerror', (e) => console.log('PAGE-EXC', e.message));

  await page.goto(C + '/qball.html', { waitUntil: 'load' });
  await page.waitForFunction(() => window.__demo && window.__demo.onboard.started, null, { timeout: 25000 });

  /* ---------- 场景 0:打字大标题 ---------- */
  const s0 = await page.evaluate(() => ({
    scene: window.__demo.onboard.scene,
    chars: document.querySelectorAll('#ob-hello span').length,
    hello: document.getElementById('ob-hello').textContent.replace(/\u00A0/g, ' '),
    sub: !document.getElementById('ob-sub').hidden,
    chat: document.body.classList.contains('onboarding')
  }));
  log(s0.scene === 0 && s0.chars === 5 && /HI,你好/.test(s0.hello) && s0.sub && s0.chat,
    'scene0: floating typewriter + hint', JSON.stringify(s0));

  /* ---------- 场景 1:图标漂移 → 点击全爆 → 进表单 ---------- */
  await page.click('#bot');
  await page.waitForFunction(() => window.__demo.onboard.scene === 1, null, { timeout: 8000 });
  const drift = await page.evaluate(async () => {
    const el = document.querySelector('.ob-ico');
    const a = el.style.left + ',' + el.style.top;
    await new Promise((r) => setTimeout(r, 750));
    const b = el.style.left + ',' + el.style.top;
    return { a, b };
  });
  log(drift.a !== drift.b, 'scene1: icons drift slowly on their own', drift.a + ' -> ' + drift.b);

  await page.click('.ob-ico');
  await page.waitForFunction(() => window.__demo.onboard.iconsLeft === 0, null, { timeout: 10000 });
  const popped = await page.evaluate(() => ({
    total: document.querySelectorAll('.ob-ico').length,
    out: document.querySelectorAll('.ob-ico.ob-out').length
  }));
  log(popped.total >= 5 && popped.out === popped.total, 'scene1: one click -> all pop sequentially',
    popped.out + '/' + popped.total);

  await page.waitForFunction(() => window.__demo.onboard.scene === 2, null, { timeout: 8000 });
  const t1 = Date.now();
  await page.waitForFunction(() => document.getElementById('ob-cloud').classList.contains('ob-show'), null, { timeout: 6000 });
  const cloudDelay = Date.now() - t1;
  log(cloudDelay > 250 && cloudDelay < 3000, 'cloud pops after entering scene2', cloudDelay + 'ms');
  await page.screenshot({ path: OUT + '/scene2_form.png' }).catch(() => {});

  /* ---------- 场景 3:拉取模型 → 气泡 + 列表 ---------- */
  await page.fill('#ob-base', FAKE + '/v1');
  await page.fill('#ob-key', 'sk-onboard');
  await page.click('#ob-detect');
  await page.waitForFunction(() => document.querySelectorAll('.ob-list-row').length > 0, null, { timeout: 15000 });
  await page.waitForTimeout(1400);
  const list = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('.ob-list-row'));
    const bubbles = Array.from(document.querySelectorAll('.ob-bubble'));
    const sizes = bubbles.map((b) => b.style.width);
    const imgs = bubbles.map((b) => { const i = b.querySelector('img'); return i ? i.getAttribute('src') : ''; });
    return {
      rows: rows.length,
      firstRow: rows[0] && rows[0].dataset.name,
      bubbles: bubbles.length,
      uniqSizes: new Set(sizes).size,
      imgNames: imgs.map((s) => s.split('/').pop().split('?')[0]).join(','),
      loaded: bubbles.every((b) => { const i = b.querySelector('img'); return i && i.complete && i.naturalWidth > 0; })
    };
  });
  log(list.bubbles === 12 && list.rows === 17, 'scene3: brand bubbles + full list',
    JSON.stringify({ bubbles: list.bubbles, rows: list.rows, firstName: list.firstRow }));
  log(list.firstRow === 'gpt-4o', 'sorted by fame (gpt-4o first)', list.firstRow);
  log(list.uniqSizes >= 3, 'bubble sizes vary', list.uniqSizes);
  log(list.imgNames.indexOf('openai.svg') === 0 && /claude[v]?/.test(list.imgNames), 'model icons auto-matched', list.imgNames);
  log(list.loaded, 'model icons loaded');
  const svgColored = await page.evaluate(async () => {
    const r = await fetch('icons/models/openai.svg');
    const t = await r.text();
    return /gradient|#[0-9a-fA-F]{3,6}/.test(t);
  });
  log(svgColored, 'model icons are colored');
  await page.screenshot({ path: OUT + '/scene3_orbit.png' }).catch(() => {});

  await page.fill('#ob-search', 'claude');
  await page.waitForTimeout(400);
  const dim = await page.evaluate(() => ({
    dimRows: document.querySelectorAll('.ob-list-row.dim').length,
    dimBubbles: document.querySelectorAll('.ob-bubble.dim').length,
    rows: document.querySelectorAll('.ob-list-row').length,
    bubbles: document.querySelectorAll('.ob-bubble').length
  }));
  log(dim.dimRows === dim.rows - 2 && dim.dimBubbles === dim.bubbles - 1, 'search dims non-matching', JSON.stringify(dim));
  await page.fill('#ob-search', '');
  await page.waitForTimeout(300);

  /* ---------- 选中 → 特写 → 吞入 ---------- */
  // 气泡持续公转,需按坐标点击(Playwright 会等"稳定"而超时)
  let picked = '';
  for (let attempt = 0; attempt < 6 && !picked; attempt++) {
    const bb = await page.evaluate(() => {
      const b = document.querySelector('.ob-bubble:not(.ob-out)');
      if (!b) return null;
      const r = b.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, name: b.title };
    });
    if (!bb) break;
    await page.mouse.click(bb.x, bb.y);
    await page.waitForTimeout(250);
    picked = await page.evaluate(() => window.__demo.onboard.model || '');
  }
  await page.waitForTimeout(300);
  const big = await page.evaluate(() => document.querySelectorAll('.ob-bubble.big').length);
  log(!!picked && big >= 1, 'selected bubble gets a close-up', JSON.stringify({ picked, big }));

  await page.waitForFunction(() => window.__demo.onboard.scene === 4, null, { timeout: 8000 });
  await page.waitForFunction(() => window.__demo.onboard.scene === 5, null, { timeout: 10000 });
  const model = await page.evaluate(() => window.__demo.onboard.model);
  const bubblesLeft = await page.evaluate(() => document.querySelectorAll('.ob-bubble').length);
  log(!!model && bubblesLeft <= 1, 'merge: others exploded, selection kept', JSON.stringify({ model, bubblesLeft }));

  /* ---------- 场景 5:连接测试 → 完成 ---------- */
  await page.waitForFunction(() => /连接成功/.test(document.getElementById('ob-test-text').textContent), null, { timeout: 25000 });
  const t5 = await page.evaluate(() => ({
    text: document.getElementById('ob-test-text').textContent,
    reply: document.getElementById('ob-test-reply').textContent
  }));
  log(/连接成功/.test(t5.text) && t5.reply.length > 0, 'scene5: connection test ok', JSON.stringify(t5));

  await page.waitForFunction(() => !document.body.classList.contains('onboarding'), null, { timeout: 12000 });
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('qball.api.v1') || '{}'));
  log(saved.base === FAKE + '/v1' && saved.key === 'sk-onboard' && saved.model === model,
    'finish: enters chat with saved cfg', JSON.stringify({ base: saved.base, key: saved.key, model: saved.model }));

  await page.fill('#chat-input', '你好');
  await page.click('#chat-send');
  await page.waitForFunction(() => {
    const bots = document.querySelectorAll('#chat-log .msg.bot');
    return bots.length >= 1 && !document.querySelector('#chat-log .msg.bot.typing');
  }, null, { timeout: 30000 });
  const chatOk = await page.evaluate(() => {
    const bot = Array.from(document.querySelectorAll('#chat-log .msg.bot')).pop();
    const txt = bot && (bot.querySelector('.txt') || bot.querySelector('.txt.md'));
    const me = document.querySelector('#chat-log .msg.me');
    return { has: !!bot, me: !!me, len: txt ? txt.textContent.length : 0 };
  });
  log(chatOk.has && chatOk.me && chatOk.len > 10, 'chat works after cinematic onboarding', JSON.stringify(chatOk));
  await page.close();

  /* ---------- 返回用户跳过引导 ---------- */
  const page2 = await context.newPage();
  await page2.goto(C + '/qball.html', { waitUntil: 'load' });
  await page2.waitForTimeout(2600);
  const ret = await page2.evaluate(() => ({
    started: window.__demo.onboard.started,
    chat: !document.body.classList.contains('onboarding')
  }));
  log(!ret.started && ret.chat, 'returning user skips onboarding', JSON.stringify(ret));
  await page2.close();
  await context.close();

  /* ---------- 坏地址 → 手动输入 → 测试失败重试 ---------- */
  const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page3 = await ctx2.newPage();
  page3.on('pageerror', (e) => console.log('PAGE-EXC', e.message));
  await page3.goto(C + '/qball.html', { waitUntil: 'load' });
  await page3.waitForFunction(() => window.__demo && window.__demo.onboard.started, null, { timeout: 25000 });
  await enterScene2(page3);
  await page3.fill('#ob-base', 'http://127.0.0.1:9/v1');
  await page3.fill('#ob-key', 'sk-x');
  await page3.click('#ob-detect');
  await page3.waitForFunction(() => !document.getElementById('ob-manual').hidden, null, { timeout: 20000 });
  log(true, 'bad endpoint -> manual input appears');

  await page3.fill('#ob-manual', 'my-local-model');
  await page3.press('#ob-manual', 'Enter');
  await page3.waitForFunction(() => window.__demo.onboard.scene === 5, null, { timeout: 15000 });
  await page3.waitForFunction(() => /连接失败/.test(document.getElementById('ob-test-text').textContent), null, { timeout: 25000 });
  const fail = await page3.evaluate(() => ({
    text: document.getElementById('ob-test-text').textContent,
    retry: !document.getElementById('ob-test-actions').hidden
  }));
  log(/连接失败/.test(fail.text) && fail.retry, 'failed test shows retry', JSON.stringify(fail));

  await page3.click('#ob-edit2');
  await page3.waitForFunction(() => window.__demo.onboard.scene === 2, null, { timeout: 8000 });
  const form = await page3.evaluate(() => ({
    form: !document.getElementById('ob-panel-form').hidden,
    model: window.__demo.onboard.model
  }));
  log(form.form && !form.model, 'back to form from failure', JSON.stringify(form));
  await ctx2.close();

  await browser.close();
  console.log(failCount() === 0 ? 'ALL PASS' : failCount() + ' FAILURES');
  process.exit(failCount() ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
