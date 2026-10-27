/* 配色表 —— 与 projects/corner-badge/build_badge.py 的 PALETTES 保持一致
   叠加层、控制面板、角标共用这一份，改这里就全改。
   字段：acc 主色 / accRgb 主色 RGB（用于 rgba()）/ w1..w3 墨底渐变 / txt 正文 / rim 环境光 / c1..c3 封面渐变 */
(function () {
  window.PALETTES = {
    magenta: {
      label: '品红', acc: '#FF2D95', accRgb: '255,45,149',
      w1: '#2A0F28', w2: '#150817', w3: '#06030A', txt: '#FFF0F7', rim: '#FFA8D2',
      c1: '#FF2D95', c2: '#8A2BE2', c3: '#26063F'
    },
    cinnabar: {
      label: '朱砂', acc: '#E8503F', accRgb: '232,80,63',
      w1: '#241828', w2: '#100C16', w3: '#06060B', txt: '#F7F3EC', rim: '#FFB3A0',
      c1: '#E8503F', c2: '#9B2E8F', c3: '#241558'
    },
    cyan: {
      label: '青', acc: '#22E1FF', accRgb: '34,225,255',
      w1: '#0E2833', w2: '#08161F', w3: '#03070C', txt: '#EAF9FF', rim: '#A7F0FF',
      c1: '#22E1FF', c2: '#1A6FD4', c3: '#061A3A'
    },
    acid: {
      label: '绿', acc: '#7CFF5A', accRgb: '124,255,90',
      w1: '#0F2A1C', w2: '#081710', w3: '#030806', txt: '#F2FFF0', rim: '#C8FFB8',
      c1: '#7CFF5A', c2: '#17A87A', c3: '#04241C'
    },
    /* ---- 第十六轮：四套霓虹撞色（两色相撞，主色 / 环境光刻意不同色） ----
       grad: true 表示「强调色走渐变」（c1 → c2），进度条 / 频谱条 / 角标斜线都是渐变； 
       前四套常规配色不加这个标记，保持纯色。 */
    neon: {
      label: '霓虹', grad: true, acc: '#FF2FD0', accRgb: '255,47,208',
      w1: '#2A0A2E', w2: '#150618', w3: '#06030A', txt: '#FFF0FB', rim: '#7BF7FF',
      c1: '#FF2FD0', c2: '#00E5FF', c3: '#1B0033'
    },
    electric: {
      label: '电紫', grad: true, acc: '#B04BFF', accRgb: '176,75,255',
      w1: '#20103A', w2: '#120A22', w3: '#05030D', txt: '#F6F0FF', rim: '#FFE94D',
      c1: '#B04BFF', c2: '#FFD84D', c3: '#170033'
    },
    toxic: {
      label: '毒绿', grad: true, acc: '#B4FF1A', accRgb: '180,255,26',
      w1: '#122A12', w2: '#091A0C', w3: '#030806', txt: '#F6FFEF', rim: '#FF5AD1',
      c1: '#B4FF1A', c2: '#FF2D95', c3: '#05200E'
    },
    sunset: {
      label: '落日', grad: true, acc: '#FF7A2F', accRgb: '255,122,47',
      w1: '#2B1520', w2: '#180B12', w3: '#070307', txt: '#FFF3E8', rim: '#46C7FF',
      c1: '#FF7A2F', c2: '#2D6BFF', c3: '#2A0A18'
    }
  };
  window.PALETTE_ORDER = [
    'magenta', 'cinnabar', 'cyan', 'acid',
    'neon', 'electric', 'toxic', 'sunset'
  ];

  /* 把配色写成 CSS 变量（挂到给定元素，默认 :root）
     第十六轮：也接受直接传一个配色对象（「跟随封面」自动生成的那套） */
  window.applyPalette = function (name, el) {
    const p = (name && typeof name === 'object')
      ? name
      : (window.PALETTES[name] || window.PALETTES.magenta);
    const s = (el || document.documentElement).style;
    s.setProperty('--acc', p.acc);
    s.setProperty('--acc-rgb', p.accRgb);
    s.setProperty('--ink1', p.w1);
    s.setProperty('--ink2', p.w2);
    s.setProperty('--ink3', p.w3);
    s.setProperty('--txt', p.txt);
    s.setProperty('--rim', p.rim);
    s.setProperty('--c1', p.c1);
    s.setProperty('--c2', p.c2);
    s.setProperty('--c3', p.c3);
    // 强调色：标记了 grad 的走两色渐变，否则就是纯色（同一个变量，CSS 不用分支）
    s.setProperty('--acc-grad',
      p.grad ? `linear-gradient(90deg, ${p.c1}, ${p.c2})` : p.acc);
    return p;
  };
})();
