// 动效与交互测试(8462 模拟):光斑 / 流式尾巴状态机 / 悬浮跟随 / 设置抽屉 / 按压弹簧 / 减弱动效 / 新对话
// 运行: node suites/agora_test.js
const { launch, makeLogger, artifacts } = require('../lib/common');
const { log, failCount } = makeLogger();
const B = process.env.EB_BASE || 'http://127.0.0.1:8462';
const OUT = artifacts('agora');

const HASH_SRC = `
window.__hash = function () {
  var c = document.getElementById('blob-canvas') || document.querySelector('canvas:not(#boot-canvas)');
  if (!c) return { hash: -1, ink: 0 };
  var ctx = c.getContext('2d');
  var d = ctx.getImageData(0, 0, c.width, c.height).data;
  var n = 0, hsh = 0;
  for (var i = 0; i < d.length; i += 53) { if (d[i + 3]) n += d[i]; hsh = ((hsh << 5) - hsh + d[i]) | 0; }
  return { hash: hsh, ink: n };
};`;

(async () => {
  const browser = await launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.addInitScript(HASH_SRC);
  await page.goto(B + '/qball.html?mock=1', { waitUntil: 'load' });
  await page.waitForFunction(() => window.__demo && window.__demo.tailMode !== undefined, null, { timeout: 20000 });
  await page.mouse.click(640, 430);
  await page.waitForTimeout(1200);

  log(await page.evaluate(() => !!(window.QballMotion && window.__demo)), 'QballMotion + debug handle');

  /* ---------- 光斑 ---------- */
  const ink = await page.evaluate(() => window.__hash());
  log(ink && ink.ink > 0, 'blob canvas painted', JSON.stringify(ink));
  const drift1 = await page.evaluate(async () => {
    const a = window.__hash().hash;
    await new Promise((r) => setTimeout(r, 700));
    const b = window.__hash().hash;
    return a !== b;
  });
  log(drift1, 'blob drifting on welcome surface');

  const modeInfo = await page.evaluate(() => ({
    chat: window.__demo.chatMode,
    cls: document.body.classList.contains('chat-mode'),
    log: getComputedStyle(document.getElementById('chat-log')).display
  }));
  log(modeInfo.chat && modeInfo.log !== 'none', 'demo defaults to chat mode (log visible)', JSON.stringify(modeInfo));
  const enterIcon = await page.evaluate(() => {
    const ic = document.querySelector('#chat-send .ic-enter');
    return ic ? getComputedStyle(ic).display !== 'none' : false;
  });
  log(enterIcon, 'send button shows enter-key icon');

  /* ---------- 发送 → 流式尾巴 ---------- */
  await page.fill('#chat-input', '你好');
  await page.click('#chat-send');
  await page.waitForFunction(() => window.__demo.sending === true, null, { timeout: 8000 });
  await page.waitForFunction(() => window.__demo.tailMode === 'attached', null, { timeout: 8000 });
  log(true, 'tail auto-attached on send', await page.evaluate(() => window.__demo.tailMode));

  await page.waitForTimeout(600); // 等 200ms 交叉淡入完成
  const composer = await page.evaluate(() => {
    const enterEl = document.querySelector('#chat-send .ic-enter');
    const cs = getComputedStyle(enterEl);
    return {
      title: document.getElementById('chat-send').title,
      stop: getComputedStyle(document.querySelector('#chat-send .lbl-stop')).display !== 'none',
      enterHidden: cs.display === 'none' || parseFloat(cs.opacity || '1') < 0.2
    };
  });
  log(composer.title === '停止', 'composer sending state', composer.title);
  log(composer.stop, 'composer label crossfaded to 停止');
  log(composer.enterHidden, '发送 label hidden during stream');

  await page.waitForTimeout(600);
  const tailRow = await page.evaluate(() => {
    const row = document.querySelector('.tail-row');
    return { row: !!row, dot: !!(row && row.querySelector('.tail-dot')) };
  });
  log(tailRow.row && tailRow.dot, 'tail dot present during stream', JSON.stringify(tailRow));

  await page.waitForFunction(() => {
    const t = document.querySelector('.msg.bot.typing .txt');
    return t && t.textContent.length > 0;
  }, null, { timeout: 15000 }).catch(() => {});
  const bubbles = await page.evaluate(async () => {
    const me = document.querySelector('#chat-log .msg.me');
    const bot = document.querySelector('.msg.bot');
    const typing = !!document.querySelector('.msg.bot.typing');
    const txt = bot ? (bot.querySelector('.txt') || {}).textContent || '' : '';
    const a = window.__hash().hash;
    await new Promise((r) => setTimeout(r, 1400));
    const b = window.__hash().hash;
    return { me: !!me, bot: !!bot, typing, txtLen: txt.length, drifting: a !== b };
  });
  log(bubbles.me && bubbles.bot, 'user bubble appended', JSON.stringify({ me: bubbles.me, bot: bubbles.bot }));
  log(bubbles.typing && bubbles.txtLen > 0, 'bot bubble streamed', JSON.stringify({ typing: bubbles.typing, len: bubbles.txtLen }));
  log(bubbles.drifting, 'blob keeps drifting during chat');

  /* ---------- 上拖 → 脱离 → 跟随芯片 ---------- */
  const logBox = await page.evaluate(() => {
    const r = document.getElementById('chat-log').getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + Math.min(200, r.height / 2) };
  });
  await page.mouse.move(logBox.x, logBox.y);
  await page.mouse.wheel(0, -600);
  await page.waitForFunction(() => window.__demo.tailMode === 'detached', null, { timeout: 6000 });
  log(true, 'wheel drag latches DETACHED', await page.evaluate(() => window.__demo.tailMode));
  const chip = await page.evaluate(() => document.getElementById('follow-chip').classList.contains('show'));
  log(chip, 'follow chip shown while detached + streaming');

  await page.click('#follow-chip', { force: true });
  await page.waitForFunction(() => ['attached', 'settling'].indexOf(window.__demo.tailMode) >= 0, null, { timeout: 6000 }).catch(() => {});
  if ((await page.evaluate(() => window.__demo.tailMode)) === 'detached') {
    // 兜底:滚回底部触发"接近重吸附"
    await page.mouse.move(logBox.x, logBox.y);
    await page.mouse.wheel(0, 1200);
    await page.waitForFunction(() => ['attached', 'settling', 'inactive'].indexOf(window.__demo.tailMode) >= 0, null, { timeout: 8000 });
  }
  log(true, 'proximity re-attach via chip / scroll back', await page.evaluate(() => window.__demo.tailMode));
  await page.waitForTimeout(400);
  const nearBottom = await page.evaluate(() => {
    const el = document.getElementById('chat-log');
    return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  });
  log(nearBottom, 'scroll moved toward bottom');

  await page.waitForFunction(() => window.__demo.sending === false, null, { timeout: 30000 });
  await page.waitForTimeout(1600);
  const rest = await page.evaluate(() => {
    const row = document.querySelector('.tail-row');
    return {
      mode: window.__demo.tailMode,
      retired: !row || row.classList.contains('retire'),
      typingGone: !document.querySelector('.msg.bot.typing'),
      title: document.getElementById('chat-send').title,
      enter: getComputedStyle(document.querySelector('#chat-send .ic-enter')).display !== 'none',
      copies: document.querySelectorAll('.msg-actions button').length
    };
  });
  log(rest.mode === 'inactive', 'tail settled INACTIVE at rest', rest.mode);
  log(rest.copies >= 1, 'copy icon button present', rest.copies);
  log(rest.retired, 'tail dot row retired');
  log(rest.typingGone, 'typing class cleared');
  log(rest.title === '发送' && rest.enter, 'composer back to enter icon');

  /* ---------- 消息操作行的悬浮行为 ---------- */
  const actHidden = await page.evaluate(() => {
    const row = Array.from(document.querySelectorAll('.msg-row')).pop();
    const a = row.querySelector('.msg-actions');
    return a ? parseFloat(getComputedStyle(a).opacity) : -1;
  });
  log(actHidden >= 0 && actHidden < 0.3, 'actions hidden without hover', actHidden);
  await page.hover('#chat-log .msg-row:last-child');
  await page.waitForTimeout(500);
  const actShown = await page.evaluate(() => {
    const row = Array.from(document.querySelectorAll('.msg-row')).pop();
    const a = row.querySelector('.msg-actions');
    return a ? parseFloat(getComputedStyle(a).opacity) : -1;
  });
  log(actShown > 0.5, 'actions revealed on hover', actShown);
  const below = await page.evaluate(() => {
    const row = Array.from(document.querySelectorAll('.msg-row')).pop();
    const bubble = row.querySelector('.msg');
    const a = row.querySelector('.msg-actions');
    if (!bubble || !a) return false;
    return a.getBoundingClientRect().top >= bubble.getBoundingClientRect().bottom - 6;
  });
  log(below, 'actions row sits below the bubble');

  /* ---------- 按压弹簧(设置把手) ---------- */
  const handle = await page.evaluate(() => {
    const r = document.getElementById('settings-handle').getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  await page.mouse.move(handle.x, handle.y);
  await page.mouse.down();
  await page.waitForTimeout(320);
  const pressed = await page.evaluate(() => {
    const m = document.getElementById('settings-handle').style.transform.match(/scale\(([\d.]+)\)/);
    return m ? parseFloat(m[1]) : 1;
  });
  log(pressed > 1.03, 'press spring engages', 'scale(' + pressed + ')');
  await page.mouse.up();
  await page.waitForTimeout(1100);
  const settled = await page.evaluate(() => {
    const t = document.getElementById('settings-handle').style.transform;
    const m = t.match(/scale\(([\d.]+)\)/);
    return m ? parseFloat(m[1]) : 1;
  });
  log(Math.abs(settled - 1) < 0.02, 'press spring settles back', 'scale(' + settled + ')');

  /* ---------- 设置抽屉 ---------- */
  // 按压弹簧那一下 mouseup 本身就是一次点击,弹窗可能已经打开
  const modalOpen = await page.evaluate(() => document.getElementById('settings-modal').classList.contains('open'));
  if (!modalOpen) {
    await page.click('#settings-handle', { force: true });
  }
  await page.waitForFunction(() => document.getElementById('settings-modal').classList.contains('open'), null, { timeout: 6000 });
  log(true, 'settings sheet opens');
  const sheet = await page.evaluate(() => {
    const card = document.querySelector('.settings-card');
    const g = card.querySelector('.sheet-grabber') || card;
    const r = g.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2, h: card.getBoundingClientRect().height };
  });
  const endY = Math.min(sheet.y + sheet.h * 0.7, 852); // 底部抽屉:向下拖 >50% 关闭
  await page.mouse.move(sheet.x, sheet.y);
  await page.mouse.down();
  await page.mouse.move(sheet.x, endY, { steps: 14 });
  await page.waitForTimeout(120);
  await page.mouse.up();
  await page.waitForFunction(() => !document.getElementById('settings-modal').classList.contains('open'), null, { timeout: 8000 });
  log(true, 'sheet drag >50% dismisses');

  /* ---------- 减弱动态效果 ---------- */
  await page.evaluate(() => window.__demo.policy.setAppReduceMotion(true));
  await page.waitForTimeout(400);
  const reduced = await page.evaluate(() => ({
    noCont: document.body.classList.contains('no-continuous'),
    app: window.__demo.policy.appReduceMotion
  }));
  log(reduced.noCont && reduced.app, 'reduce-motion policy applied', JSON.stringify(reduced));
  const frozen = await page.evaluate(async () => {
    const a = window.__hash().hash;
    await new Promise((r) => setTimeout(r, 900));
    const b = window.__hash().hash;
    return a === b;
  });
  log(frozen, 'blob frozen under reduce motion');
  await page.evaluate(() => window.__demo.policy.setAppReduceMotion(false));
  await page.waitForTimeout(300);
  const resumed = await page.evaluate(async () => {
    const a = window.__hash().hash;
    await new Promise((r) => setTimeout(r, 900));
    const b = window.__hash().hash;
    return a !== b;
  });
  log(resumed, 'reduce-motion restored');

  /* ---------- 新对话 ---------- */
  await page.evaluate(() => window.__demo.clear());
  await page.waitForTimeout(1000);
  const cleared = await page.evaluate(() => ({
    idle: window.__demo.sceneIdle,
    msgs: document.querySelectorAll('#chat-log .msg-row').length,
    hint: !!document.querySelector('#chat-log .log-hint')
  }));
  log(cleared.idle, 'new chat returns to welcome surface', JSON.stringify(cleared));
  log(cleared.msgs === 0 && !cleared.hint, 'new chat clears messages (no hint)', JSON.stringify(cleared));
  const drifted = await page.evaluate(async () => {
    const a = window.__hash().hash;
    await new Promise((r) => setTimeout(r, 700));
    const b = window.__hash().hash;
    return a !== b;
  });
  log(drifted, 'blob drift resumed after new chat');

  log(errors.length === 0, 'no page errors', errors.join(' | ').slice(0, 140));
  await page.screenshot({ path: OUT + '/final.png' }).catch(() => {});
  await browser.close();
  console.log(failCount() === 0 ? 'ALL PASS' : failCount() + ' FAILURES');
  process.exit(failCount() ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
