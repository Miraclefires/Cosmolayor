/* 控制面板逻辑：读 /api/snapshot 拿状态，改完 POST /api/state，叠加层 500ms 内跟上 */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);

  const COMPONENTS = [
    ['badge',   '◤', '角标',     '三角徽章：封面 / 歌名 / 进度'],
    ['banner',  '▬', '条幅',     '横条：封面 / 歌名 / 歌手 / 进度 / 时长'],
    ['card',    '▢', '播放卡片', '卡片：封面 / 歌名 / 歌手 / 进度'],
    ['spectrum','▥', '频谱',     '64 段实时频谱，可拖可缩放']
  ];

  /* 程序名给人看：桥上报的是 exe，这里翻成中文（认不出来的原样显示） */
  const SRC_NAME = {
    'cloudmusic.exe': '网易云音乐',
    'spotify.exe': 'Spotify',
    'qqmusic.exe': 'QQ 音乐',
    'foobar2000.exe': 'foobar2000',
    'musicbee.exe': 'MusicBee',
    'aimp.exe': 'AIMP',
    'potplayer.exe': 'PotPlayer',
    'potplayermini64.exe': 'PotPlayer',
    'zune.exe': 'Groove 音乐'
  };
  const srcName = (s) => SRC_NAME[String(s || '').toLowerCase()] || s || '播放器';

  const SEG_BADGE_POS = { key: 'badgePos', label: '角标位置', opts: [['bl', '左下'], ['tl', '左上']] };
  const SEG_BANNER_POS = { key: 'bannerPos', label: '条幅位置', opts: [['top', '顶部'], ['bottom', '底部']] };

  /* 全局的只剩封面形状了：位置/透明度/大小都收进各自组件的 ▾ 里（第十四轮） */
  const SEGMENTS = [
    { key: 'coverShape', label: '封面形状', opts: [['circle', '圆形'], ['square', '圆角方']] }
  ];

  /* 频谱自己的设置放「频谱」卡片，不混在布局里 */
  const SPEC_SEGMENTS = [
    { key: 'spectrumMode', label: '形态', opts: [['full', '通栏'], ['narrow', '跟着条幅']] },
    { key: 'spectrumFps',  label: '帧率', opts: [['30', '30 fps'], ['60', '60 fps']] },
    // 第十四轮：进程回环——只录一个程序的声音（默认网易云），空串 = 整机混音
    { key: 'audioLock',    label: '音频来源', opts: [['cloudmusic.exe', '只抓网易云'], ['', '全部声音']] }
  ];

  function segHtml(list) {
    return list.map((s) => `
      <div class="seg-group">
        <div class="k">${s.label}</div>
        <div class="seg" data-seg="${s.key}">
          ${s.opts.map(([v, t]) => `<button data-v="${v}" class="${String(state[s.key]) === String(v) ? 'on' : ''}">${t}</button>`).join('')}
        </div>
      </div>`).join('');
  }

  /* 第十三轮：每个组件一行「透明度 + 大小 + 位置」。
     缩放上限 = 当前默认大小（1.0×，只能缩）；卡片保留原来的 0.75~1.5。
     频谱没有透明度（需求只要三个组件的），它的大小/位置在「频谱」卡里。 */
  const LOOKS = [
    { key: 'badge',  name: '角标',     opacity: 'badgeOpacity',
      scale: 'badgeScale',  x: 'badgeX',  y: 'badgeY',  sMin: 0.4,  sMax: 1 },
    { key: 'banner', name: '条幅',     opacity: 'bannerOpacity',
      scale: 'bannerScale', x: 'bannerX', y: 'bannerY', sMin: 0.4,  sMax: 1 },
    { key: 'card',   name: '播放卡片', opacity: 'cardOpacity',
      scale: 'cardScale',   x: 'cardX',   y: 'cardY',   sMin: 0.75, sMax: 1.5 }
  ];

  const pct = (v) => Math.round((v == null ? 1 : Number(v)) * 100);
  const paletteLabel = (k) => ((window.PALETTES || {})[k] || {}).label || k || '手动';

  /* 各组件的默认位置（1920×1080，y = 距底边）。
     ⚠️ 和 overlay.js 的 defaultRect() 是同一张表，改一处要同步改另一处。 */
  function defaultRect(kind) {
    const badgeOn = state.visible.badge;
    const badgeBL = badgeOn && state.badgePos === 'bl';
    const badgeTL = badgeOn && state.badgePos === 'tl';
    if (kind === 'banner') {
      const atBottom = state.bannerPos === 'bottom';
      const clash = atBottom ? badgeBL : badgeTL;
      const top = atBottom ? 1080 - 88 - 36 : 60;
      return { x: clash ? 600 : 60, y: 1080 - top - 88 };
    }
    if (kind === 'card') return { x: 60, y: 1080 - (badgeBL ? 632 : 850) - 208 };
    if (kind === 'badge') return { x: 0, y: state.badgePos === 'tl' ? 1080 - 254 : 0 };
    return { x: 0, y: 0 };
  }

  function lookBlock(L) {
    const op = pct(state[L.opacity]);
    const sc = Number(state[L.scale]) || 1;
    const px = Math.round(Number(state[L.x]) || 0);
    const py = Math.round(Number(state[L.y]) || 0);
    return `
      <div class="seg-group">
        <div class="k" title="x = 左边距，y = 距底边（px）；也可以在右边预览里直接拖">
          位置（左, 底） <b class="cs-val" id="pos-${L.key}">${px}, ${py}</b>
        </div>
        <div class="size-row">
          <span class="sl-k">透明度</span>
          <input type="range" id="op-${L.key}" min="0" max="1" step="0.01" value="${op / 100}">
          <b class="cs-val" id="opv-${L.key}">${op}%</b>
        </div>
        <div class="size-row">
          <span class="sl-k">大小</span>
          <input type="range" id="sc-${L.key}" min="${L.sMin}" max="${L.sMax}" step="0.01" value="${sc}">
          <b class="cs-val" id="scv-${L.key}">${sc.toFixed(2)}×</b>
          <button data-reset="${L.key}" title="位置 / 大小 / 透明度 全部回默认">重置</button>
        </div>
      </div>`;
  }

  const LOOK_BY_KEY = {};
  LOOKS.forEach((L) => { LOOK_BY_KEY[L.key] = L; });

  /* 每个组件的 ▾ 次级菜单：开关 + 位置预设 + 透明度/大小/位置 */
  function subHtml(key) {
    let html = (SUBMENUS[key] || []).map((s) => `
        <div class="row sub">
          <div class="ico">▥</div>
          <div class="lbl"><b>${s.name}</b>${s.desc ? `<small>${s.desc}</small>` : ''}</div>
          <label class="sw"><input type="checkbox" id="${s.id}" ${state[s.key] !== false ? 'checked' : ''}><span></span></label>
        </div>`).join('');
    if (key === 'badge') html += segHtml([SEG_BADGE_POS]);
    if (key === 'banner') html += segHtml([SEG_BANNER_POS]);
    if (LOOK_BY_KEY[key]) html += lookBlock(LOOK_BY_KEY[key]);
    return html;
  }

  /* 第十六轮：全局外观 —— 封面旋转 + 整体圆角（墨块 / 角标三角 / 圆角方封面都跟着走）
     圆角矩形（coverShape=square）**不支持封面旋转**：转起来像个转动的窗口。
     这时把开关藏掉，原地换成一句说明（hover 也有 title 提示）。 */
  function globalHtml() {
    const raw = Number(state.radius);
    const rad = isFinite(raw) ? Math.round(raw) : 30;
    const square = state.coverShape === 'square';
    const spinRow = square
      ? `<div class="row" style="border:0;padding:8px 0 4px" title="圆角矩形不支持封面旋转">
          <div class="ico">↻</div>
          <div class="lbl"><b>封面旋转</b><small class="tip">圆角矩形不支持封面旋转</small></div>
        </div>`
      : `<div class="row" style="border:0;padding:8px 0 4px">
          <div class="ico">↻</div>
          <div class="lbl"><b>封面旋转</b><small>播放时转，暂停时停在当前角度</small></div>
          <label class="sw"><input type="checkbox" id="swSpin" ${state.coverSpin ? 'checked' : ''}><span></span></label>
        </div>`;
    return spinRow + `
      <div class="seg-group">
        <div class="k">整体圆角 <b class="cs-val" id="radVal">${rad}</b></div>
        <div class="size-row">
          <input id="radRange" type="range" min="0" max="44" step="1" value="${rad}">
          <button id="radReset">重置</button>
        </div>
        <div class="size-hint">组件底板 / 角标三角 / 圆角方封面一起变（0 = 直角）</div>
      </div>`;
  }

  function staleHint() {
    const stale = typeof state.badgeScale !== 'number';
    return stale
      ? '<div class="size-hint warn">服务端是旧版本：位置 / 透明度 / 大小存不进去 —— 关掉 start.cmd 窗口再开一次</div>'
      : '';
  }

  /* 组件的次级菜单：每项一个开关，收在组件行右侧的 ▾ 里 */
  /* 组件的次级菜单：每项一个开关，收在组件行右侧的 ▾ 里。
     （2026-10-27：角标频谱 / 卡片频谱 的说明文字按用户要求删掉，只留开关） */
  const SUBMENUS = {
    badge: [{ id: 'swBadgeSpec', key: 'badgeSpectrum', name: '角标频谱' }],
    card: [{ id: 'swCardSpec', key: 'cardSpectrum', name: '卡片频谱' }]
  };

  let state = null;
  let saveTimer = 0;
  let anim = { pos: 0, at: 0, dur: 0, playing: false };
  const subOpen = { badge: true, banner: false, card: false };   // 次级菜单展开状态（只存内存）

  /* ---------------- 渲染控件 ---------------- */
  function renderControls() {
    // 组件开关：每个组件的 ▾ 里放它自己的开关 + 位置 / 透明度 / 大小（第十四轮）
    $('compRows').innerHTML = COMPONENTS.map(([key, ico, name, desc]) => {
      const hasSub = !!LOOK_BY_KEY[key] || !!SUBMENUS[key];
      const open = !!subOpen[key];
      return `
      <div class="row">
        <div class="ico">${ico}</div>
        <div class="lbl"><b>${name}</b><small>${desc}</small></div>
        ${hasSub ? `<button class="sub-btn${open ? ' open' : ''}" data-sub="${key}"
          title="${name}的开关 / 位置 / 大小" aria-label="${name}设置" aria-expanded="${open ? 'true' : 'false'}">▾</button>` : ''}
        <label class="sw"><input type="checkbox" data-vis="${key}" ${state.visible[key] ? 'checked' : ''}><span></span></label>
      </div>` + (hasSub ? `
      <div class="sub-wrap${open ? ' open' : ''}" id="sub-${key}">${subHtml(key)}</div>` : '');
    }).join('');

    // 布局 / 频谱
    $('segRows').innerHTML = segHtml(SEGMENTS) + globalHtml() + staleHint();
    $('specRows').innerHTML = segHtml(SPEC_SEGMENTS) +
      '<div class="size-hint" style="margin:-2px 0 12px">帧率 30 够用、60 更顺（CPU 约翻倍）；'
      + '「全部声音」= 整机混音，浏览器 / 通知也会进频谱</div>' + specHtml();

    // 配色（第十六轮：多一个「跟随封面」开关，开着时色板置灰不可点；开了自动再给出「配色用渐变」）
    $('palRows').innerHTML = `
      <div class="row" style="border:0;padding:0 0 10px">
        <div class="ico">◐</div>
        <div class="lbl"><b>跟随封面</b><small>从封面抓取主要颜色</small></div>
        <label class="sw"><input type="checkbox" id="swAutoPal" ${state.autoPalette ? 'checked' : ''}><span></span></label>
      </div>` + (state.autoPalette ? `
      <div class="row" style="border:0;padding:0 0 6px">
        <div class="ico">▤</div>
        <div class="lbl"><b>跟随封面-渐变</b><small>主色 → 副色，和霓虹配色同一套</small></div>
        <label class="sw"><input type="checkbox" id="swAutoGrad" ${state.autoGrad ? 'checked' : ''}><span></span></label>
      </div>
      <div class="size-hint" style="margin:0 0 10px">逐条插值画频谱，开销和霓虹配色一样（可忽略）</div>` : '');
    $('swatches').classList.toggle('off', !!state.autoPalette);
    $('swatches').innerHTML = window.PALETTE_ORDER.map((k) => {
      const p = window.PALETTES[k];
      return `<button class="sw-item ${state.palette === k ? 'on' : ''}" data-pal="${k}">
        <i class="c" style="--c:linear-gradient(135deg,${p.acc},${p.c2})"></i>${p.label}</button>`;
    }).join('');
    const ph = $('palHint');
    if (ph) {
      ph.textContent = state.autoPalette
        ? '「跟随封面」开着时，色板灰掉是正常的 —— 取不到颜色才会退回手动这套（' + paletteLabel(state.palette) + '）'
        : '';
    }

    $('swDemo').checked = state.demo === 'on';
    window.applyPalette(state.palette);   // 面板自己的主色跟着走
  }

  /* 大频谱的位置 / 缩放（在预览里拖出来，这里显示 + 滑杆 + 重置） */
  function specHtml() {
    const x = Math.round(Number(state.specX) || 0);
    const y = Math.round(Number(state.specY) || 0);
    const s = Number(state.specScale) || 1;
    // 旧进程没有 audioLock：切了也不会生效，直接说明白（和卡片缩放那条一个套路）
    const stale = typeof state.audioLock !== 'string';
    return `
      <div class="seg-group">
        <div class="k">位置 / 缩放 <b class="cs-val" id="ssVal">${x}, ${y} · ${s.toFixed(2)}×</b></div>
        <div class="size-row">
          <span class="sl-k">大小</span>
          <input id="ssRange" type="range" min="0.4" max="1" step="0.01" value="${s}">
          <button id="ssReset" title="位置回通栏底部、大小回 1.00×">重置</button>
        </div>
        <div class="size-hint">在右边预览里拖频谱本体移动，拖右下角圆点缩放</div>
      </div>` + (stale ? `
      <div class="size-hint warn">服务端是旧版本：切「音频来源」不生效 —— 关掉 start.cmd 窗口再开一次</div>` : '');
  }

  let lastScaleEdit = 0;
  function bind() {
    document.addEventListener('change', (e) => {
      const t = e.target;
      if (t.dataset && t.dataset.vis) {
        const key = t.dataset.vis;
        // 角标开关也会改变"条幅该不该让位"：先记下再切
        const bBefore = defaultRect('banner');
        const bannerUntouched = Number(state.bannerX) === bBefore.x && Number(state.bannerY) === bBefore.y;
        state.visible[key] = t.checked;
        if (key === 'badge' && bannerUntouched) {
          const db = defaultRect('banner');
          state.bannerX = db.x; state.bannerY = db.y;
          syncLooksUi();
        }
        save();
      }
      if (t.id === 'swDemo') {
        state.demo = t.checked ? 'on' : 'auto';
        save();
      }
      if (t.id === 'swBadgeSpec') {
        state.badgeSpectrum = t.checked;
        save();
      }
      if (t.id === 'swCardSpec') {
        state.cardSpectrum = t.checked;
        save();
      }
      if (t.id === 'swSpin') {
        state.coverSpin = t.checked;
        save();
      }
      if (t.id === 'swAutoPal') {
        state.autoPalette = t.checked;
        renderControls();       // 色板要跟着置灰/恢复
        save();
      }
      if (t.id === 'swAutoGrad') {
        state.autoGrad = t.checked;
        save();
      }
    });

    // 滑杆：透明度 / 大小 / 频谱大小（边走边存）
    document.addEventListener('input', (e) => {
      const id = e.target.id || '';
      if (id.indexOf('op-') === 0) {
        const L = LOOKS.find((x) => 'op-' + x.key === id);
        if (!L) return;
        state[L.opacity] = Math.round(Number(e.target.value) * 100) / 100;
        const v = $('opv-' + L.key);
        if (v) v.textContent = pct(state[L.opacity]) + '%';
        save();
      } else if (id.indexOf('sc-') === 0) {
        const L = LOOKS.find((x) => 'sc-' + x.key === id);
        if (!L) return;
        state[L.scale] = Math.round(Number(e.target.value) * 100) / 100;
        const v = $('scv-' + L.key);
        if (v) v.textContent = (Number(state[L.scale]) || 1).toFixed(2) + '×';
        lastScaleEdit = Date.now();
        save();
      } else if (id === 'ssRange') {
        state.specScale = Math.round(Number(e.target.value) * 100) / 100;
        lastScaleEdit = Date.now();
        save();
      } else if (id === 'radRange') {
        state.radius = Math.round(Number(e.target.value));
        const v = $('radVal');
        if (v) v.textContent = String(state.radius);
        lastScaleEdit = Date.now();
        save();
      }
    });

    // 预览页里拖完之后 postMessage 过来：读数立刻跟手（落盘由预览页自己 POST）
    addEventListener('message', (e) => {
      if (e.origin !== location.origin) return;
      const d = e.data || {};
      if (d.type !== 'edit' || !d.patch) return;
      Object.assign(state, d.patch);
      syncLooksUi();
    });

    document.addEventListener('click', (e) => {
      const resetKey = e.target.dataset && e.target.dataset.reset;
      if (resetKey) {
        const L = LOOKS.find((x) => x.key === resetKey);
        if (L) {
          const d = defaultRect(resetKey);
          state[L.x] = d.x;
          state[L.y] = d.y;
          state[L.scale] = 1;
          state[L.opacity] = 1;
          lastScaleEdit = Date.now();
          renderControls();
          save();
        }
        return;
      }
      if (e.target.id === 'radReset') {
        state.radius = 30;
        lastScaleEdit = Date.now();
        renderControls();
        save();
        return;
      }
      if (e.target.id === 'ssReset') {
        state.specX = 0; state.specY = 0; state.specScale = 1;
        lastScaleEdit = Date.now();
        renderControls();
        save();
        return;
      }
      const sb = e.target.closest('.sub-btn');
      if (sb) {
        subOpen[sb.dataset.sub] = !subOpen[sb.dataset.sub];
        renderControls();
        return;
      }
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.v !== undefined && b.parentElement.dataset.seg) {
        const k = b.parentElement.dataset.seg;
        // 切之前先记下条幅的默认位：它没被手动拖过的话，下面要跟着重排
        const bBefore = defaultRect('banner');
        const bannerUntouched = Number(state.bannerX) === bBefore.x && Number(state.bannerY) === bBefore.y;
        state[k] = b.dataset.v;
        if (k === 'badgePos') {
          // 角标换角落 = 换镜像几何 + 换位置
          const d = defaultRect('badge');
          state.badgeX = d.x; state.badgeY = d.y;
          // 换了边，"条幅该不该让位"也变了 —— 条幅没被拖过就重新排
          if (bannerUntouched) {
            const db = defaultRect('banner');
            state.bannerX = db.x; state.bannerY = db.y;
          }
        } else if (k === 'bannerPos') {
          const d = defaultRect('banner');
          state.bannerX = d.x; state.bannerY = d.y;
        }
        renderControls();
        save();
      }
      if (b.dataset.pal) {
        state.palette = b.dataset.pal;
        renderControls();
        save();
      }
    });
  }

  /* ---------------- 写回服务 ---------------- */
  /* 只更新读数、不重排 DOM：拖滑杆或拖预览时不能把焦点/拖拽状态弄丢 */
  function syncLooksUi() {
    LOOKS.forEach((L) => {
      const p = $('pos-' + L.key);
      if (p) p.textContent = `${Math.round(state[L.x])}, ${Math.round(state[L.y])}`;
      const sc = Number(state[L.scale]) || 1;
      const scv = $('scv-' + L.key);
      if (scv) scv.textContent = sc.toFixed(2) + '×';
      const scr = $('sc-' + L.key);
      if (scr && document.activeElement !== scr) scr.value = sc;
      const op = pct(state[L.opacity]);
      const opv = $('opv-' + L.key);
      if (opv) opv.textContent = op + '%';
      const opr = $('op-' + L.key);
      if (opr && document.activeElement !== opr) opr.value = op / 100;
    });
    const sv = $('ssVal');
    if (sv) {
      sv.textContent = `${Math.round(state.specX)}, ${Math.round(state.specY)} · ` +
        `${(Number(state.specScale) || 1).toFixed(2)}×`;
    }
    const sr = $('ssRange');
    if (sr && document.activeElement !== sr) sr.value = Number(state.specScale) || 1;
    // 整体圆角
    const rv = $('radVal');
    if (rv) rv.textContent = String(Math.round(Number(state.radius) || 0));
    const rr = $('radRange');
    if (rr && document.activeElement !== rr) rr.value = Math.round(Number(state.radius) || 0);
  }

  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        await fetch('/api/state', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(state)
        });
        flashFoot('已保存');
      } catch (e) {
        flashFoot('保存失败：' + e.message);
      }
    }, 90);
  }

  function flashFoot(msg) {
    $('footState').textContent = msg;
  }

  /* ---------------- 现在播放 ---------------- */
  function fmt(sec) {
    if (sec == null || !isFinite(sec) || sec < 0) return '--:--';
    sec = Math.floor(sec);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const p = (n) => String(n).padStart(2, '0');
    return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
  }

  function renderNow(n, bridge) {
    if (n) {
      $('nowTitle').textContent = n.idle ? '未在播放' : (n.title || '—');
      $('nowArtist').textContent = n.idle ? '' : ([n.artist, n.album].filter(Boolean).join(' · ') || '—');
      anim = { pos: n.position || 0, at: Date.now(), dur: n.duration || 0, playing: n.playing !== false };

      const cov = $('nowCover');
      const img = cov.querySelector('img');
      if (n.cover) {
        if (img.getAttribute('src') !== n.cover) { img.src = n.cover; img.onload = () => cov.classList.add('has-img'); }
      } else {
        cov.classList.remove('has-img');
      }

      const b = bridge || {};
      const st = $('stDot'), txt = $('stText');
      if (n.demo) {
        st.className = 'dot warn';
        txt.textContent = '演示模式 · 假数据（OBS 里同样显示）';
      } else if (n.idle) {
        st.className = 'dot warn';
        txt.textContent = b.ok ? '未在播放 · 等播放器出声' : '数据桥没在跑，可在下面「诊断」里重启';
      } else if (b.ok) {
        st.className = 'dot';
        txt.textContent = `数据桥正常 · ${srcName(b.source)} · ${n.playing === false ? '暂停' : '播放中'}`;
      } else {
        st.className = 'dot bad';
        txt.textContent = '数据桥异常：拿不到播放信息，试试「重启数据桥」';
      }
    }
    tick();
  }

  function tick() {
    const pos = anim.pos + (anim.playing ? (Date.now() - anim.at) / 1000 : 0);
    const p = anim.dur > 0 ? Math.min(100, (pos / anim.dur) * 100) : 0;
    $('nowFill').style.width = p + '%';
    $('nowEl').textContent = fmt(pos);
    $('nowTot').textContent = fmt(anim.dur);
  }

  /* ---------------- 诊断 ---------------- */
  const kb = (n) => (n ? Math.round(n / 1024) + ' KB' : '无');

  async function pollDiag() {
    try {
      const d = await fetch('/api/diag?_=' + Date.now(), { cache: 'no-store' }).then((r) => r.json());
      const g = d.diag || {};
      const raw = g.raw || {};

      const ps = $('dPs');
      if (g.note) {
        ps.textContent = g.note; ps.className = '';
      } else if (g.psAlive) {
        ps.textContent = g.restarts ? `运行中 · 重开 ${g.restarts} 次` : '运行中';
        ps.className = 'good';
      } else {
        ps.textContent = '子进程已退出'; ps.className = 'warn';
      }

      $('dIv').textContent = g.intervalMs ? g.intervalMs + ' ms' : '—';

      const dr = $('dRaw');
      if (!g.raw || raw.none) {
        dr.textContent = '无媒体会话'; dr.className = 'warn';
      } else {
        const src = String(raw.source || '').split('!').pop().replace(/^.*[\\/]/, '');
        // SMTC 给的是 Playing / Paused / Stopped，翻成中文；不认识的照原样显示
        const stMap = { playing: '播放中', paused: '暂停', stopped: '已停止', closed: '已关闭' };
        const stTxt = stMap[String(raw.status || '').toLowerCase()]
          || raw.status || (raw.playing ? '播放中' : '?');
        dr.textContent = `${src || '?'} · ${stTxt}`;
        dr.className = raw.playing ? 'good' : '';
        dr.title = JSON.stringify(raw);
      }

      const srcMap = { elog: '真实 · 网易云日志', smtc: '真实 · SMTC', estimate: '估算（兜底）' };
      const dp = $('dPos');
      dp.textContent = srcMap[g.positionSource] || (g.realProgress ? '真实' : '—');
      dp.className = g.positionSource && g.positionSource !== 'estimate' ? 'good' : '';

      const au = d.audio || {};
      const dsp = $('dSpec');
      if (au.ok) {
        const dev = String(au.device || '').replace(/\s*\[Loopback\]\s*/i, '');
        // 进程回环时 device 是 "cloudmusic.exe (pid 1234)"，一眼能看出锁没锁上
        dsp.textContent = (au.mode === 'process' ? '锁定 · ' : '真实 · ') + dev;
        dsp.title = `${dev} · ${au.rate} Hz · ${au.fps || '?'} fps · peak ${au.peak}`;
        dsp.className = 'good';
      } else {
        dsp.textContent = '模拟' + (au.error ? ' · ' + au.error : '');
        dsp.title = au.error || '';
        dsp.className = 'warn';
      }

      $('dCov').textContent = kb(g.coverBytes);
    } catch (e) { /* 服务没起时静默 */ }
  }

  /* 预览 iframe 地址：带上 demo（没歌也有画面）+ edit（卡片大小手柄）；
     控制台自己带 nosse 时（截图/省 CPU），预览也不开长连接 */
  function previewUrl(extra) {
    const nosse = location.search.indexOf('nosse') >= 0 ? '&nosse=1' : '';
    return 'index.html?bg=studio&demo=1&edit=1' + nosse + (extra ? '&_=' + Date.now() : '');
  }

  /* ---------------- 轮询 ---------------- */
  async function poll() {
    try {
      const r = await fetch('/api/snapshot?_=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const snap = await r.json();

      if (!state) {
        state = snap.state;
        renderControls();
        $('preview').classList.remove('off');
        $('footState').textContent = '已连接 · ' + (snap.server || '');
      }
      renderNow(snap.now, snap.bridge);
      // 预览里拖出来的位置 / 大小 / 透明度 → 面板读数跟上
      //（自己刚拖过的滑杆 800ms 内不回写，免得跟轮询打架）
      if (snap.state) {
        LOOKS.forEach((L) => {
          if (typeof snap.state[L.x] === 'number') {
            state[L.x] = snap.state[L.x];
            state[L.y] = snap.state[L.y];
          }
          if (typeof snap.state[L.opacity] === 'number') state[L.opacity] = snap.state[L.opacity];
          if (typeof snap.state[L.scale] === 'number' && Date.now() - lastScaleEdit > 800) {
            state[L.scale] = snap.state[L.scale];
          }
        });
        if (typeof snap.state.specScale === 'number') {
          state.specX = snap.state.specX;
          state.specY = snap.state.specY;
          if (Date.now() - lastScaleEdit > 800) state.specScale = snap.state.specScale;
        }
        if (typeof snap.state.radius === 'number' && Date.now() - lastScaleEdit > 800) {
          state.radius = snap.state.radius;
        }
        if (typeof snap.state.coverSpin === 'boolean') state.coverSpin = snap.state.coverSpin;
        syncLooksUi();
      }
      $('urlText').textContent = location.origin + '/index.html';
    } catch (e) {
      $('preview').classList.add('off');
      $('footState').textContent = '连接失败：' + e.message;
      $('stDot').className = 'dot bad';
      $('stText').textContent = '本地服务没起来 —— 双击 start.cmd 再刷新这个页面';
    }
  }

  /* ---------------- 按钮 ---------------- */
  function bindButtons() {
    $('btnCopy').addEventListener('click', async () => {
      const url = location.origin + '/index.html';
      try {
        await navigator.clipboard.writeText(url);
        flashFoot('地址已复制');
      } catch (e) {
        // file:// 或权限受限时退化成选中
        const ta = document.createElement('textarea');
        ta.value = url; document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); ta.remove();
        flashFoot('地址已复制');
      }
    });

    $('btnReload').addEventListener('click', () => {
      $('frame').src = previewUrl(true);
      flashFoot('预览已重载');
    });

    $('btnDiag').addEventListener('click', () => {
      window.open('/api/diagnose', '_blank');
      flashFoot('诊断已导出：把下载的 txt 发回来');
    });

    $('btnRestartBridge').addEventListener('click', async () => {
      try {
        const r = await fetch('/api/bridge/restart', { method: 'POST' });
        const j = await r.json();
        flashFoot(j.ok ? '数据桥已重启' : ('重启失败：' + (j.error || r.status)));
      } catch (e) {
        flashFoot('重启失败：' + e.message);
      }
    });
  }

  function init() {
    bind();
    bindButtons();
    $('frame').src = previewUrl();
    poll();
    pollDiag();
    setInterval(poll, 1000);
    setInterval(pollDiag, 2000);
    setInterval(tick, 250);
  }

  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', init);
  else init();
})();

