/* 叠加层主逻辑：一份快照喂五个组件
   数据源 /api/snapshot —— 拿不到（file:// 或服务未启动）就退回内置演示数据，
   所以直接双击 index.html 也能看效果。 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const qs = new URLSearchParams(location.search);

  /* 出错别静默：打不开黑屏时先看这里（?debug） */
  addEventListener('error', (e) => {
    const msg = 'JS ERROR: ' + (e.message || e.type) + ' @ ' + String(e.filename || '').split('/').pop() + ':' + e.lineno;
    document.title = msg;
    const d = document.getElementById('dbg');
    if (d) { d.style.display = 'block'; d.textContent = msg; }
  });

  const PREVIEW = qs.has('bg') || qs.has('studio');
  const PREVIEW_DEMO = qs.has('demo');   // 预览时没歌也拿假数据出画面；OBS 里不要加这个参数

  const DEFAULT_STATE = {
    visible: { badge: true, banner: true, card: true, spectrum: true },
    palette: 'magenta',
    bannerPos: 'top',          /* top | bottom —— 决策① */
    coverShape: 'circle',      /* circle | square —— 决策② */
    spectrumMode: 'full',      /* full | narrow —— 决策④ */
    badgePos: 'bl',            /* bl | tl —— 角标放左下还是左上（右半边做过又删了） */
    cardScale: 1,              /* 0.75 ~ 1.5 —— 播放卡片等比缩放（锚点左下角） */
    cardSpectrum: true,        /* 卡片底部的小频谱（面板「卡片」次级菜单） */
    specX: 0,                  /* 大频谱：左边距（1920 坐标系） */
    specY: 0,                  /* 大频谱：距底边 */
    specScale: 1,              /* 大频谱：0.4 ~ 1.0 等比缩放（锚点左下） */
    /* 第十三轮：透明度 / 等比缩放 / 自由位置 */
    badgeOpacity: 1,           /* 角标透明度 0 ~ 1 */
    bannerOpacity: 1,          /* 条幅透明度 */
    cardOpacity: 1,            /* 卡片透明度 */
    badgeScale: 1,             /* 角标等比缩放 0.4 ~ 1.0（锚点左下） */
    bannerScale: 1,            /* 条幅等比缩放 0.4 ~ 1.0（锚点左下） */
    badgeX: 0, badgeY: 0,      /* 角标：左边距 / 距底边 */
    bannerX: 60, bannerY: 932, /* 条幅：左边距 / 距底边（顶部 60 + 高 88） */
    cardX: 60, cardY: 240,     /* 卡片：左边距 / 距底边（左下角标在时让位） */
    radius: 30,                /* 第十六轮：整体圆角 0 ~ 44（墨块 + 角标三角） */
    coverSpin: false,          /* 第十六轮：封面旋转（播放时转、暂停停） */
    autoPalette: false,        /* 第十六轮：跟随封面取色（每首重算，失败退回手动配色） */
    autoGrad: false,           /* 第十六轮：自动配色也用渐变（c1 → c2） */
    badgeSpectrum: true,       /* 角标头上的频谱是否可视化 */
    hideWhenIdle: true,
    demo: 'auto'
  };

  const el = {
    stage: $('stage'),
    banner: $('banner'), card: $('card'),
    badge: $('badge'), specLayer: $('specLayer'), canvas: $('spec'),
    cardSpec: $('cardSpec'),
    dbg: $('dbg')
  };

  const refs = {
    bannerTitle: $('bannerTitle'), bannerTitleBox: $('bannerTitleBox'),
    bannerArtist: $('bannerArtist'), bannerArtistBox: $('bannerArtistBox'),
    bannerFill: $('bannerFill'), bannerThumb: $('bannerThumb'),
    bannerEl: $('bannerEl'), bannerTot: $('bannerTot'),
    cardTitle: $('cardTitle'), cardTitleBox: $('cardTitleBox'),
    cardArtist: $('cardArtist'),
    cardFill: $('cardFill'), cardThumb: $('cardThumb'),
    cardEl: $('cardEl'), cardTot: $('cardTot'),
    bannerCover: $('bannerCover'), cardCover: $('cardCover')
  };

  let state = JSON.parse(JSON.stringify(DEFAULT_STATE));
  let stateKey = '';
  let now = null;
  const anchor = { pos: 0, at: Date.now(), playing: false, dur: 0, known: false, id: '' };
  let trackKey = '';
  let lastCover = '';
  let lastBadgePos = '';
  let lastRadius = -1;
  let spec = null;
  let specReal = false;
  let offline = false;
  let idle = true;

  /* ---------------- 基础工具 ---------------- */
  const pad2 = (n) => String(n).padStart(2, '0');
  const round2 = (n) => Math.round(n * 100) / 100;
  function clampNum(v, lo, hi, dflt) {
    const n = Number(v);
    if (!isFinite(n)) return dflt;
    return Math.min(hi, Math.max(lo, n));
  }

  /* 卡片等比缩放的范围（和 server.py 的 CARD_SCALE_MIN/MAX 保持一致） */
  const SCALE_MIN = 0.75, SCALE_MAX = 1.5;
  const clampScale = (v) => round2(clampNum(v, SCALE_MIN, SCALE_MAX, 1));

  /* 第十三轮：位置 / 透明度 / 等比缩放 —— 四个组件共用一套范围（和 server.py 一致）
     x = 左边距，y = **距底边**；缩放上限 = 当前默认大小（1.0×），只能缩不能放大 */
  const POS_X_MIN = -200, POS_X_MAX = 1200;
  const POS_Y_MIN = 0, POS_Y_MAX = 1080;
  const OPACITY_MIN = 0, OPACITY_MAX = 1;
  const COMP_SCALE_MIN = 0.4, COMP_SCALE_MAX = 1;
  const clampPosX = (v) => Math.round(clampNum(v, POS_X_MIN, POS_X_MAX, 0));
  const clampPosY = (v) => Math.round(clampNum(v, POS_Y_MIN, POS_Y_MAX, 0));
  const clampOpacity = (v) => round2(clampNum(v, OPACITY_MIN, OPACITY_MAX, 1));
  const clampCompScale = (v) => round2(clampNum(v, COMP_SCALE_MIN, COMP_SCALE_MAX, 1));
  /* 第十六轮：整体圆角 */
  const RADIUS_MIN = 0, RADIUS_MAX = 44;
  const clampRadius = (v) => Math.round(clampNum(v, RADIUS_MIN, RADIUS_MAX, 30));
  /* 大频谱沿用旧函数名：范围已经并到上面那套，别单独改 */
  const clampSpecX = clampPosX, clampSpecY = clampPosY, clampSpecScale = clampCompScale;
  function fmt(sec) {
    if (sec == null || !isFinite(sec) || sec < 0) return '--:--';
    sec = Math.floor(sec);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return h ? `${h}:${pad2(m)}:${pad2(s)}` : `${pad2(m)}:${pad2(s)}`;
  }

  /* ---------------- 演示数据（离线/预览兜底） ---------------- */
  function demoNow() {
    const dur = 248;
    const pos = (Date.now() / 1000) % dur;
    return {
      title: 'Neon Tokyo', artist: 'AMANE', album: '雨音',
      source: 'demo', playing: true, demo: true,
      position: pos, duration: dur, durationKnown: true,
      cover: ''
    };
  }

  function idleNow() {
    return {
      idle: true, playing: false, title: '', artist: '', album: '', source: '',
      position: 0, duration: 0, durationKnown: false, cover: ''
    };
  }

  /* ---------------- 配色（手动 / 跟随封面） ---------------- */
  let autoPal = null;      // 取色结果 { kind:'auto'|'preset', pal, hex, key }
  let autoView = null;     // 实际用的那套：开了「渐变」就在 pal 上多挂一个 grad:true
  let autoKey = '';        // 已经取过色的封面 URL

  function refreshAutoView() {
    autoView = (autoPal && state.autoGrad)
      ? Object.assign({}, autoPal.pal, { grad: true })
      : (autoPal ? autoPal.pal : null);
  }

  function paletteNow() {
    return (state.autoPalette && autoView)
      ? autoView
      : (window.PALETTES[state.palette] || window.PALETTES.magenta);
  }

  function applyPaletteNow() {
    const p = paletteNow();
    window.applyPalette(p);
    // 角标 SVG 里的渐变靠这个 class 切（CSS 只能改 stop-color）
    document.body.classList.toggle('grad-accent', !!(p && p.grad));
    if (spec) spec.setPalette(p);
  }

  /* 换歌 / 换封面才重新取色；开关关掉时清掉记忆，下次打开会重算 */
  function maybeAutoPalette(n) {
    if (!state.autoPalette || !window.AutoColor) return;
    const cover = n && n.cover;
    if (!cover || cover === autoKey) return;
    autoKey = cover;
    window.AutoColor.fromCover(cover).then((res) => {
      autoPal = res || null;
      refreshAutoView();
      if (res) {
        document.body.dataset.autoPal = res.hex || '';
        document.body.dataset.autoKind = res.kind;
      }
      applyPaletteNow();
    }).catch(() => { /* 取色失败就维持现状 */ });
  }

  /* ---------------- 状态应用 ---------------- */
  function applyState(s) {
    state = Object.assign({}, DEFAULT_STATE, s, {
      visible: Object.assign({}, DEFAULT_STATE.visible, s && s.visible)
    });

    // 查询串可以覆盖，方便截图验证各种组合
    ['palette', 'bannerPos', 'coverShape', 'spectrumMode', 'badgePos'].forEach((k) => {
      if (qs.has(k)) state[k] = qs.get(k);
    });
    // ?show= 要先于"按角落重排"生效：下面算避让时要用最终的可见性
    if (qs.has('show')) {
      const want = qs.get('show').split(',').map((s) => s.trim().toLowerCase());
      const all = want.includes('all');
      Object.keys(state.visible).forEach((k) => {
        state.visible[k] = all || want.includes(k);
      });
    }
    // 数值型覆盖：位置 / 缩放 / 透明度（截图验证各种组合用）
    [
      ['cardScale', clampScale], ['specScale', clampSpecScale],
      ['badgeScale', clampCompScale], ['bannerScale', clampCompScale],
      ['specX', clampPosX], ['specY', clampPosY],
      ['badgeX', clampPosX], ['badgeY', clampPosY],
      ['bannerX', clampPosX], ['bannerY', clampPosY],
      ['cardX', clampPosX], ['cardY', clampPosY],
      ['badgeOpacity', clampOpacity], ['bannerOpacity', clampOpacity], ['cardOpacity', clampOpacity],
      ['radius', clampRadius]
    ].forEach(([k, fn]) => { if (qs.has(k)) state[k] = fn(qs.get(k)); });
    if (qs.has('coverSpin')) state.coverSpin = qs.get('coverSpin') !== '0';
    if (qs.has('autoPalette')) state.autoPalette = qs.get('autoPalette') !== '0';
    if (qs.has('autoGrad')) state.autoGrad = qs.get('autoGrad') !== '0';
    // 只给了角落没给坐标：位置也跟到该角落的默认值
    if (qs.has('badgePos') && !qs.has('badgeX') && !qs.has('badgeY')) {
      const d = defaultRect('badge'); state.badgeX = d.x; state.badgeY = d.y;
      // 角标换边会改变"条幅该不该让位"，条幅也一起按默认重排
      if (!qs.has('bannerX') && !qs.has('bannerY')) {
        const db = defaultRect('banner'); state.bannerX = db.x; state.bannerY = db.y;
      }
    }
    if (qs.has('bannerPos') && !qs.has('bannerX') && !qs.has('bannerY')) {
      const d = defaultRect('banner'); state.bannerX = d.x; state.bannerY = d.y;
    }

    if (!state.autoPalette) autoKey = '';      // 关掉自动：下次打开重新取色
    refreshAutoView();                          // 「渐变」开关变了要重算实际用的那套
    applyPaletteNow();
    document.body.classList.toggle('shape-square', state.coverShape === 'square');
    document.body.classList.toggle('shape-circle', state.coverShape !== 'square');
    el.badge.classList.toggle('no-spec', !state.badgeSpectrum);

    // 角标角落：几何镜像，切换时要整块重画
    el.badge.classList.toggle('top', state.badgePos === 'tl');
    if (lastBadgePos !== state.badgePos) {
      lastBadgePos = state.badgePos;
      if (now) renderBadge(now);
    }

    // 第十六轮：整体圆角（CSS 变量 → 墨块/窄条）+ 角标三角圆角（重建几何后重画）+ 封面旋转开关
    const rad = clampRadius(state.radius);
    document.documentElement.style.setProperty('--radius', rad + 'px');
    document.body.classList.toggle('spin', !!state.coverSpin);
    if (window.Badge.setRadius) window.Badge.setRadius(rad);
    if (lastRadius !== rad) {
      lastRadius = rad;
      if (now) renderBadge(now);
    }

    layout();
  }

  /* ---------------- 布局（所有坐标只在这里算） ---------------- */
  /* 各组件"应有的默认位置"（1920×1080，y = 距底边）。
     等价于前十二轮的算法：同侧的角标开着时，条幅右移 540、卡片上移让位。
     ⚠️ control.js 的「重置位置」有一份同样的表，改这里要同步改那边。 */
  function defaultRect(kind) {
    const badgeOn = state.visible.badge;
    const badgeBL = badgeOn && state.badgePos === 'bl';
    const badgeTL = badgeOn && state.badgePos === 'tl';
    if (kind === 'banner') {
      const atBottom = state.bannerPos === 'bottom';
      const clash = atBottom ? badgeBL : badgeTL;   // 只有同边的角标才抢地方
      const top = atBottom ? 1080 - 88 - 36 : 60;
      return { x: clash ? 600 : 60, y: 1080 - top - 88 };
    }
    if (kind === 'card') return { x: 60, y: 1080 - (badgeBL ? 632 : 850) - 208 };
    if (kind === 'badge') return { x: 0, y: state.badgePos === 'tl' ? 1080 - 254 : 0 };
    return { x: 0, y: 0 };                          // 频谱：通栏贴底
  }

  function layout() {
    // ---------- 条幅：绝对位置 + 等比缩放（锚点左下）+ 透明度
    const bs = clampCompScale(state.bannerScale);
    el.banner.style.left = clampPosX(state.bannerX) + 'px';
    el.banner.style.bottom = clampPosY(state.bannerY) + 'px';
    el.banner.style.top = 'auto';
    el.banner.style.setProperty('--sc', bs);
    el.banner.style.setProperty('--op', clampOpacity(state.bannerOpacity));
    el.banner.style.setProperty('--hs', (1 / (bs * (stageScale || 1))).toFixed(3));

    // ---------- 卡片：同上（缩放范围不同：0.75 ~ 1.5）
    const cs = clampScale(state.cardScale);
    el.card.style.left = clampPosX(state.cardX) + 'px';
    el.card.style.bottom = clampPosY(state.cardY) + 'px';
    el.card.style.top = 'auto';
    el.card.style.setProperty('--sc', cs);
    el.card.style.setProperty('--op', clampOpacity(state.cardOpacity));
    el.card.style.setProperty('--hs', (1 / (cs * (stageScale || 1))).toFixed(3));
    el.card.classList.toggle('no-spec', !state.cardSpectrum);

    // ---------- 角标：几何镜像仍由 badge.js 按 badgePos 画，这里只管位置/缩放/透明度
    const gs = clampCompScale(state.badgeScale);
    el.badge.style.left = clampPosX(state.badgeX) + 'px';
    el.badge.style.bottom = clampPosY(state.badgeY) + 'px';
    el.badge.style.top = 'auto';
    el.badge.style.setProperty('--sc', gs);
    el.badge.style.setProperty('--op', clampOpacity(state.badgeOpacity));
    el.badge.style.setProperty('--hs', (1 / (gs * (stageScale || 1))).toFixed(3));

    // ---------- 频谱
    const layer = el.specLayer;
    const narrow = state.spectrumMode === 'narrow';
    layer.classList.toggle('narrow', narrow);
    layer.classList.toggle('full', !narrow);
    if (narrow) {
      // 窄条跟着条幅走（含条幅自身的缩放）
      const bw = Math.round(1000 * bs), bh = Math.round(88 * bs);
      const bottomEdge = 1080 - clampPosY(state.bannerY);
      const topEdge = bottomEdge - bh;
      layer.style.left = clampPosX(state.bannerX) + 'px';
      layer.style.width = bw + 'px';
      layer.style.height = '80px';
      if (bottomEdge + 80 > 1080 - 12) {           // 放不下就翻到条幅上方
        layer.style.top = (topEdge - 80) + 'px';
        layer.style.bottom = 'auto';
        layer.classList.add('flip');
      } else {
        layer.style.top = bottomEdge + 'px';
        layer.style.bottom = 'auto';
        layer.classList.remove('flip');
      }
    } else {
      // 通栏：自由位置（specX / specY）+ 等比缩放（specScale，锚点左下）
      const ss = clampSpecScale(state.specScale);
      layer.style.left = clampSpecX(state.specX) + 'px';
      layer.style.width = '1920px';
      layer.style.height = '140px';
      layer.style.top = 'auto';
      layer.style.bottom = clampSpecY(state.specY) + 'px';
      layer.style.setProperty('--ss', ss);
      layer.style.setProperty('--hs2', (1 / (ss * (stageScale || 1))).toFixed(3));
      layer.classList.remove('flip');
    }
  }

  /* ---------------- 组件渲染 ---------------- */
  function setText(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
  }

  /* 标题太长就来回滚：速度约 22px/s，两端各停一下。
     用 rAF 自己算位置（不用 CSS 动画）：好控制、后台标签页也不会乱跑。 */
  const marquees = [];

  function holdEase(k) {
    const a = 0.12;
    if (k <= a) return 0;
    if (k >= 1 - a) return 1;
    const x = (k - a) / (1 - 2 * a);
    return x * x * (3 - 2 * x);
  }

  function setupMarquee(box, inner) {
    if (!box || !inner) return;
    let rec = marquees.find((m) => m.inner === inner);
    if (!rec) { rec = { box, inner, dist: 0, dur: 1, start: 0 }; marquees.push(rec); }
    const dist = inner.scrollWidth - box.clientWidth;
    if (dist <= 2) {
      rec.dist = 0;
      inner.style.transform = '';
      box.classList.remove('marquee');
      return;
    }
    rec.dist = dist;
    rec.dur = Math.max(4.5, dist / 32 + 1.2);   // ~32px/s，走一趟两端各停一下
    rec.start = performance.now();
    box.classList.add('marquee');
  }

  function stepMarquees() {
    const now = performance.now();
    for (const m of marquees) {
      if (m.dist <= 0) {
        if (m.inner.style.transform) m.inner.style.transform = '';
        continue;
      }
      const t = (now - m.start) / 1000;
      const p = (t / m.dur) % 2;
      const k = p < 1 ? p : 2 - p;
      m.inner.style.transform = `translateX(${(-m.dist * holdEase(k)).toFixed(1)}px)`;
    }
  }

  function setCover(box, url) {    const img = box.querySelector('img');
    if (!url) {
      box.classList.remove('has-img');
      if (img) img.removeAttribute('src');
      return;
    }
    if (img.getAttribute('src') !== url) {
      img.onload = () => box.classList.add('has-img');
      img.onerror = () => box.classList.remove('has-img');
      img.src = url;
    }
  }

  function renderNow(n) {
    if (!n) return;
    if (qs.has('title')) n.title = qs.get('title');    // 调试：验证长标题滚动
    if (qs.has('artist')) n.artist = qs.get('artist');
    if (qs.has('cover')) n.cover = qs.get('cover');     // 调试：指定封面（验证跟随取色）
    // 调试：钉住进度，出图时六张图的时间码/进度条才会一致（OBS 里别加）
    if (qs.has('pos')) n.position = Math.max(0, Number(qs.get('pos')) || 0);
    if (qs.has('dur')) {
      n.duration = Math.max(1, Number(qs.get('dur')) || 0);
      n.durationKnown = true;
    }
    if (n.idle && PREVIEW_DEMO) n = demoNow();   // 控制台预览：没歌也给点东西看
    const wasIdle = idle;
    idle = !!n.idle;
    now = n;

    const playing = n.playing !== false;
    document.body.classList.toggle('playing', playing);   // 封面旋转靠它暂停
    const durKnown = !!n.durationKnown && isFinite(n.duration) && n.duration > 0;
    const key = [n.title, n.artist, n.album, n.cover].join('\u0001');

    // 时间基准：只在换歌 / 首次 / 漂移超 1.5s / 播放状态切换时重设。
    // 以前每次轮询都重置 anchor.at，位置就每 500ms 退回一次 —— 小球来回"鬼畜"就是这个。
    const nowMs = Date.now();
    const prevPlaying = anchor.playing;
    const expected = anchor.pos + (anchor.playing ? (nowMs - anchor.at) / 1000 : 0);
    let resync = key !== anchor.id || !anchor.known;
    if (!resync) {
      if (Math.abs(expected - (n.position || 0)) > 1.5) {
        resync = true;
      } else if (playing !== prevPlaying) {
        anchor.pos = Math.max(0, expected);        // 暂停/继续：就地冻结，不要跳
        anchor.at = nowMs;
      }
    }
    if (resync) {
      anchor.pos = Math.max(0, Number(n.position) || 0);
      anchor.at = nowMs;
      anchor.id = key;
      lastPos = anchor.pos;                        // 允许 seek 后往回走
    }
    anchor.playing = playing;
    anchor.dur = durKnown ? n.duration : 0;
    anchor.known = durKnown;

    if (key !== trackKey) {
      const first = !trackKey;
      trackKey = key;
      setText(refs.bannerTitle, n.title || '—');
      setText(refs.bannerArtist, [n.artist, n.album].filter(Boolean).join(' · ') || '—');
      setText(refs.cardTitle, n.title || '—');
      setText(refs.cardArtist, [n.artist, n.album].filter(Boolean).join(' · ') || '—');
      setupMarquee(refs.bannerTitleBox, refs.bannerTitle);
      setupMarquee(refs.bannerArtistBox, refs.bannerArtist);   // 作者超长也滚，不再省略号
      setupMarquee(refs.cardTitleBox, refs.cardTitle);
      setCover(refs.bannerCover, n.cover);
      setCover(refs.cardCover, n.cover);
      renderBadge(n);
      if (!first) swapAnim();
    } else if (n.cover !== lastCover) {
      setCover(refs.bannerCover, n.cover);
      setCover(refs.cardCover, n.cover);
    }
    lastCover = n.cover || '';
    maybeAutoPalette(n);      // 开了「跟随封面」才动，且只在封面变了时重算

    // 时长未知 -> 不确定态进度条（条幅现在没有进度条了，只处理卡片）
    [el.banner.querySelector('.bar'), el.card.querySelector('.bar')]
      .filter(Boolean)
      .forEach((b) => b.classList.toggle('indet', !durKnown));

    el.banner.classList.toggle('on', !idle && state.visible.banner);
    el.card.classList.toggle('on', !idle && state.visible.card);
    el.badge.classList.toggle('on', !idle && state.visible.badge);
    el.specLayer.classList.toggle('on', !idle && state.visible.spectrum);

    if (spec) spec.setPlaying(playing);
    // 底部频谱关了但角标频谱还开着时，引擎也得继续跑（角标要拿形状）
    const needSpec = !idle && (state.visible.spectrum || state.badgeSpectrum || state.cardSpectrum);
    if (needSpec) spec && spec.start();
    else spec && spec.stop();

    layout();
  }

  function swapAnim() {
    [el.banner, el.card].forEach((x) => {
      x.classList.remove('swapping');
      void x.offsetWidth;              // 强制重排，动画能重放
      x.classList.add('swapping');
    });
    setTimeout(() => [el.banner, el.card].forEach((x) => x.classList.remove('swapping')), 900);
  }

  function renderBadge(n) {
    el.badge.innerHTML = window.Badge.render(n, state.badgePos);
    const svg = el.badge.querySelector('svg');
    window.Badge.patch(svg, localPos(), anchor.dur, state.badgePos);
    // 量一下有没有超宽，超了就滚。带守卫：OBS 的 Chromium 可能缓存着旧 badge.js
    if (window.Badge.setupText) window.Badge.setupText(svg);
  }

  /* ---------------- 时间推进 ---------------- */
  let lastPos = 0;
  let dbgBack = 0;

  function localPos() {
    if (!anchor.known && !now) return 0;
    let p = anchor.pos + (anchor.playing ? (Date.now() - anchor.at) / 1000 : 0);
    if (anchor.dur > 0) p = Math.min(p, anchor.dur);
    p = Math.max(0, p);
    // 同一段里绝不倒退（seek 会走 resync 并把 lastPos 重置）
    if (p < lastPos) { dbgBack++; return lastPos; }
    lastPos = p;
    return p;
  }

  let lastPaint = 0, lastBadge = 0, lastBadgeSpec = 0;
  function frame(t) {
    requestAnimationFrame(frame);

    // 角标频谱：独立 30fps 节流，放在下面 45ms 动画闸门之前。
    // 以前它在闸门之后又卡 100ms，实际只有 ~8.5fps。
    if (!idle && state.visible.badge && state.badgeSpectrum && spec &&
        t - lastBadgeSpec >= 32) {
      lastBadgeSpec = t;
      const svg = el.badge.querySelector('svg');
      if (svg) window.Badge.setBars(svg, spec.getShape(window.Badge.BAR_COUNT), state.badgePos);
    }

    if (t - lastPaint < 45) return;        // 其余动画 ~22fps 够顺，省 CPU
    lastPaint = t;
    const pos = localPos();
    const pct = anchor.dur > 0 ? Math.min(100, (pos / anchor.dur) * 100) : 0;

    if (anchor.known) {
      const w = pct.toFixed(2) + '%';
      if (refs.bannerFill) refs.bannerFill.style.width = w;
      if (refs.bannerThumb) refs.bannerThumb.style.left = w;
      if (refs.cardFill) refs.cardFill.style.width = w;
      if (refs.cardThumb) refs.cardThumb.style.left = w;
    } else {
      // 时长未知：交给 CSS 的不确定态动画，别用内联宽度覆盖它
      [refs.bannerFill, refs.cardFill].forEach((x) => x && (x.style.width = ''));
      [refs.bannerThumb, refs.cardThumb].forEach((x) => x && (x.style.left = ''));
    }
    setText(refs.bannerEl, fmt(pos));
    setText(refs.cardEl, fmt(pos));
    setText(refs.bannerTot, fmt(anchor.known ? anchor.dur : NaN));
    setText(refs.cardTot, fmt(anchor.known ? anchor.dur : NaN));

    if (t - lastBadge > 100) {             // 进度条/时间码 10fps 就够（频谱在上面单独 30fps）
      lastBadge = t;
      window.Badge.patch(el.badge.querySelector('svg'), pos, anchor.dur, state.badgePos);
    }

    drawCardSpec();
    stepMarquees();
    // 角标的歌名/作者超宽时来回滚（同样带守卫）
    if (window.Badge.stepTextMarquees) window.Badge.stepTextMarquees(t);
    stepCoverSpin(t);
  }

  /* 第十六轮：角标封面旋转 —— 绕封面圆心（SVG 里只改一个 transform 属性，最省）
     暂停时保持当前角度；换歌会重画 SVG，角度接着转不跳回 0。 */
  let spinAngle = 0, spinLast = 0;
  function stepCoverSpin(t) {
    const g = el.badge.querySelector('.b-cover-rot');
    if (!g) return;
    // 圆角矩形封面不支持旋转（转起来像个转动的窗口）
    if (!state.coverSpin || state.coverShape === 'square') {
      if (spinAngle) { g.removeAttribute('transform'); spinAngle = 0; }
      spinLast = 0;
      return;
    }
    if (!anchor.playing) { spinLast = 0; return; }        // 暂停：停住
    if (!spinLast) spinLast = t;
    spinAngle = (spinAngle + (t - spinLast) / 7000 * 360) % 360;
    spinLast = t;
    const cx = g.getAttribute('data-cx'), cy = g.getAttribute('data-cy');
    g.setAttribute('transform', `rotate(${spinAngle.toFixed(2)},${cx},${cy})`);
  }

  /* 卡片底部的小频谱：直接吃大频谱那台的形状（条高上限 30px，和角标一致） */  function drawCardSpec() {
    const cv = el.cardSpec;
    if (!cv || !state.cardSpectrum || !spec || idle) return;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const bw = Math.round(w * dpr), bh = Math.round(h * dpr);
    if (cv.width !== bw || cv.height !== bh) { cv.width = bw; cv.height = bh; }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const n = Math.max(16, Math.floor(w / 9));
    const shape = spec.getShape(n);
    // 配色跟着「跟随封面」走（以前这里只认手动配色表，自动配色时卡片频谱会不同步）
    const pal = paletteNow() || {};
    const hexRgb = (hstr, dflt) => {
      const s = String(hstr || '').replace('#', '');
      if (s.length !== 6) return dflt;
      return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
    };
    const base = hexRgb(pal.acc, [255, 45, 149]);
    const acc = pal.accRgb || base.join(',');
    const grad = !!pal.grad;
    const alt = grad ? hexRgb(pal.c2, base) : base;
    const step = w / n;
    ctx.lineCap = 'round';
    ctx.lineWidth = 3;
    for (let i = 0; i < n; i++) {
      const v = Math.max(0.04, shape[i] || 0);
      const barH = Math.max(2, v * h);        // 上限就是 h（30px）
      const x = i * step + step / 2;
      const alpha = (0.25 + 0.6 * v).toFixed(3);
      if (grad) {
        const t2 = n > 1 ? i / (n - 1) : 0;
        const c = `${(base[0] + (alt[0] - base[0]) * t2) | 0},` +
                  `${(base[1] + (alt[1] - base[1]) * t2) | 0},` +
                  `${(base[2] + (alt[2] - base[2]) * t2) | 0}`;
        ctx.strokeStyle = `rgba(${c},${alpha})`;
      } else {
        ctx.strokeStyle = `rgba(${acc},${alpha})`;
      }
      ctx.beginPath();
      ctx.moveTo(x, h);
      ctx.lineTo(x, h - barH);
      ctx.stroke();
    }
  }

  /* ---------------- 真实频谱（SSE 推 PCM，页面自己 FFT） ---------------- */
  const NOSSE = qs.has('nosse');        // 截图/省 CPU 时可以关掉长连接
  let fft = null;                       // window.FFT.Analyzer（js/fft.js）
  let fftAt = 0;                        // 上一帧时间戳（算 dt 用）

  function decodePcm(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Int16Array(bytes.buffer, 0, bytes.length >> 1);
  }

  function analyzePcm(b64, rate) {
    const now = performance.now();
    const dt = fftAt ? (now - fftAt) / 1000 : 1 / 60;
    fftAt = now;
    fft.setRate(rate);
    return fft.analyze(decodePcm(b64), dt);
  }

  function connectSpectrum() {
    if (NOSSE) {
      // 截图/调试：?pcm=1 单次拉 PCM（不开 SSE），真实频谱也能出图
      if (qs.has('pcm') && window.FFT) {
        fft = fft || new window.FFT.Analyzer(2048, 64);
        const tick = async () => {
          try {
            const r = await fetch('/api/pcm?_=' + Date.now(), { cache: 'no-store' });
            const d = await r.json();
            if (d && d.ok && d.b) {
              spec.setLevels(analyzePcm(d.b, d.r));
              specReal = true;
            }
          } catch (e) { /* 服务没起就保持模拟 */ }
        };
        tick();
        setInterval(tick, 700);
      }
      // 调试：?levels=0.1,0.9,... 直接喂固定频段，用来验证真实数据那条渲染路径
      const raw = qs.get('levels');
      if (raw) {
        const arr = raw.split(',').map(Number).filter((n) => isFinite(n));
        if (arr.length) {
          specReal = true;
          setInterval(() => spec.setLevels(arr), 400);
        }
      }
      return;
    }
    if (!window.EventSource || !spec || !window.FFT) return;
    fft = new window.FFT.Analyzer(2048, 64);
    try {
      const es = new EventSource('/api/pcm-stream');
      es.onmessage = (ev) => {
        try {
          const d = JSON.parse(ev.data);
          if (d && d.ok && d.b) {
            spec.setLevels(analyzePcm(d.b, d.r));
            specReal = true;
          } else {
            specReal = false;
            spec.setLevels(null);
          }
        } catch (e) { /* 忽略坏帧 */ }
      };
      es.onerror = () => { specReal = false; spec.setLevels(null); };
    } catch (e) {
      specReal = false;
    }
  }

  /* ---------------- 数据 ---------------- */
  async function poll() {
    let snap = null;
    try {
      const r = await fetch('/api/snapshot?_=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      snap = await r.json();
      offline = false;
    } catch (e) {
      offline = true;
    }

    if (offline) {
      renderNow((PREVIEW || PREVIEW_DEMO) ? demoNow() : idleNow());
      if (qs.has('debug')) {
        el.dbg.textContent = 'offline · 内置演示数据（未连服务）';
        document.body.classList.add('debug');
      }
      return;
    }

    if (snap.state) {
      const k = JSON.stringify(snap.state);
      if (k !== stateKey) { stateKey = k; applyState(snap.state); }
    }
    if (snap.now) renderNow(snap.now);

    if (qs.has('debug')) {
      document.body.classList.add('debug');
      const b = snap.bridge || {};
      const mqEl = document.querySelector('.t1.marquee > span');
      let mq = '-';
      if (mqEl) {
        try {
          const m = new DOMMatrixReadOnly(getComputedStyle(mqEl).transform);
          mq = Math.round(m.m41) + 'px';
        } catch (e) { mq = '?'; }
      }
      el.dbg.textContent =
        `bridge: ${b.ok ? 'ok' : 'off'} · age ${b.ageMs == null ? '-' : Math.round(b.ageMs) + 'ms'} · ` +
        `src ${b.source || '-'} · demo ${now && now.demo ? 'Y' : 'N'} · ` +
        `spec ${specReal ? 'real' : 'sim'} · mq ${mq} · pos ${localPos().toFixed(2)} · back ${dbgBack} · ` +
        `${state.bannerPos}/${state.coverShape}/${state.spectrumMode}/${state.badgePos}`;
    }
  }

  /* ---------------- 缩放适配（OBS 源不是 1920 也不用改） ---------------- */
  let stageScale = 1;          // 拖拽换算要用：屏幕像素 → 1920 坐标系
  function fit() {
    const s = Math.min(innerWidth / 1920, innerHeight / 1080);
    const x = (innerWidth - 1920 * s) / 2;
    const y = (innerHeight - 1080 * s) / 2;
    stageScale = s;
    el.stage.style.transform = `translate(${x.toFixed(2)}px,${y.toFixed(2)}px) scale(${s.toFixed(4)})`;
  }

  /* ---------------- 编辑（只在控制面板的预览里：?edit=1） ----------------
     第十三轮：条幅 / 卡片 / 角标 都能「拖本体移动 + 拖右上角圆点缩放」；
     频谱另有自己的一套（拖本体移动 + 右下角缩放）。
     OBS 打开的 index.html 没有 edit=1，不生成手柄，也不吃鼠标事件。 */
  const EDIT = qs.has('edit');

  const EDITEES = {
    banner: { label: '条幅', get: () => el.banner, w: 1000, h: 88,
              scaleKey: 'bannerScale', xKey: 'bannerX', yKey: 'bannerY',
              sMin: COMP_SCALE_MIN, sMax: COMP_SCALE_MAX },
    card:   { label: '卡片', get: () => el.card, w: 420, h: 208,
              scaleKey: 'cardScale', xKey: 'cardX', yKey: 'cardY',
              sMin: SCALE_MIN, sMax: SCALE_MAX },
    badge:  { label: '角标', get: () => el.badge, w: 564, h: 254,
              scaleKey: 'badgeScale', xKey: 'badgeX', yKey: 'badgeY',
              sMin: COMP_SCALE_MIN, sMax: COMP_SCALE_MAX }
  };

  function initEditor() {
    if (!EDIT) return;
    document.body.classList.add('editing');
    Object.keys(EDITEES).forEach((k) => makeEditable(k, EDITEES[k]));
    initSpecEditor();
  }

  /* 拖完落盘：顺手 postMessage 给控制面板，让那边的读数/滑杆跟手（同源才发） */
  async function savePatch(key, patch) {
    if (window.parent && window.parent !== window) {
      try { window.parent.postMessage({ type: 'edit', key, patch }, location.origin); }
      catch (err) { /* 忽略 */ }
    }
    try {
      await fetch('/api/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch)
      });
    } catch (err) { /* 服务没起就只留在本地 */ }
  }

  function makeEditable(key, ed) {
    const node = ed.get();
    if (!node) return;
    const handle = document.createElement('div');
    handle.className = 'edit-handle';
    handle.title = `拖动改${ed.label}大小（${ed.sMin}×～${ed.sMax}×）`;
    const readout = document.createElement('div');
    readout.className = 'edit-readout';
    node.append(handle, readout);

    const show = (text) => { readout.textContent = text; readout.classList.add('on'); };
    const st = () => stageScale || 1;
    let move = null, scale = null, hideTimer = 0;
    const hideLater = () => {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => readout.classList.remove('on'), 900);
    };

    // 拖本体 = 移动位置
    node.addEventListener('pointerdown', (e) => {
      if (e.target === handle) return;
      e.preventDefault();
      node.setPointerCapture(e.pointerId);
      node.classList.add('dragging');
      move = { x: e.clientX, y: e.clientY,
               x0: clampPosX(state[ed.xKey]), y0: clampPosY(state[ed.yKey]), moved: false };
      clearTimeout(hideTimer);
      show(`位置 ${move.x0}, ${move.y0}`);
    });

    node.addEventListener('pointermove', (e) => {
      if (!move) return;
      const dx = (e.clientX - move.x) / st(), dy = (e.clientY - move.y) / st();
      state[ed.xKey] = clampPosX(move.x0 + dx);
      state[ed.yKey] = clampPosY(move.y0 - dy);      // 往上拖 = 离底边更远
      if (Math.abs(state[ed.xKey] - move.x0) > 1 || Math.abs(state[ed.yKey] - move.y0) > 1) move.moved = true;
      node.style.left = state[ed.xKey] + 'px';
      node.style.bottom = state[ed.yKey] + 'px';
      show(`位置 ${state[ed.xKey]}, ${state[ed.yKey]}`);
    });

    const endMove = (e) => {
      if (!move) return;
      try { node.releasePointerCapture(e.pointerId); } catch (err) { /* 已释放 */ }
      node.classList.remove('dragging');
      if (move.moved) {
        savePatch(key, { [ed.xKey]: state[ed.xKey], [ed.yKey]: state[ed.yKey] });
      }
      move = null;
      hideLater();
    };
    node.addEventListener('pointerup', endMove);
    node.addEventListener('pointercancel', endMove);

    // 右上角圆点 = 等比缩放（锚点左下角，往右拖变大）
    handle.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      handle.setPointerCapture(e.pointerId);
      node.classList.add('dragging');       // 拖的时候关掉过渡，不然缩放跟手会飘
      scale = { x: e.clientX, y: e.clientY,
                s0: clampNum(state[ed.scaleKey], ed.sMin, ed.sMax, 1), moved: false };
      clearTimeout(hideTimer);
      show(scale.s0.toFixed(2) + '×');
    });

    handle.addEventListener('pointermove', (e) => {
      if (!scale) return;
      const dx = (e.clientX - scale.x) / st(), dy = (e.clientY - scale.y) / st();
      // 卡片沿用老手感（两轴取平均）；条幅/角标按各自宽度归一：拖出一整个身位 = 1.0 倍
      const raw = key === 'card'
        ? scale.s0 + (dx / ed.w - dy / ed.h) / 2
        : scale.s0 + dx / ed.w;
      const s = round2(clampNum(raw, ed.sMin, ed.sMax, scale.s0));
      if (Math.abs(s - state[ed.scaleKey]) > 0.004) scale.moved = true;
      state[ed.scaleKey] = s;
      node.style.setProperty('--sc', s);
      node.style.setProperty('--hs', (1 / (s * st())).toFixed(3));
      show(s.toFixed(2) + '×');
    });

    const endScale = (e) => {
      if (!scale) return;
      try { handle.releasePointerCapture(e.pointerId); } catch (err) { /* 已释放 */ }
      node.classList.remove('dragging');
      if (scale.moved) savePatch(key, { [ed.scaleKey]: state[ed.scaleKey] });
      scale = null;
      hideLater();
    };
    handle.addEventListener('pointerup', endScale);
    handle.addEventListener('pointercancel', endScale);
  }

  /* 大频谱的编辑：拖本体移动、拖右下角圆点缩放（只对通栏模式生效） */
  function initSpecEditor() {
    const layer = el.specLayer;
    if (!layer) return;
    const grip = document.createElement('div');
    grip.className = 'spec-handle';
    grip.title = '拖动改频谱大小（0.4×～1.5×）';
    const readout = document.createElement('div');
    readout.className = 'spec-readout';
    layer.append(grip, readout);

    const showReadout = (text) => {
      readout.textContent = text;
      readout.classList.add('on');
    };
    let hideTimer = 0, move = null, scale = null;

    const hideLater = () => {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => readout.classList.remove('on'), 900);
    };
    const stageDx = (e, d) => (e.clientX - d.x) / (stageScale || 1);
    const stageDy = (e, d) => (e.clientY - d.y) / (stageScale || 1);

    // 拖本体 = 移动位置
    layer.addEventListener('pointerdown', (e) => {
      if (state.spectrumMode !== 'full' || e.target === grip) return;
      e.preventDefault();
      layer.setPointerCapture(e.pointerId);
      move = { x: e.clientX, y: e.clientY, sx: clampSpecX(state.specX), sy: clampSpecY(state.specY), moved: false };
      clearTimeout(hideTimer);
      showReadout(`位置 ${move.sx}, ${move.sy}`);
    });

    layer.addEventListener('pointermove', (e) => {
      if (!move) return;
      const dx = stageDx(e, move), dy = stageDy(e, move);
      state.specX = clampSpecX(move.sx + dx);
      state.specY = clampSpecY(move.sy - dy);      // 往上拖 = 离底边更远
      if (Math.abs(state.specX - move.sx) > 1 || Math.abs(state.specY - move.sy) > 1) move.moved = true;
      layer.style.left = state.specX + 'px';
      layer.style.bottom = state.specY + 'px';
      showReadout(`位置 ${state.specX}, ${state.specY}`);
    });

    const endMove = (e) => {
      if (!move) return;
      try { layer.releasePointerCapture(e.pointerId); } catch (err) { /* 已释放 */ }
      if (move.moved) commit({ specX: state.specX, specY: state.specY });
      move = null;
      hideLater();
    };
    layer.addEventListener('pointerup', endMove);
    layer.addEventListener('pointercancel', endMove);

    // 右下角圆点 = 等比缩放（锚点在左下，往右 / 往上都变大）
    grip.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      grip.setPointerCapture(e.pointerId);
      scale = { x: e.clientX, y: e.clientY, s0: clampSpecScale(state.specScale), moved: false };
      clearTimeout(hideTimer);
      showReadout(round2(scale.s0).toFixed(2) + '×');
    });

    grip.addEventListener('pointermove', (e) => {
      if (!scale) return;
      const dx = stageDx(e, scale), dy = stageDy(e, scale);
      const s = clampSpecScale(scale.s0 + (dx - dy) / 700);
      if (Math.abs(s - state.specScale) > 0.004) scale.moved = true;
      state.specScale = round2(s);
      layer.style.setProperty('--ss', state.specScale);
      layer.style.setProperty('--hs2', (1 / (state.specScale * (stageScale || 1))).toFixed(3));
      showReadout(state.specScale.toFixed(2) + '×');
    });

    const endScale = (e) => {
      if (!scale) return;
      try { grip.releasePointerCapture(e.pointerId); } catch (err) { /* 已释放 */ }
      if (scale.moved) commit({ specScale: state.specScale });
      scale = null;
      hideLater();
    };
    grip.addEventListener('pointerup', endScale);
    grip.addEventListener('pointercancel', endScale);

    async function commit(patch) {
      try {
        await fetch('/api/state', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch)
        });
      } catch (err) { /* 服务没起就只留在本地 */ }
    }
  }

  /* ---------------- 启动 ---------------- */
  function init() {
    if (qs.has('bg') || qs.has('studio')) document.body.classList.add('studio');
    if (qs.has('debug')) document.body.classList.add('debug');
    if (qs.has('instant')) document.body.classList.add('instant');

    applyPalette('magenta');
    spec = new window.Spectrum(el.canvas);
    spec.setPalette(window.PALETTES[state.palette]);

    fit();
    addEventListener('resize', () => { fit(); layout(); });

    applyState(JSON.parse(JSON.stringify(DEFAULT_STATE)));   // 查询串覆盖也在这里生效
    // 先画一版，防止首屏空白：预览模式给演示数据，OBS 里就空着等真数据
    renderNow((PREVIEW || PREVIEW_DEMO) ? demoNow() : idleNow());
    initEditor();
    connectSpectrum();
    requestAnimationFrame(frame);

    poll();
    setInterval(poll, 500);
  }

  // 配色变了要让频谱跟上（applyState 内部已经通过 applyPaletteNow 处理，这里不用再包一层）

  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', init);
  else init();
})();
