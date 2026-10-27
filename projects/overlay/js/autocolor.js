/* 封面取色 → 自动配色（第十六轮）
   思路：
     1. 把封面缩到 32×32 画到 canvas 上读像素（同源，没有跨域问题）
     2. 丢掉「太暗 / 太亮 / 不够彩」的像素，按 30° 分 12 个色相箱，用「饱和度 × 中间亮度权重」计票
     3. 票最多的色相 = 主色，隔开至少 2 个箱再取一个 = 副色（找不到就拿主色 +180°）
     4. 生成一套和手动配色同构的调色板（acc / rim / ink1-3 / txt / c1-3），
        最后做一次对比度体检：不合格返回 null，调用方退回「最近的手动配色」
   为什么不全按封面原色走：封面经常是灰的/黑白的/大面积人像，
   照搬会得到又灰又糊的配色；所以只借"色相"，饱和度和明度都按可读性重新给。 */
(function () {
  'use strict';

  const BINS = 12;                 // 每箱 30°
  const MIN_PIXELS = 40;           // 有效彩色像素太少就不硬算

  function rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    const l = (mx + mn) / 2;
    let h = 0, s = 0;
    if (mx !== mn) {
      const d = mx - mn;
      s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      if (mx === r) h = ((g - b) / d + (g < b ? 6 : 0));
      else if (mx === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
    }
    return [h, s, l];
  }

  function hslToRgb(h, s, l) {
    h = ((h % 360) + 360) % 360;
    const c = (1 - Math.abs(2 * l - 1)) * s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = l - c / 2;
    let r = 0, g = 0, b = 0;
    if (h < 60) { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else { r = c; b = x; }
    return [
      Math.round((r + m) * 255),
      Math.round((g + m) * 255),
      Math.round((b + m) * 255),
    ];
  }

  const hex2 = (n) => n.toString(16).padStart(2, '0');
  const toHex = (rgb) => '#' + rgb.map(hex2).join('').toUpperCase();
  const toRgbStr = (rgb) => rgb.join(',');

  /* WCAG 相对亮度 + 对比度 */
  function lum(rgb) {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
  }
  function contrast(a, b) {
    const la = lum(a), lb = lum(b);
    const hi = Math.max(la, lb), lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
  }

  /* 从像素里挑主色 / 副色（返回色相，没结果返回 null） */
  function dominantHues(data) {
    const bins = new Array(BINS).fill(0);
    let n = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] < 128) continue;
      const [h, s, l] = rgbToHsl(data[i], data[i + 1], data[i + 2]);
      if (l < 0.08 || l > 0.92) continue;          // 近乎纯黑 / 纯白
      if (s < 0.12) continue;                      // 灰的没色相意义
      const w = s * (1 - Math.abs(l - 0.55));      // 中间亮度、鲜艳的票更重
      bins[Math.min(BINS - 1, Math.floor(h / (360 / BINS)))] += w;
      n++;
    }
    if (n < MIN_PIXELS) return null;

    const order = bins.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]);
    if (order[0][0] <= 0) return null;
    const b1 = order[0][1];
    const h1 = b1 * (360 / BINS) + 360 / BINS / 2;

    // 副色：和主色隔开 ≥ 2 个箱，且至少拿到主色 25% 的票
    let b2 = -1;
    for (const [v, i] of order.slice(1)) {
      const dist = Math.min(Math.abs(i - b1), BINS - Math.abs(i - b1));
      if (dist >= 2 && v >= order[0][0] * 0.25) { b2 = i; break; }
    }
    const h2 = b2 >= 0 ? b2 * (360 / BINS) + 360 / BINS / 2 : (h1 + 180) % 360;
    return { h1, h2 };
  }

  /* 由两个色相生成调色板；对比度不合格返回 null */
  function buildPalette(h1, h2) {
    const acc = hslToRgb(h1, 0.95, 0.60);
    const rim = hslToRgb(h2, 0.85, 0.76);
    const w1 = hslToRgb(h1, 0.30, 0.11);
    const w2 = hslToRgb(h1, 0.34, 0.06);
    const w3 = hslToRgb(h1, 0.42, 0.03);
    const txt = hslToRgb(h1, 0.12, 0.97);
    const c3 = hslToRgb(h1, 0.55, 0.10);

    // 体检：正文得看得清，强调色也得压得住墨底
    if (contrast(txt, w2) < 8) return null;
    if (contrast(acc, w2) < 3.2) return null;

    return {
      label: '自动',
      acc: toHex(acc), accRgb: toRgbStr(acc),
      w1: toHex(w1), w2: toHex(w2), w3: toHex(w3),
      txt: toHex(txt), rim: toHex(rim),
      c1: toHex(acc), c2: toHex(rim), c3: toHex(c3),
    };
  }

  /* 生成失败时：按主色色相挑一套最接近的手动配色 */
  function nearestPreset(h) {
    const all = window.PALETTES || {};
    let best = null, bestD = 1e9;
    Object.keys(all).forEach((k) => {
      const [ph] = rgbToHsl.apply(null, hexToRgb(all[k].acc));
      const d = Math.min(Math.abs(ph - h), 360 - Math.abs(ph - h));
      if (d < bestD) { bestD = d; best = k; }
    });
    return best;
  }

  function hexToRgb(hex) {
    const s = String(hex).replace('#', '');
    return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
  }

  /* 主入口：拿封面 URL → { kind, pal, hex, key } | null */
  async function fromCover(url, size) {
    if (!url) return null;
    const img = new Image();
    img.decoding = 'async';
    const ok = await new Promise((res) => {
      img.onload = () => res(true);
      img.onerror = () => res(false);
      img.src = url;
    });
    if (!ok) return null;

    const S = size || 32;
    const cv = document.createElement('canvas');
    cv.width = S; cv.height = S;
    const ctx = cv.getContext('2d', { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, S, S);
    let px;
    try {
      px = ctx.getImageData(0, 0, S, S).data;
    } catch (e) {
      return null;                                  // 理论上同源不会走到这
    }

    const hues = dominantHues(px);
    if (!hues) return null;

    const pal = buildPalette(hues.h1, hues.h2);
    if (pal) {
      return { kind: 'auto', pal, hex: pal.acc, key: url };
    }
    // 体检没过：退回最近的手动配色（稳定、绝不会难看）
    const key = nearestPreset(hues.h1);
    if (key && window.PALETTES[key]) {
      return { kind: 'preset', pal: window.PALETTES[key], hex: window.PALETTES[key].acc, key };
    }
    return null;
  }

  window.AutoColor = { fromCover, buildPalette, dominantHues, nearestPreset, contrast, hexToRgb };
})();
