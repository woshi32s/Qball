/*
 * AgoraMotion — Agora 动效语言 Web 移植版
 * 参数与状态机语义复刻自 newo-ether/Agora (MIT License):
 *   ui/motion/AgoraMotionPolicy.kt          动效降级策略(三类语义开关)
 *   ui/chat/message/GenerationLifecycleMotion.kt  消息生命周期时长
 *   ui/chat/StreamingTailIndicator.kt       流式尾巴指示器 + 自动跟随状态机
 *   ui/chat/ChatAppInteractionEffects.kt    拖拽阈值 / 滚动恢复延迟
 *   ui/components/AnimatedBlobBackground.kt 背景光斑
 *   ui/settings/AnimatedActionFab.kt        按压弹簧
 */
(function (global) {
  'use strict';

  var SPEC = {
    MESSAGE_ENTER_MS: 320,
    SEGMENT_ENTER_MS: 420,
    SEGMENT_ENTER_INITIAL_SCALE: 0.90,
    STATUS_CROSSFADE_MS: 280,
    ACTIONS_ENTER_MS: 320,
    ACTIONS_EXIT_MS: 220,
    COMPOSER_ICON_CROSSFADE_MS: 200,
    REGENERATION_EXIT_MS: 180,
    STREAM_SCROLL_RESUME_DELAY_MS: 160,
    DRAWER_DISMISS_THRESHOLD: 0.5,
    ATTACH_THRESHOLD_PX: 48,
    LOOK: { TIME_CONSTANT_S: 0.11, MAX_VELOCITY_PX_S: 3400, MIN_STEP_PX: 0.6 },
    TAIL: {
      DOT_SIZE: 11,
      ENTER_MS: 400,
      ENTER_SCALE: 0.55,
      EXIT_SCALE_MS: 320,
      BREATH_MS: 850,
      BREATH_MIN: 0.55
    },
    SPRING: { STIFFNESS: 400, DAMPING_RATIO: 0.25 },
    SHEET_SPRING: { STIFFNESS: 480, DAMPING_RATIO: 0.85 },
    BLOB: {
      COUNT: 4,
      BLUR: 40,
      CENTER_ALPHA: 0.20,
      QUARTER_ALPHA: 0.09,
      EDGE_ALPHA: 0.0,
      VEIL_ALPHA: 0.12
    }
  };

  var EASE = {
    SPATIAL: 'cubic-bezier(0, 0, 0.2, 1)',
    STANDARD: 'cubic-bezier(0.4, 0, 0.2, 1)',
    LINEAR: 'linear'
  };

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function rand(lo, hi) { return lo + Math.random() * (hi - lo); }
  function prefersReduce() {
    return !!(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  /* ---------------- motion policy (AgoraMotionPolicy.kt) ---------------- */

  function createPolicy() {
    var appReduce = false;
    try { appReduce = global.localStorage.getItem('agoraDemo.reduceMotion') === '1'; } catch (e) {}
    var listeners = [];
    var mq = global.matchMedia ? global.matchMedia('(prefers-reduced-motion: reduce)') : null;

    var policy = {
      get reduceMotion() { return appReduce || (mq && mq.matches); },
      get allowContinuousMotion() { return !this.reduceMotion; },
      get allowSpatialTransitions() { return !this.reduceMotion; },
      get allowProgrammaticScroll() { return !this.reduceMotion; },
      get appReduceMotion() { return appReduce; },
      setAppReduceMotion: function (on) {
        appReduce = !!on;
        try { global.localStorage.setItem('agoraDemo.reduceMotion', on ? '1' : '0'); } catch (e) {}
        notify();
      },
      onChange: function (fn) { listeners.push(fn); },
      notify: notify
    };

    function notify() { listeners.forEach(function (fn) { try { fn(policy); } catch (e) {} }); }

    if (mq) {
      var onMq = function () { notify(); };
      if (mq.addEventListener) mq.addEventListener('change', onMq);
      else if (mq.addListener) mq.addListener(onMq);
    }
    return policy;
  }

  /* ---------------- draw-layer lifecycle enter (GenerationLifecycleMotion.kt) ---------------- */
  /* opacity + scale only — 元素第一帧就占用最终布局,永不触发重排 */

  function enter(el, kind, policy) {
    if (!el || !el.animate) return null;
    var spatial = !policy || policy.allowSpatialTransitions;
    var cfg;
    if (kind === 'segment') {
      cfg = { duration: SPEC.SEGMENT_ENTER_MS, scale0: spatial ? SPEC.SEGMENT_ENTER_INITIAL_SCALE : 1 };
    } else if (kind === 'actions') {
      cfg = { duration: SPEC.ACTIONS_ENTER_MS, scale0: 1 };
    } else {
      cfg = { duration: SPEC.MESSAGE_ENTER_MS, scale0: 1 };
    }
    var from = { opacity: 0 };
    if (spatial && cfg.scale0 !== 1) from.transform = 'scale(' + cfg.scale0 + ')';
    var anim = el.animate([from, { opacity: 1, transform: 'scale(1)' }], {
      duration: cfg.duration,
      easing: EASE.SPATIAL,
      fill: 'both'
    });
    anim.finished && anim.finished.then(function () {
      try { anim.cancel(); } catch (e) {}
      el.style.opacity = '';
    }).catch(function () {});
    return anim;
  }

  function exitActions(el, policy) {
    if (!el || !el.animate) return { finished: Promise.resolve() };
    var anim = el.animate(
      [{ opacity: 1 }, { opacity: 0 }],
      { duration: SPEC.ACTIONS_EXIT_MS, easing: EASE.STANDARD, fill: 'both' }
    );
    return anim;
  }

  /* ---------------- status crossfade 280ms ---------------- */

  function crossfade(outEls, inEls, opts) {
    opts = opts || {};
    var duration = opts.duration || SPEC.STATUS_CROSSFADE_MS;
    var spatial = !opts.policy || opts.policy.allowSpatialTransitions;
    if (outEls) {
      (Array.isArray(outEls) ? outEls : [outEls]).forEach(function (el) {
        if (!el) return;
        if (!el.animate) { el.style.display = 'none'; return; }
        var a = el.animate([{ opacity: 1 }, { opacity: 0 }], {
          duration: duration, easing: EASE.STANDARD, fill: 'both'
        });
        a.finished && a.finished.then(function () {
          try { a.cancel(); } catch (e) {}
          el.style.display = 'none';
        }).catch(function () {});
      });
    }
    if (inEls) {
      (Array.isArray(inEls) ? inEls : [inEls]).forEach(function (el) {
        if (!el) return;
        el.style.display = '';
        if (!el.animate) { el.style.opacity = '1'; return; }
        var frames = spatial
          ? [{ opacity: 0, transform: 'scale(0.985)' }, { opacity: 1, transform: 'scale(1)' }]
          : [{ opacity: 0 }, { opacity: 1 }];
        var a = el.animate(frames, { duration: duration, easing: EASE.SPATIAL, fill: 'both' });
        a.finished && a.finished.then(function () {
          try { a.cancel(); } catch (e) {}
          el.style.opacity = '';
        }).catch(function () {});
      });
    }
  }

  /* ---------------- composer icon crossfade 200ms ---------------- */

  function iconSwap(showEl, hideEl, policy) {
    var duration = SPEC.COMPOSER_ICON_CROSSFADE_MS;
    var spatial = !policy || policy.allowSpatialTransitions;
    [showEl, hideEl].forEach(function (el) {
      if (!el || !el.animate) return;
      el.style.display = 'block';
      var toShow = el === showEl;
      if (!toShow && el.style.opacity === '0') {
        el.style.display = 'none';
        return;
      }
      var frames = toShow
        ? [{ opacity: 0, transform: spatial ? 'scale(0.86)' : 'scale(1)' }, { opacity: 1, transform: 'scale(1)' }]
        : [{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: spatial ? 'scale(0.86)' : 'scale(1)' }];
      var a = el.animate(frames, { duration: duration, easing: EASE.SPATIAL, fill: 'both' });
      a.finished && a.finished.then(function () {
        try { a.cancel(); } catch (e) {}
        if (!toShow) el.style.display = 'none';
        el.style.opacity = toShow ? '' : '0';
      }).catch(function () {});
    });
  }

  /* ---------------- spring (AnimatedActionFab.kt) ---------------- */

  function createSpring(stiffness, dampingRatio) {
    var c = 2 * dampingRatio * Math.sqrt(stiffness);
    var x = 1, v = 0, target = 1;
    return {
      set: function (t) { target = t; },
      step: function (dt) {
        var a = -stiffness * (x - target) - c * v;
        v += a * dt;
        x += v * dt;
        if (Math.abs(x - target) < 0.0005 && Math.abs(v) < 0.005) { x = target; v = 0; }
        return x;
      },
      value: function () { return x; },
      velocity: function () { return v; },
      snapTo: function (val) { x = val; v = 0; target = val; }
    };
  }

  function pressSpring(el, opts) {
    if (!el) return null;
    opts = opts || {};
    var grow = opts.grow || 1.06;
    var contentScale = opts.contentScale || 1.1;
    var content = opts.content || el;
    var spring = createSpring(SPEC.SPRING.STIFFNESS, SPEC.SPRING.DAMPING_RATIO);
    var raf = 0, last = 0, pressed = false;

    function apply() {
      var s = spring.value();
      if (opts.contentOnly) {
        var cs = 1 + (s - 1) / ((grow - 1) || 1) * (contentScale - 1);
        el.style.transform = 'scale(' + cs.toFixed(4) + ')';
        return;
      }
      el.style.transform = 'scale(' + s.toFixed(4) + ')';
      if (content !== el) {
        var c2 = 1 + (s - 1) / ((grow - 1) || 1) * (contentScale - 1);
        content.style.transform = 'scale(' + c2.toFixed(4) + ')';
      }
    }

    function tick(ts) {
      if (!last) last = ts;
      var dt = Math.min(0.05, (ts - last) / 1000);
      last = ts;
      spring.step(dt);
      apply();
      var settled = Math.abs(spring.value() - 1) < 0.004 && Math.abs(spring.velocity()) < 0.05;
      if (!pressed && settled) {
        spring.snapTo(1);
        el.style.transform = '';
        if (content !== el) content.style.transform = '';
        raf = 0;
        return;
      }
      raf = requestAnimationFrame(tick);
    }

    function kick() {
      if (!raf) { last = 0; raf = requestAnimationFrame(tick); }
    }
    function start() {
      if (opts.disabled) return;
      pressed = true;
      spring.set(grow);
      kick();
    }
    function end() {
      if (!pressed) return;
      pressed = false;
      spring.set(1);
      kick();
    }
    el.addEventListener('pointerdown', start);
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
    el.addEventListener('pointerleave', end);
    return {
      destroy: function () {
        if (raf) cancelAnimationFrame(raf);
        el.style.transform = '';
        if (content !== el) content.style.transform = '';
        el.removeEventListener('pointerdown', start);
        el.removeEventListener('pointerup', end);
        el.removeEventListener('pointercancel', end);
        el.removeEventListener('pointerleave', end);
      }
    };
  }

  /* ---------------- streaming tail dot (StreamingTailIndicator.kt) ---------------- */

  function tailDotEnter(el, policy) {
    if (!el || !el.animate) return;
    var spatial = !policy || policy.allowSpatialTransitions;
    var from = { opacity: 0 };
    if (spatial) from.transform = 'scale(' + SPEC.TAIL.ENTER_SCALE + ')';
    var a = el.animate([from, { opacity: 1, transform: 'scale(1)' }], {
      duration: SPEC.TAIL.ENTER_MS,
      easing: EASE.SPATIAL,
      fill: 'both'
    });
    a.finished && a.finished.then(function () {
      try { a.cancel(); } catch (e) {}
      el.style.opacity = '';
    }).catch(function () {});
  }

  function tailDotExit(el, row, policy) {
    if (!el) return;
    el.style.opacity = '0';
    if (el.animate && (!policy || policy.allowSpatialTransitions)) {
      el.animate(
        [{ transform: 'scale(1)' }, { transform: 'scale(' + SPEC.TAIL.ENTER_SCALE + ')' }],
        { duration: SPEC.TAIL.EXIT_SCALE_MS, easing: EASE.LINEAR, fill: 'both' }
      );
    }
    if (row) row.classList.add('retire');
    setTimeout(function () {
      if (row && row.parentNode) row.remove();
    }, SPEC.TAIL.EXIT_SCALE_MS + 80);
  }

  /* ---------------- coalesced scroll step (StreamingTailIndicator.kt) ---------------- */

  function coalescedStep(errorPx, elapsedSeconds) {
    var L = SPEC.LOOK;
    if (!errorPx || elapsedSeconds <= 0) return 0;
    var fraction = 1 - Math.exp(-elapsedSeconds / L.TIME_CONSTANT_S);
    var maxStep = Math.max(L.MIN_STEP_PX, L.MAX_VELOCITY_PX_S * elapsedSeconds);
    return clamp(errorPx * fraction, -maxStep, maxStep);
  }

  /* ---------------- streaming tail follower (StreamingTailIndicator.kt) ----------------
   * INACTIVE / ARMED / ATTACHED / SETTLING / DETACHED
   * ARMED: 生成中但未跟随。ATTACHED: 页面底部随内容增长保持不动。
   * 用户一拖 → 立即 DETACHED;所有运动静止后,离底部足够近是唯一的重新吸附依据。
   */

  function createTailFollower(opts) {
    var container = opts.container;
    var policy = opts.policy;
    var onMode = opts.onMode || function () {};
    var mode = 'inactive';
    var generationActive = false;
    var pinRaf = 0, pinLast = 0;
    var resumeTimer = 0;
    var userMotion = false;
    var programmatic = false;
    var tickCount = 0;

    function setMode(next) {
      if (next === mode) return;
      mode = next;
      onMode(mode);
      opts.onLog && opts.onLog('tail ' + mode);
      if (mode === 'attached' || mode === 'settling') ensurePinLoop();
      if (mode === 'inactive') stopPinLoop();
    }

    function bottomGap() {
      return container.scrollHeight - container.clientHeight - container.scrollTop;
    }

    function writeScroll(top) {
      programmatic = true;
      container.scrollTop = top;
      requestAnimationFrame(function () { programmatic = false; });
    }

    function stopPinLoop() {
      if (pinRaf) { cancelAnimationFrame(pinRaf); pinRaf = 0; }
      pinLast = 0;
    }

    function ensurePinLoop() {
      if (pinRaf) return;
      pinRaf = requestAnimationFrame(pinTick);
    }

    function pinTick(ts) {
      pinRaf = 0;
      tickCount++;
      if (!pinLast) pinLast = ts;
      var dt = Math.min(0.05, (ts - pinLast) / 1000);
      pinLast = ts;
      var gap = bottomGap();
      if (gap <= 1) {
        if (gap > 0) writeScroll(container.scrollHeight);
        if (mode === 'settling') setMode('inactive');
        else if (mode === 'detached' && generationActive) {
          reduce({ type: 'proximity', within: true, scrollInProgress: false });
        }
        return;
      }
      if (!policy.allowProgrammaticScroll) {
        writeScroll(container.scrollHeight);
        if (mode === 'settling') setMode('inactive');
        return;
      }
      var step = coalescedStep(gap, dt);
      if (Math.abs(step) < 1) step = 1;
      writeScroll(container.scrollTop + step);
      ensurePinLoop();
    }

    function cancelUserMotion() {
      userMotion = false;
      if (resumeTimer) { clearTimeout(resumeTimer); resumeTimer = 0; }
    }

    function scheduleResumeCheck() {
      if (resumeTimer) clearTimeout(resumeTimer);
      resumeTimer = setTimeout(function () {
        resumeTimer = 0;
        userMotion = false;
        if (mode === 'inactive') return;
        var within = bottomGap() <= SPEC.ATTACH_THRESHOLD_PX;
        reduce({ type: 'proximity', within: within, scrollInProgress: false });
      }, SPEC.STREAM_SCROLL_RESUME_DELAY_MS);
    }

    function reduce(ev) {
      switch (ev.type) {
        case 'generation':
          generationActive = ev.active;
          if (!ev.active) {
            if (mode === 'attached' || mode === 'settling') setMode('settling');
            else setMode('inactive');
          } else if (mode === 'detached') {
            setMode('detached');
          } else if (mode === 'attached' || mode === 'settling') {
            /* keep */
          } else {
            setMode('armed');
          }
          break;
        case 'drag':
          if (mode !== 'inactive') setMode('detached');
          break;
        case 'proximity':
          if (mode === 'inactive') break;
          if (ev.scrollInProgress) break;
          if (mode === 'attached' || mode === 'settling') break;
          if (ev.within) setMode('attached');
          break;
        case 'settling':
          if (mode === 'settling') setMode('inactive');
          break;
      }
    }

    if (container) {
      container.addEventListener('wheel', function () { userDragStarted(); }, { passive: true });
      container.addEventListener('touchstart', function () { userDragStarted(); }, { passive: true });
      container.addEventListener('touchmove', function () { userDragStarted(); }, { passive: true });
      container.addEventListener('pointerdown', function (e) {
        if (e.target && e.target.closest && e.target.closest('.msg-actions, .tail-row')) return;
        userDragStarted();
      });
      container.addEventListener('scroll', function () {
        if (programmatic) return;
        if (pinRaf) return;
        if (mode === 'inactive') return;
        userDragStarted();
      }, { passive: true });
    }

    function userDragStarted() {
      if (mode === 'inactive') return;
      userMotion = true;
      stopPinLoop();
      reduce({ type: 'drag' });
      scheduleResumeCheck();
    }

    return {
      get mode() { return mode; },
      isGenerationActive: function () { return generationActive; },
      debug: function () {
        return {
          mode: mode, pinRaf: pinRaf, gap: container ? bottomGap() : -1,
          programmatic: programmatic, generationActive: generationActive,
          ticks: tickCount
        };
      },
      generationChanged: function (active) {
        reduce({ type: 'generation', active: active });
        if (active) {
          if (mode === 'inactive') setMode('armed');
          var within = bottomGap() <= SPEC.ATTACH_THRESHOLD_PX;
          reduce({ type: 'proximity', within: within, scrollInProgress: false });
        }
      },
      userDragStarted: userDragStarted,
      contentChanged: function () {
        if (mode === 'attached' || mode === 'settling') ensurePinLoop();
      },
      jumpToBottom: function (instant) {
        stopPinLoop();
        writeScroll(container.scrollHeight);
        if (!instant && policy.allowProgrammaticScroll) ensurePinLoop();
      },
      releaseToIdle: function () {
        cancelUserMotion();
        if (mode === 'attached' || mode === 'settling') setMode('settling');
      },
      destroy: function () { stopPinLoop(); cancelUserMotion(); }
    };
  }

  /* ---------------- blob background (AnimatedBlobBackground.kt) ---------------- */

  function createBlobs(canvas, opts) {
    opts = opts || {};
    var policy = opts.policy;
    if (!canvas) return { setMotion: function () {}, destroy: function () {} };
    var ctx = canvas.getContext('2d');
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    var W = 0, H = 0;

    var specs = [];
    for (var i = 0; i < SPEC.BLOB.COUNT; i++) {
      specs.push({
        cxFrac: rand(0.1, 0.9),
        cyFrac: rand(0.15, 0.85),
        radius: rand(180, 220),
        xAmp: rand(0.06, 0.14),
        yAmp: rand(0.06, 0.14),
        xPeriod: rand(10, 22),
        yPeriod: rand(8, 20),
        phase: i * 1.3
      });
    }

    var color = opts.color || [216, 220, 236];
    var rgb = 'rgb(' + color[0] + ',' + color[1] + ',' + color[2] + ')';
    var motion = opts.motionEnabled !== false;
    var raf = 0;
    var t0 = performance.now();

    function measure() {
      W = global.innerWidth;
      H = global.innerHeight;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      canvas.style.width = W + 'px';
      canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    measure();
    global.addEventListener('resize', measure);

    function draw(now) {
      var t = (now - t0) / 1000;
      ctx.clearRect(0, 0, W, H);

      var veilAlpha = SPEC.BLOB.VEIL_ALPHA;
      var g1 = ctx.createLinearGradient(0, 0, W, H);
      g1.addColorStop(0, 'rgba(' + color[0] + ',' + color[1] + ',' + color[2] + ',' + (0.6 * veilAlpha) + ')');
      g1.addColorStop(0.5, 'rgba(' + color[0] + ',' + color[1] + ',' + color[2] + ',' + (0.3 * veilAlpha) + ')');
      g1.addColorStop(1, 'rgba(' + color[0] + ',' + color[1] + ',' + color[2] + ',' + (0.6 * veilAlpha) + ')');
      ctx.fillStyle = g1;
      ctx.fillRect(0, 0, W, H);
      var g2 = ctx.createLinearGradient(0, H, W, 0);
      g2.addColorStop(0, 'rgba(' + color[0] + ',' + color[1] + ',' + color[2] + ',0)');
      g2.addColorStop(1, 'rgba(' + color[0] + ',' + color[1] + ',' + color[2] + ',' + (0.2 * veilAlpha) + ')');
      ctx.fillStyle = g2;
      ctx.fillRect(0, 0, W, H);

      for (var i = 0; i < specs.length; i++) {
        var s = specs[i];
        var ox = 0, oy = 0;
        if (motion) {
          ox = W * s.xAmp * Math.sin(2 * Math.PI * t / s.xPeriod + s.phase);
          oy = H * s.yAmp * Math.cos(2 * Math.PI * t / s.yPeriod + s.phase);
        }
        var x = W * s.cxFrac + ox;
        var y = H * s.cyFrac + oy;
        var r = s.radius;
        var grad = ctx.createRadialGradient(x, y, 0, x, y, r);
        grad.addColorStop(0, 'rgba(' + color[0] + ',' + color[1] + ',' + color[2] + ',' + SPEC.BLOB.CENTER_ALPHA + ')');
        grad.addColorStop(0.25, 'rgba(' + color[0] + ',' + color[1] + ',' + color[2] + ',' + SPEC.BLOB.QUARTER_ALPHA + ')');
        grad.addColorStop(1, 'rgba(' + color[0] + ',' + color[1] + ',' + color[2] + ',' + SPEC.BLOB.EDGE_ALPHA + ')');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    function loop(now) {
      draw(now);
      raf = requestAnimationFrame(loop);
    }

    function sync() {
      var want = motion && (!policy || policy.allowContinuousMotion);
      if (want) {
        if (!raf) raf = requestAnimationFrame(loop);
        return;
      }
      if (raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
      draw(performance.now());
    }
    sync();

    return {
      get specs() { return specs; },
      setMotion: function (on) {
        motion = !!on;
        sync();
      },
      refresh: sync,
      destroy: function () {
        if (raf) cancelAnimationFrame(raf);
        global.removeEventListener('resize', measure);
      }
    };
  }

  /* ---------------- bottom sheet drag (ChatAppInteractionEffects.kt drawer 阈值 0.5) ---------------- */

  function dragSheet(card, opts) {
    opts = opts || {};
    var threshold = opts.threshold || SPEC.DRAWER_DISMISS_THRESHOLD;
    var onDismiss = opts.onDismiss || function () {};
    var onDrag = opts.onDrag;
    var dy = 0, active = false, startY = 0;
    var height = 0;
    var spring = createSpring(SPEC.SHEET_SPRING.STIFFNESS, SPEC.SHEET_SPRING.DAMPING_RATIO);
    var raf = 0, last = 0, animating = false;

    function stopAnim() { if (raf) cancelAnimationFrame(raf); raf = 0; }
    function springTo(target, done) {
      stopAnim();
      spring.snapTo(dy);
      spring.set(target);
      animating = true;
      last = 0;
      (function tick(ts) {
        if (!last) last = ts;
        var dt = Math.min(0.05, (ts - last) / 1000);
        last = ts;
        dy = spring.step(dt);
        card.style.transform = 'translateY(' + Math.max(0, dy).toFixed(1) + 'px)';
        if (Math.abs(dy - target) < 0.4 && Math.abs(spring.value() - target) < 0.4) {
          dy = target;
          card.style.transform = target === 0 ? '' : 'translateY(' + target + 'px)';
          animating = false;
          done && done();
          return;
        }
        raf = requestAnimationFrame(tick);
      })(performance.now());
    }

    function down(e) {
      if (e.target && e.target.closest && e.target.closest('input, button, .switch')) return;
      active = true;
      dy = 0;
      startY = e.clientY;
      height = card.getBoundingClientRect().height;
      stopAnim();
      card.style.transition = 'none';
      try { card.setPointerCapture(e.pointerId); } catch (err) {}
    }
    function move(e) {
      if (!active) return;
      var raw = e.clientY - startY;
      dy = raw > 0 ? raw : raw * 0.12;
      card.style.transform = 'translateY(' + dy.toFixed(1) + 'px)';
      onDrag && onDrag(dy, height);
    }
    function up() {
      if (!active) return;
      active = false;
      card.style.transition = '';
      if (dy > height * threshold) {
        springTo(height + 40, function () {
          onDismiss();
          dy = 0;
          card.style.transform = '';
        });
      } else {
        springTo(0);
      }
    }
    card.addEventListener('pointerdown', down);
    card.addEventListener('pointermove', move);
    card.addEventListener('pointerup', up);
    card.addEventListener('pointercancel', up);

    return {
      reset: function () { stopAnim(); dy = 0; card.style.transform = ''; },
      destroy: function () {
        stopAnim();
        card.removeEventListener('pointerdown', down);
        card.removeEventListener('pointermove', move);
        card.removeEventListener('pointerup', up);
        card.removeEventListener('pointercancel', up);
      }
    };
  }

  global.AgoraMotion = {
    SPEC: SPEC,
    EASE: EASE,
    createPolicy: createPolicy,
    enter: enter,
    exitActions: exitActions,
    crossfade: crossfade,
    iconSwap: iconSwap,
    pressSpring: pressSpring,
    tailDotEnter: tailDotEnter,
    tailDotExit: tailDotExit,
    coalescedStep: coalescedStep,
    createTailFollower: createTailFollower,
    createBlobs: createBlobs,
    dragSheet: dragSheet
  };
})(window);
