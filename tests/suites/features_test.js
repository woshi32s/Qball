// 功能测试:A. Markdown 单元 B. 思考卡片(模拟流) C. 真实链路(含 reasoning) D. 触屏适配 E. PWA
// 运行: node suites/features_test.js    (EB_BASE 默认 http://127.0.0.1:8462)
const { launch, makeLogger, artifacts } = require('../lib/common');
const { log, failCount } = makeLogger();
const B = process.env.EB_BASE || 'http://127.0.0.1:8462';
const FAKE = process.env.EB_FAKE || 'http://127.0.0.1:8484';
const OUT = artifacts('features');

(async () => {
  const browser = await launch();

  /* ---------- A. Markdown 单元 ---------- */
  {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    await page.goto(B + '/qball.html?mock=1', { waitUntil: 'load' });
    await page.waitForFunction(() => window.__demo && window.__demo.md, null, { timeout: 20000 });
    const r = await page.evaluate(() => {
      const md = window.__demo.md;
      const strip = window.__demo.stripMd;
      return {
        bold: md('**b**'),
        italic: md('*i*'),
        strike: md('~~s~~'),
        inlineCode: md('`c`'),
        ul: md('- a\n- b'),
        ol: md('1. a\n2. b'),
        fence: md('```js\nball.x(1)\n```'),
        openFence: md('text\n```js\nlet a'),
        link: md('[t](https://example.com)'),
        xss: md('<img src=x onerror=alert(1)>'),
        jsLink: md('[x](javascript:alert(1))'),
        head: md('### 标题'),
        quote: md('> q'),
        stripBold: strip('**bold** 和 `code` [链接](https://x.y)'),
        stripList: strip('- a\n1. b\n# c')
      };
    });
    log(r.bold.includes('<strong>b</strong>'), 'md bold');
    log(r.italic.includes('<em>i</em>'), 'md italic');
    log(r.strike.includes('<del>s</del>'), 'md strike');
    log(r.inlineCode.includes('<code class="inline">c</code>'), 'md inline code');
    log(r.ul.includes('<li>a</li>') && r.ul.includes('<li>b</li>'), 'md ul');
    log(r.ol.includes('<li>a</li>') && r.ol.includes('<li>b</li>'), 'md ol');
    log(r.fence.includes('data-lang="js"') && r.fence.includes('ball.x(1)'), 'md fenced code');
    log(r.openFence.includes('<pre><code'), 'md unclosed fence during stream');
    log(r.link.includes('href="https://example.com"') && r.link.includes('target="_blank"'), 'md link');
    log(!r.xss.includes('<img'), 'md xss escaped');
    log(!/href="javascript/i.test(r.jsLink) && r.jsLink.indexOf('<a ') < 0, 'md javascript: link rejected', r.jsLink.slice(0, 40));
    log(r.head.includes('<h5>标题</h5>'), 'md heading', r.head);
    log(r.quote.includes('<blockquote'), 'md blockquote');
    log(r.stripBold === 'bold 和 code 链接', 'strip for tts', JSON.stringify(r.stripBold));
    log(r.stripList === 'a\nb\nc', 'strip list/heading', JSON.stringify(r.stripList));
    await page.close();
  }

  /* ---------- B. 思考卡片(模拟流) ---------- */
  {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    page.on('pageerror', (e) => console.log('PAGE-EXC', e.message));
    await page.goto(B + '/qball.html?mock=1', { waitUntil: 'load' });
    await page.waitForFunction(() => window.__demo && window.__demo.onboard, null, { timeout: 20000 });
    await page.mouse.click(640, 430);
    await page.waitForTimeout(1200);

    await page.fill('#chat-input', '你好');
    await page.click('#chat-send');
    await page.waitForFunction(() => {
      const c = document.querySelector('.think-card.live');
      if (!c) return false;
      const t = c.querySelector('.think-text');
      return t && t.textContent.length > 10;
    }, null, { timeout: 15000 });
    const live = await page.evaluate(() => {
      const c = document.querySelector('.think-card.live');
      return { live: true, open: c.classList.contains('open'), label: c.querySelector('.think-label').textContent, text: c.querySelector('.think-text').textContent.length };
    });
    log(live.live && live.open && live.text > 10, 'think card streams live', JSON.stringify(live));

    await page.waitForFunction(() => window.__demo.sending === false, null, { timeout: 30000 });
    await page.waitForTimeout(900);
    const done = await page.evaluate(() => {
      const c = document.querySelector('.think-card');
      return { collapsed: !c.classList.contains('live'), label: c.querySelector('.think-label').textContent };
    });
    log(done.collapsed && /已思考/.test(done.label), 'think card auto-collapsed', done.label);

    await page.click('.think-card');
    await page.waitForTimeout(400);
    const reopened = await page.evaluate(() => document.querySelector('.think-card').classList.contains('open'));
    log(reopened, 'think card re-expands on click');

    const md = await page.evaluate(() => {
      const row = Array.from(document.querySelectorAll('.msg-row')).pop();
      const txt = row.querySelector('.txt.md');
      return {
        strong: txt.querySelectorAll('strong').length,
        em: txt.querySelectorAll('em').length,
        li: txt.querySelectorAll('li').length,
        pre: txt.querySelectorAll('pre code').length,
        inlineCode: txt.querySelectorAll('code.inline').length,
        link: txt.querySelectorAll('a').length,
        del: txt.querySelectorAll('del').length
      };
    });
    log(md.strong >= 1 && md.em >= 1 && md.li >= 3 && md.pre >= 1 && md.inlineCode >= 1 && md.link >= 1 && md.del >= 1,
      'answer rendered as markdown', JSON.stringify(md));
    await page.screenshot({ path: OUT + '/md_answer.png' }).catch(() => {});
    await page.close();
  }

  /* ---------- C. 真实链路(reasoning 转发 + 卡片折叠) ---------- */
  {
    await fetch(B + '/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_base: FAKE + '/v1', api_key: 'sk-fake', model: 'fake-model-alpha' })
    });
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    page.on('pageerror', (e) => console.log('PAGE-EXC', e.message));
    await page.addInitScript(() => {
      try {
        localStorage.setItem('qball.muted', '1');
        localStorage.setItem('qball.api.v1', JSON.stringify({ base: 'http://127.0.0.1:8484/v1', key: 'sk-fake', model: 'fake-model-alpha' }));
      } catch (e) {}
    });
    await page.goto(B + '/qball.html', { waitUntil: 'load' });
    await page.mouse.click(640, 430);
    await page.waitForTimeout(3000);
    await page.fill('#chat-input', '你好');
    await page.click('#chat-send');
    await page.waitForFunction(() => {
      const t = document.querySelector('.think-text');
      return t && t.textContent.indexOf('让我想想') >= 0;
    }, null, { timeout: 20000 });
    log(true, 'server forwarded reasoning_content into think card');
    await page.waitForFunction(() => window.__demo.sending === false, null, { timeout: 30000 });
    await page.waitForTimeout(800);
    const done = await page.evaluate(() => {
      const row = Array.from(document.querySelectorAll('.msg-row')).pop();
      const card = row.querySelector('.think-card');
      const txt = row.querySelector('.txt.md');
      return {
        collapsed: card && !card.classList.contains('live'),
        label: card && card.querySelector('.think-label').textContent,
        strong: txt ? txt.querySelectorAll('strong').length : 0,
        li: txt ? txt.querySelectorAll('li').length : 0
      };
    });
    log(done.collapsed && /已思考/.test(done.label), 'real-path think card collapsed', done.label);
    log(done.strong >= 1 && done.li >= 2, 'real-path markdown rendered', JSON.stringify(done));
    await page.close();
  }

  /* ---------- D. 触屏适配 ---------- */
  {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    page.on('pageerror', (e) => console.log('PAGE-EXC', e.message));
    await page.goto(B + '/qball.html?mock=1', { waitUntil: 'load' });
    await page.waitForFunction(() => window.__demo && window.__demo.coarse, null, { timeout: 20000 });
    await page.mouse.click(195, 420);
    await page.waitForTimeout(1200);

    const t = await page.evaluate(() => ({
      coarse: window.__demo.coarse,
      hoverNone: window.matchMedia('(hover: none)').matches,
      micTitle: document.getElementById('chat-mic').title,
      viewport: document.querySelector('meta[name=viewport]').content,
      manifest: !!document.querySelector('link[rel=manifest]')
    }));
    log(t.coarse && t.hoverNone, 'coarse pointer detected', JSON.stringify({ coarse: t.coarse, hoverNone: t.hoverNone }));
    log(t.micTitle === '按住说话', 'hold-to-talk wired on touch', t.micTitle);
    log(t.viewport.indexOf('resizes-content') >= 0, 'keyboard resize viewport meta');
    log(t.manifest, 'manifest linked');

    const drag = await page.evaluate(() => {
      const stage = document.getElementById('stage') || document.querySelector('.stage');
      function pe(type, x, y) {
        return new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'touch', clientX: x, clientY: y, isPrimary: true });
      }
      stage.dispatchEvent(pe('pointerdown', 200, 400));
      stage.dispatchEvent(pe('pointermove', 270, 450));
      stage.dispatchEvent(pe('pointerup', 270, 450));
      return stage.style.transform;
    });
    log(/translate\(70px,\s*50px\)/.test(drag), 'ball touch-drag follows finger', drag.slice(0, 60));

    await page.fill('#chat-input', '你好');
    await page.click('#chat-send');
    await page.waitForFunction(() => document.querySelectorAll('#chat-log .msg-row').length >= 2, null, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(600);
    const actions = await page.evaluate(() => {
      const row = Array.from(document.querySelectorAll('.msg-row')).pop();
      const a = row.querySelector('.msg-actions');
      return a ? getComputedStyle(a).opacity : '0';
    });
    log(parseFloat(actions) > 0.5, 'actions always visible on touch (no hover needed)', actions);
    await page.close();
  }

  /* ---------- E. PWA ---------- */
  {
    const man = await fetch(B + '/manifest.webmanifest').then((r) => r.json());
    log(man.name === 'Qball' && man.icons.length === 3, 'manifest served & valid');
    const codes = [];
    for (const p of ['/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-maskable-512.png', '/icons/icon-180.png']) {
      const r = await fetch(B + p);
      codes.push(r.status);
    }
    log(codes.every((c) => c === 200), 'icons served', codes.join(','));

    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    await page.goto(B + '/qball.html?mock=1', { waitUntil: 'load' });
    await page.waitForFunction(() => navigator.serviceWorker && navigator.serviceWorker.getRegistration().then ? true : true, null, { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(2500);
    const sw1 = await page.evaluate(async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return { supported: 'serviceWorker' in navigator, registration: !!reg };
    });
    await page.reload({ waitUntil: 'load' });
    await page.waitForTimeout(2500);
    const sw2 = await page.evaluate(() => ({
      controller: !!navigator.serviceWorker.controller,
      caches: typeof caches !== 'undefined' ? caches.keys() : null
    }));
    const cacheKeys = sw2.caches ? await sw2.caches : [];
    log(sw1.supported && sw1.registration, 'service worker registered', JSON.stringify(sw1));
    log(sw2.controller || cacheKeys.length > 0, 'served from SW cache after reload', JSON.stringify({ controller: sw2.controller, caches: cacheKeys }));
    await page.close();
  }

  await browser.close();
  console.log(failCount() === 0 ? 'ALL PASS' : failCount() + ' FAILURES');
  process.exit(failCount() ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
