/* 角标（左下 / 左上）—— 与 projects/corner-badge/build_badge.py 同一套几何
   多角形式抄的是 history/four-corners.html 的方案：**几何镜像、字形不镜像**；
   上角额外下沉 12px 修正文字爬顶（cover 118 → 84）。
   右半边（br / tr）做过又删了：左→右的阅读习惯改不掉，右侧角标怎么调都别扭（2026-10-27 决定）。
   静态成品仍是"定稿"参考，不要在这里改设计，只改数据绑定。 */
(function () {
  const W = 500, H = 190, PAD = 32;
  const CR = 30, R = 32, R_OUT = 35, HALO = 76;
  let CR_CUR = CR;                 // 当前圆角（面板滑块可改，0 就是直角）
  const XOFF = 42, NOTE = 22;

  /* 斜边长度：左下 (0,18)→(500,190)；左上 (0,172)→(500,0)，长度相同 */
  const L = Math.hypot(W, H - 18);

  /* 与生成器同款伪随机（种子同 20261025，形态一致） */
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* 左上角定稿位置：封面往左下让，文字贴到封面右侧（和左下角同款间距） */
  const TL_COVER = [64, 75];
  const TL_TEXT = [64, 77];
  /* 左下角定稿位置（和生成器一致） */
  const BL_COVER = [74, 118];

  const CORNERS = ['bl', 'tl'];

  /* 文字盒子离形状边缘的最小垂直距离（500×190 坐标系里的 px）
     文字是沿斜边方向排的（19°），越往右离底边/顶边越近，
     所以「能写多宽」必须按几何算，不能拍脑袋给固定值。 */
  const TEXT_MARGIN = 14;

  /* 字体必须和 overlay.css 的 .b-t1 / .b-t2 一致；字距也要算进宽度，
     否则量出来的"放得下"比实际窄（2.2px × 字数，长歌手名能差 40px）。 */
  const TITLE_FONT = '700 18px "Segoe UI","Microsoft YaHei",sans-serif';
  const ART_FONT = '700 9.5px "Segoe UI","Microsoft YaHei",sans-serif';
  const TITLE_LS = 0.2, ART_LS = 2.2;

  /* 字形超出基线的上下量（ascent / descent）。
     盒子宽度必须按**字形**算：只按基线算的话，末尾那几个字的头顶会顶出斜边
     （十六轮之后用户看到的"文字溢出"就是这么来的）。 */
  let FONT_MET = null;
  function fontMetrics() {
    if (FONT_MET) return FONT_MET;
    let got = null;
    try {
      const c = document.createElement('canvas').getContext('2d');
      const probe = '游戏Agjyq《》';
      const ve = (font) => {
        c.font = font;
        const m = c.measureText(probe);
        if (!m || !isFinite(m.width)) return null;
        return {
          asc: Math.max(2, m.actualBoundingBoxAscent || 0),
          desc: Math.max(1, m.actualBoundingBoxDescent || 0),
        };
      };
      const a = ve(TITLE_FONT), b = ve(ART_FONT);
      if (a && b) got = { t1: a, t2: b };
    } catch (e) {
      got = null;                       // 假 DOM（Node 测试）没有 canvas：走兜底值
    }
    FONT_MET = got || { t1: { asc: 15, desc: 3 }, t2: { asc: 8, desc: 2 } };
    return FONT_MET;
  }

  /* 一个角一套几何：A/B 是斜边两端，NX/NY 指向形状内部。
     over 只给对比页用：{ cx, cy } 单独挪左上封面。 */
  function mkGeom(corner, over) {
    over = over || {};
    const top = corner === 'tl';
    const A = top ? [0, H - 18] : [0, 18];
    const B = top ? [W, 0] : [W, H];
    const UX = (B[0] - A[0]) / L, UY = (B[1] - A[1]) / L;
    // 指向形状内部的法线：左下 (-b, a)，左上 (b, -a)
    const NX = top ? UY : -UY, NY = top ? -UX : UX;
    const ANG = Math.atan2(UY, UX) * 180 / Math.PI;

    const TRI = top
      ? `M0,${H - 18} L0,${CR_CUR} Q0,0 ${CR_CUR},0 L${W},0 Z`
      : `M0,18 L0,${H - CR_CUR} Q0,${H} ${CR_CUR},${H} L${W},${H} Z`;
    const LEGS = TRI.slice(0, -2);        // 去掉 " Z"：只描竖边和长边

    // 封面 / 文字：左上单独一套定稿值，左下和生成器一致
    const base = top ? TL_COVER : BL_COVER;
    const baseText = top ? TL_TEXT : BL_COVER;
    const cx0 = (top && typeof over.cx === 'number') ? over.cx : base[0];
    const cy0 = (top && typeof over.cy === 'number') ? over.cy : base[1];
    const cover = [cx0, cy0];
    const text = baseText.slice();

    /* 上角：歌名在下、作者在上；间距和左下视觉一致 */
    const textY = top ? { t1: 12, t2: -11 } : { t1: -8, t2: 11 };

    /* 这条线最多能往右写多宽：**字形的上下沿**要离底边（左下）/ 顶边（左上）还有 TEXT_MARGIN。
       局部点 x 旋转后：gy = ty + sin*x + cos*lineY  →  解 gy + cos*(-asc) ≤ H-margin（或 ≥ margin）
       （老公式只算了基线，glyph 的 ascent 没算进去 → 末尾的字头顶出边缘） */
    const rad = ANG * Math.PI / 180;
    const sinA = Math.sin(rad), cosA = Math.cos(rad);
    const met = fontMetrics();
    function run(lineY, asc, desc) {
      const ty0 = text[1];
      const atEdge = top
        ? (ty0 + cosA * (lineY - asc) - TEXT_MARGIN) / (-sinA)
        : (H - TEXT_MARGIN - ty0 - cosA * (lineY + desc)) / sinA;
      return Math.max(40, Math.round(atEdge - XOFF));
    }
    const t1Max = run(textY.t1, met.t1.asc, met.t1.desc);   // 歌名可用宽度
    const t2Max = run(textY.t2, met.t2.asc, met.t2.desc);   // 作者可用宽度（更低/更高，通常更窄）

    /* 文字可见区的左端（局部 x）：必须让开封面圆，否则滚动时第一个字会从
       封面右边"钻"出来一截（用户截图里那个半截字）。 */
    const c0 = Math.cos(-rad), s0 = Math.sin(-rad);
    const dl = [cover[0] - text[0], cover[1] - text[1]];
    const coverLocalX = c0 * dl[0] - s0 * dl[1];
    const boxL = Math.max(XOFF - 16, Math.round(coverLocalX + R + 2));

    const bars = [];
    for (let d = 3; d < L - 3; d += 4.6) {
      const tt = d / L;
      bars.push({
        px: A[0] + UX * d,
        py: A[1] + UY * d,
        env: Math.min(1, Math.min(tt, 1 - tt) * 14 + 0.12),
      });
    }

    // 时间码固定两端：左=已播放、右=总长，往形状内让 18px
    const TL = [A[0] + UX * 16 + NX * 18, A[1] + UY * 16 + NY * 18];
    const TR = [A[0] + UX * 452 + NX * 18, A[1] + UY * 452 + NY * 18];

    return {
      top, A, B, UX, UY, NX, NY, ANG, TRI, LEGS, cover, text, bars, TL, TR,
      textY, t1Max, t2Max, boxL,
    };
  }

  const GEOM = {};
  CORNERS.forEach((c) => { GEOM[c] = mkGeom(c); });
  const BAR_COUNT = GEOM.bl.bars.length;

  /* 圆角滑块：改完重建两角几何（只有 TRI 里的圆角变了，A/B/NX/NY 和文字位置不受影响） */
  function setRadius(r) {
    const v = Math.round(Number(r));
    if (!isFinite(v) || v === CR_CUR) return;
    CR_CUR = Math.max(0, Math.min(44, v));
    CORNERS.forEach((c) => { GEOM[c] = mkGeom(c); });
  }

  /* 没有实时数据时的静态形（和生成器同款手感） */
  const rnd = mulberry32(20261025);
  const STATIC_SHAPE = GEOM.bl.bars.map(() => (2 + rnd() * 28) / 30);

  function barsPath(g, shape) {
    let s = '';
    for (let i = 0; i < g.bars.length; i++) {
      const b = g.bars[i];
      const v = shape ? shape[i] : STATIC_SHAPE[i];
      const h = Math.max(1.5, (v || 0) * 30 * b.env);
      s += `M${b.px.toFixed(1)},${b.py.toFixed(1)}`
         + `L${(b.px - g.NX * h).toFixed(1)},${(b.py - g.NY * h).toFixed(1)}`;
    }
    return s;
  }

  /* 外部每帧喂 0..1 的频谱形状（长度 = BAR_COUNT） */
  function setBars(svg, shape, corner) {
    if (!svg || !shape || !shape.length) return;
    const g = GEOM[corner] || GEOM.bl;
    const d = barsPath(g, shape);
    const a = svg.querySelector('#b-bars-accent');
    const u = svg.querySelector('#b-bars-under');
    if (a) a.setAttribute('d', d);
    if (u) u.setAttribute('d', d);
  }

  /* 文字测量：截断和"要不要滚"都靠它（和 SVG 里同字体同字号同字距） */
  let mctx = null;
  function measure(text, font, ls) {
    if (!mctx) mctx = document.createElement('canvas').getContext('2d');
    mctx.font = font;
    const s = text || '';
    return mctx.measureText(s).width + Math.max(0, s.length - 1) * (ls || 0);
  }
  function ellipsis(text, font, maxW) {
    if (!text) return '';
    if (!mctx) mctx = document.createElement('canvas').getContext('2d');
    mctx.font = font;
    if (mctx.measureText(text).width <= maxW) return text;
    let lo = 0, hi = text.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (mctx.measureText(text.slice(0, mid) + '…').width <= maxW) lo = mid; else hi = mid - 1;
    }
    return text.slice(0, lo) + '…';
  }

  function fmt(s) {
    if (s == null || !isFinite(s) || s < 0) return '--:--';
    s = Math.floor(s);
    return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
  }

  function render(data, corner, over) {
    const key = CORNERS.indexOf(corner) >= 0 ? corner : 'bl';
    const g = over === undefined ? GEOM[key] : mkGeom(key, over);
    const [cx, cy] = g.cover;
    const [tx, ty] = g.text;
    // 不再按硬编码宽度截断：整串都放进去，盒子（clipPath）负责裁，
    // 超出的部分交给 setupText / stepTextMarquees 来回滚（和卡片一致）
    const title = data.title || '—';
    const artist = [data.artist, data.album].filter(Boolean).join(' · ') || '—';
    const titleW = measure(title, TITLE_FONT, TITLE_LS);
    const artistW = measure(artist, ART_FONT, ART_LS);
    // 放得下就完全不套 mask：不滚动时任何淡出都会啃掉首尾字母
    // （+4 是宽容量：只超出 1~4px 的不值得来回滚，仍在 14px 安全边距之内）
    const t1Scroll = titleW > g.t1Max + 4;
    const t2Scroll = artistW > g.t2Max + 4;
    const cover = data.cover
      ? `<g class="b-cover-rot" data-cx="${cx}" data-cy="${cy}"><image href="${data.cover}" x="${cx - R}" y="${cy - R}" width="${R * 2}" height="${R * 2}"
                preserveAspectRatio="xMidYMid slice" clip-path="url(#b-cc)"/></g>`
      : '';

    /* 文字盒子的 mask + 渐变（文字局部坐标）。
       左端 boxL 让开封面、右端 x0+max 是不透明的安全边界，再往外 FADE 淡出。
       滚动到头时最后一个字正好停在 R 上，所以不会出现"半个字卡在边上"。 */
    function textMask(n, lineY, x0, max, dy, h) {
      const L = g.boxL, R = x0 + max;
      const FADE = 22;                     // 尾巴的淡出长度（太短会像被刀切）
      const span = Math.max(1, R + FADE - L);
      const a1 = Math.max(0, Math.min(1, (x0 - L) / span)).toFixed(4);
      const a2 = Math.max(0, Math.min(1, (R - L) / span)).toFixed(4);
      return `<mask id="b-tm${n}" maskUnits="userSpaceOnUse" x="${L}" y="${lineY + dy}" width="${span}" height="${h}">
      <rect x="${L}" y="${lineY + dy}" width="${span}" height="${h}" fill="url(#b-tf${n})"/>
    </mask>
    <linearGradient id="b-tf${n}" gradientUnits="userSpaceOnUse" x1="${L}" y1="0" x2="${R + FADE}" y2="0">
      <stop offset="0" stop-color="#fff" stop-opacity="0"/>
      <stop offset="${a1}" stop-color="#fff"/>
      <stop offset="${a2}" stop-color="#fff"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </linearGradient>`;
    }

    return `<svg class="badge-svg" width="${W + PAD * 2}" height="${H + PAD * 2}"
      viewBox="${-PAD} ${-PAD} ${W + PAD * 2} ${H + PAD * 2}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="b-ink" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="var(--ink1)"/><stop offset="0.42" stop-color="var(--ink2)"/><stop offset="1" stop-color="var(--ink3)"/>
    </linearGradient>
    <linearGradient id="b-cover" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="var(--c1)"/><stop offset="0.52" stop-color="var(--c2)"/><stop offset="1" stop-color="var(--c3)"/>
    </linearGradient>
    <radialGradient id="b-gloss" cx="0.3" cy="0.22" r="0.8">
      <stop offset="0" stop-color="#FFF" stop-opacity="0.38"/><stop offset="0.55" stop-color="#FFF" stop-opacity="0.03"/><stop offset="1" stop-color="#FFF" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="b-halo" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="var(--acc)" stop-opacity="0.18"/><stop offset="1" stop-color="var(--acc)" stop-opacity="0"/>
    </radialGradient>
    <filter id="b-sh" x="-15%" y="-15%" width="140%" height="140%"><feGaussianBlur stdDeviation="6"/></filter>
    <filter id="b-gl" x="-25%" y="-25%" width="150%" height="150%">
      <feGaussianBlur stdDeviation="1.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="b-soft" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="6"/></filter>
    <filter id="b-rim" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="9"/></filter>
    <clipPath id="b-clip"><path d="${g.TRI}"/></clipPath>
    <!-- 强调色渐变：沿斜边从 A 到 B；常规配色两个 stop 同色（等效纯色），霓虹配色才分色 -->
    <linearGradient id="b-acc" gradientUnits="userSpaceOnUse"
                    x1="${g.A[0]}" y1="${g.A[1]}" x2="${g.B[0]}" y2="${g.B[1]}">
      <stop class="b-gs-a" offset="0"/>
      <stop class="b-gs-b" offset="1"/>
    </linearGradient>
    <clipPath id="b-cc"><circle cx="${cx}" cy="${cy}" r="${R}"/></clipPath>
    <!-- 文字盒子：**只在要滚动时才套 mask**（见下面的 t1Scroll / t2Scroll）。
         不滚动时一点淡出都不能有，否则首尾字母会被啃掉（用户报过：没溢出却"被隐藏"）。
         左端淡出区放在文字起点之前（XOFF-14 → XOFF-2），所以静止时第一个字仍是实心的。
         高度往基线上下各留够，别把 g/p/y 的下沉部分切掉。 -->
    ${textMask(1, g.textY.t1, XOFF, g.t1Max, -24, 34)}
    ${textMask(2, g.textY.t2, XOFF + 1, g.t2Max, -16, 26)}
  </defs>

  <path d="${g.TRI}" fill="#000" opacity="0.55" filter="url(#b-sh)" transform="translate(4,5)"/>
  <path d="${g.TRI}" fill="url(#b-ink)" opacity="0.97"/>

  <g clip-path="url(#b-clip)">
    <path d="M${g.A[0]},${g.A[1]} L${g.B[0]},${g.B[1]}" stroke="var(--txt)" stroke-width="52" opacity="0.07" filter="url(#b-rim)"/>
    <path d="M${g.A[0]},${g.A[1]} L${g.B[0]},${g.B[1]}" stroke="var(--rim)" stroke-width="18" opacity="0.055" filter="url(#b-rim)"/>

    <circle cx="${cx}" cy="${cy}" r="${HALO}" fill="url(#b-halo)"/>
    <circle cx="${cx}" cy="${cy}" r="${R}" fill="url(#b-cover)"/>
    ${cover}
    <circle cx="${cx}" cy="${cy}" r="${R}" fill="url(#b-gloss)"/>
    <circle cx="${cx}" cy="${cy}" r="${R_OUT}" fill="none" stroke="var(--acc)" stroke-width="1" opacity="0.45"/>
    <circle cx="${cx}" cy="${cy}" r="${R}" fill="none" stroke="var(--txt)" stroke-width="1.4" opacity="0.4"/>
    <text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="central"
          font-family="Segoe UI Symbol, Segoe UI, sans-serif" font-size="${NOTE}" fill="#FFF" opacity="0.4">&#9834;</text>

    <g transform="translate(${tx},${ty}) rotate(${g.ANG.toFixed(2)})">
      <g${t1Scroll ? ' mask="url(#b-tm1)"' : ''}>
        <g id="b-t1-move" data-w="${titleW.toFixed(1)}" data-max="${g.t1Max}">
          <text class="b-t1" x="${XOFF}" y="${g.textY.t1}">${esc(title)}</text>
        </g>
      </g>
      <g${t2Scroll ? ' mask="url(#b-tm2)"' : ''}>
        <g id="b-t2-move" data-w="${artistW.toFixed(1)}" data-max="${g.t2Max}">
          <text class="b-t2" x="${XOFF + 1}" y="${g.textY.t2}">${esc(artist)}</text>
        </g>
      </g>
    </g>

    <path d="${g.LEGS}" fill="none" stroke="#FFFFFF" stroke-opacity="0.18" stroke-width="2.6"/>
    <path d="M${g.A[0]},${g.A[1]} L${g.B[0]},${g.B[1]}" stroke="url(#b-acc)" stroke-width="1.6" opacity="0.8" fill="none"/>
  </g>

  <g class="b-bars">
    <path id="b-bars-under" d="${barsPath(g, null)}" fill="none" stroke="#000000" stroke-opacity="0.5" stroke-width="3.8" stroke-linecap="round"/>
    <path id="b-bars-accent" d="${barsPath(g, null)}" fill="none" stroke="url(#b-acc)" stroke-opacity="0.82" stroke-width="2" stroke-linecap="round"/>
  </g>
  <g clip-path="url(#b-clip)">
    <line id="b-track" stroke="#FFFFFF" stroke-opacity="0.16" stroke-width="3" stroke-linecap="round"/>
    <line id="b-play" stroke="url(#b-acc)" stroke-width="3" stroke-linecap="round" opacity="0.95"/>
    <circle id="b-dot" r="7" fill="url(#b-acc)" opacity="0.18" filter="url(#b-soft)"/>
    <circle id="b-ball" r="5.5" fill="url(#b-acc)"/>
    <g transform="translate(${g.TL[0].toFixed(1)},${g.TL[1].toFixed(1)}) rotate(${g.ANG.toFixed(2)})">
      <text font-family="Segoe UI, Microsoft YaHei, sans-serif" font-size="8.4" font-weight="600" letter-spacing="0.6">
        <tspan id="b-el" fill="var(--acc)">--:--</tspan></text>
    </g>
    <g transform="translate(${g.TR[0].toFixed(1)},${g.TR[1].toFixed(1)}) rotate(${g.ANG.toFixed(2)})">
      <text text-anchor="end" font-family="Segoe UI, Microsoft YaHei, sans-serif" font-size="8.4" font-weight="600" letter-spacing="0.6">
        <tspan id="b-tot" fill="#FFFFFF" fill-opacity="0.42">--:--</tspan></text>
    </g>
  </g>
</svg>`;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /* 静态部分画一次，动态部分每帧只改属性 */
  function patch(svg, pos, dur, corner) {
    if (!svg) return;
    const g = GEOM[corner] || GEOM.bl;
    const inset = 7.5, d0 = 12, d1 = 480;
    const ax = g.A[0] + g.UX * d0 + g.NX * inset, ay = g.A[1] + g.UY * d0 + g.NY * inset;
    const bx = g.A[0] + g.UX * d1 + g.NX * inset, by = g.A[1] + g.UY * d1 + g.NY * inset;
    const span = d1 - d0;
    const known = isFinite(dur) && dur > 0;
    const p = known ? Math.max(0, Math.min(1, pos / dur)) : 0;
    const ex = ax + g.UX * span * p, ey = ay + g.UY * span * p;

    const track = svg.querySelector('#b-track');
    if (track) {
      track.setAttribute('x1', ax.toFixed(1)); track.setAttribute('y1', ay.toFixed(1));
      track.setAttribute('x2', bx.toFixed(1)); track.setAttribute('y2', by.toFixed(1));
    }
    const play = svg.querySelector('#b-play');
    if (play) {
      play.setAttribute('x1', ax.toFixed(1)); play.setAttribute('y1', ay.toFixed(1));
      play.setAttribute('x2', ex.toFixed(1)); play.setAttribute('y2', ey.toFixed(1));
      play.setAttribute('opacity', known ? '0.95' : '0.25');
    }
    ['#b-dot', '#b-ball'].forEach((sel) => {
      const c = svg.querySelector(sel);
      if (c) { c.setAttribute('cx', ex.toFixed(1)); c.setAttribute('cy', ey.toFixed(1)); }
    });

    const el = svg.querySelector('#b-el'), tot = svg.querySelector('#b-tot');
    if (el) el.textContent = fmt(pos);
    if (tot) tot.textContent = fmt(known ? dur : NaN);
  }

  /* ---------------- 文字滚动（超出盒子就来回滚，和卡片同一套手感） ----------------
     SVG 里没有 scrollWidth，靠 render() 预先量好的 data-w / data-max 判断。 */
  let textRecs = [];

  function holdEase(k) {
    const a = 0.12;
    if (k <= a) return 0;
    if (k >= 1 - a) return 1;
    const x = (k - a) / (1 - 2 * a);
    return x * x * (3 - 2 * x);
  }

  /* 每次重新 render 之后调一次：重新量、重新起跑 */
  function setupText(svg) {
    textRecs = [];
    if (!svg) return;
    ['b-t1-move', 'b-t2-move'].forEach((id) => {
      const el = svg.querySelector('#' + id);
      if (!el) return;
      const w = Number(el.getAttribute('data-w') || 0);
      const max = Number(el.getAttribute('data-max') || 0);
      if (!max || w <= max + 4) {          // 放得下（或只差几 px）：不滚，和 render 的判定保持一致
        el.removeAttribute('transform');
        return;
      }
      const dist = w - max;                // 滚到"最后一个字刚好贴在盒右边界"为止
      textRecs.push({ el, dist, dur: Math.max(4.5, dist / 32 + 1.2), start: performance.now() });
    });
  }

  /* 每帧推进（overlay.js 的绘制闸门里调，和卡片跑马灯同频） */
  function stepTextMarquees(now) {
    for (const r of textRecs) {
      const t = (now - r.start) / 1000;
      const p = (t / r.dur) % 2;
      const k = p < 1 ? p : 2 - p;
      r.el.setAttribute('transform', `translate(${(-r.dist * holdEase(k)).toFixed(1)},0)`);
    }
  }

  window.Badge = {
    render, patch, fmt, setBars, BAR_COUNT, GEOM, CORNERS, TL_COVER, TL_TEXT,
    setupText, stepTextMarquees, setRadius, metrics: fontMetrics,
  };
})();
