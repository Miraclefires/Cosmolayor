# 第十二轮技术要点（2026-10-27）

> 范围：大频谱可拖可缩 + 去背景、卡片底部 30px 频谱、条幅定宽重排、组件次级菜单。
> 代码都在 `projects/overlay/`，这一轮**已人工复核通过**（用户："brilliant"），下面记的是"为什么这么写"和"改哪里会踩什么坑"。
>
> ⚠️ **第十三轮已改动本文两处**：`specScale` 上限 `1.5` → **`1.0`**（缩放上限统一改成"当前默认大小"）；
> 位置字段扩展成四个组件共用（`badgeX/Y`、`bannerX/Y`、`cardX/Y` + `specX/Y`）。
> 现状以 [`HANDOFF.md`](HANDOFF.md) 的「第十三轮」为准，本文保留作为当时的决策记录。

---

## 1. 状态模型：新增 4 个字段

| 字段 | 默认 | 范围 | 谁写 | 谁读 |
|---|---|---|---|---|
| `specX` | `0` | **-200 ～ 1200** | 预览里拖频谱本体 / 面板重置 | `layout()` 写 `left` |
| `specY` | `0` | **0 ～ 900** | 同上 | `layout()` 写 `bottom` |
| `specScale` | `1.0` | **0.4 ～ 1.5**（锚点左下） | 拖右下角圆点 / 面板重置 | `--ss` → `transform: scale()` |
| `cardSpectrum` | `true` | bool | 组件「卡片」▾ 里的开关 | 卡片高度 + `drawCardSpec()` |

三处夹取必须一致（**改一处就要三处一起改**）：

1. 前端 `js/overlay.js:81-91` —— `SPEC_SCALE_MIN/MAX`、`SPEC_X_MIN/MAX`、`SPEC_Y_MIN/MAX` + `clampNum()`
2. 服务端 `server.py:70-90` —— 同名常量 + `clamp_num()`（非法/NaN 返回 `None`，调用方**保留旧值**而不是写 0）
3. 载入兜底 `server.py:118-127` —— `load_state()` 里对旧 state.json 再夹一次（右半边角标、缺字段的旧文件都在这里救）

> 为什么服务端要"非法就忽略"而不是"回默认"：拖拽期间前端会连续 POST，如果某次带了个坏值，回默认会让画面突然跳一下。

---

## 2. 大频谱：无背景 + 可拖 + 可缩

### 2.1 去背景（需求原话：取消背景渐变透明）

`css/overlay.css:284-295`：

```css
.spectrum-layer.full {
  background: none;            /* 原来有 rgba(0,0,0,.46) 的渐变底衬 */
  -webkit-mask-image: none;    /* 原来左侧 470→760px 的淡出遮罩，为角标让光 */
  mask-image: none;
  transform-origin: left bottom;
  transform: scale(var(--ss, 1));
  pointer-events: none;        /* 编辑模式才打开 */
  overflow: visible;           /* 编辑读数在框外，别被裁 */
}
```

- **去掉 mask 是必须的**：频谱现在能自由移动，写死"左侧淡出"就会跟着跑到右上角去
- `overflow: visible` 是被裁过才发现：`.spectrum-layer` 基础样式有 `overflow: hidden`，读数的 `top: -34px` 会被切掉

### 2.2 位置与缩放

`js/overlay.js:204-215`（`layout()` 的 full 分支）：

```js
layer.style.left = clampSpecX(state.specX) + 'px';
layer.style.bottom = clampSpecY(state.specY) + 'px';   // 距底边，不是 top
layer.style.width = '1920px';                          // 逻辑宽度不变，缩放交给 transform
layer.style.height = '140px';
layer.style.setProperty('--ss', ss);
layer.style.setProperty('--hs2', 1 / (ss * stageScale));
```

- 用 `bottom` 而不是 `top`：默认 `y=0` 就天然是"贴底通栏"，不用算 1080-140
- **锚点必须 `left bottom`**：否则右下角手柄一拖，整条会向左上飘
- canvas 尺寸跟着布局走，`transform` 只是视觉效果 —— 缩小时是降采样，不会糊

### 2.3 编辑交互（只在 `?edit=1`）

`js/overlay.js:657-751`：

| 手势 | 目标 | 换算 |
|---|---|---|
| 拖本体 | 改 `specX/specY` | `specX += dx/stageScale`，`specY -= dy/stageScale`（往上拖 = 离底更远） |
| 拖右下圆点 | 改 `specScale` | `s0 + (dx - dy) / 700`（两个方向都算，"700 舞台像素 ≈ 1.0 倍"） |

- `stageScale` = `fit()` 里算的舞台缩放（1920 画布塞进窗口的比例），不除它拖起来会跟手差一倍
- 松手才 POST（`pointerup`），拖动中只改本地 `style` —— 免得每帧一次请求
- 手柄 `--hs2 = 1/(specScale × stageScale)`：不反算的话，缩到 0.5 后手柄只剩 11px 屏幕上根本点不到

### 2.4 叠放顺序（容易忽略的破坏性改动）

原来是 `z-index: 5`，频谱会盖在条幅/卡片上。这轮改成**不给 z-index**：

- `.spectrum-layer` 是 `#stage` 的第一个子节点 → 按 DOM 顺序垫底
- 条幅/卡片 `z-index: auto` → 在它上面
- 角标 `z-index: 50` → 永远最上

> 想改回"频谱总在最前"，只改一处：给 `.spectrum-layer` 加回 `z-index: 5`。

---

## 3. 卡片底部频谱（30px）

### 3.1 尺寸与布局

| 项 | 值 | 说明 |
|---|---|---|
| 画布高 | **30px** | = 角标频谱最大条高（`badge.js` 里 `h = v * 30 * env`，env ≤ 1） |
| 画布宽 | 卡片内容宽 368（420 − 2×26 padding） | `width: 100%` |
| 间距 | `margin-top: 10px` | 与时间码行分开 |
| 卡片高 | **170 → 208** | 内容 164：row 88 + 条 17 + 时间 18 + 频谱 40 ≈ 163 |

`css/overlay.css:206-217`：关掉时 `.card.no-spec { height: 170px }` 把高度还回去，不留空洞。

### 3.2 数据：复用同一台频谱引擎

`js/overlay.js:426-456` 的 `drawCardSpec()`：

```js
const n = Math.max(16, Math.floor(w / 9));   // 368 宽 → 40 根
const shape = spec.getShape(n);              // 直接吃大频谱那台的形状
const barH = Math.max(2, v * h);             // 上限 = h（30px）
```

- **没有第二条数据链路**：SSE → FFT 只有 `Spec Analyzer` 一份，卡片/角标都是从 `getShape()` 采样
- 采样是**线性重采样**（`getShape` 里按比例取两邻点插值），所以卡片 40 根、角标 114 根、大频谱 274 根，形状一致
- 没有自己的 rAF：挂在页面 45ms 的绘制闸门里（~22fps），省 CPU

### 3.3 引擎启动条件

`js/overlay.js:347`：

```js
const needSpec = !idle && (state.visible.spectrum || state.badgeSpectrum || state.cardSpectrum);
```

> 漏写 `cardSpectrum` 的后果：大频谱和角标频谱都关掉、只留卡片频谱时，引擎不转 → 卡片频谱静止。

---

## 4. 条幅重排：修掉"进度条一动，歌手一跳"

### 4.1 根因

旧实现：歌手靠 `margin-left: auto` 顶在 meta 右边缘，而 meta 是 `flex: 1`。

```
总宽 = 封面 + 间距 + meta(flex:1) + 间距 + 进度条(220) + 间距 + 时长(auto)
```

时长是**比例字体**，`01:19 → 01:20` 或 `1 → 0` 时宽度会变 1～2px → meta 宽度反着变 → 歌手的右边缘跟着抖。看起来就像"进度条在挤歌手"。

### 4.2 新结构

`index.html`：`#bannerArtist` 从 `.meta` 里挪出来，排在曲名后面（**顺序即语义**）。

`css/overlay.css:108-140`：

| 元素 | 规则 | 作用 |
|---|---|---|
| `.banner` | `gap: 18px; padding: 0 34px` | 五段等距 |
| `.banner .meta` | `flex: 1 1 auto; min-width: 0` | **唯一会伸缩的**（曲名） |
| `.banner .t2`（歌手） | `flex: 0 0 auto; max-width: 260px` | 固定宽、超长省略号 |
| `.banner .bar` | `flex: 0 0 200px` | 固定 |
| `.banner .time` | `flex: 0 0 auto; min-width: 92px; text-align: right` | **定宽** |
| `.banner .time` | `font-variant-numeric: tabular-nums; font-feature-settings: "tnum"` | 数字等宽，秒数变化不改宽度 |

宽度账（内容区 932px）：`56 封面 + 72 四个间距 + 200 进度条 + 92 时长 = 420`，剩 **512** 给"曲名 + 歌手"；歌手按内容宽（常见 70～120），所以曲名常态 ~400，长名字时最差也有 ~250 再触发滚动。

> 只要"时长定宽 + 等宽数字"这一条在，歌手就不会再动 —— 双保险是把它移出 flex:1 容器。

### 4.3 没动的东西

- 长标题跑马灯（`setupMarquee`）只看 `.t1` 的 `scrollWidth/clientWidth`，结构变了照常工作
- 切歌动画 `.swapping .t1/.t2` 照旧

---

## 5. 组件次级菜单

`js/control.js:55-63`：表驱动，**加子项只改一行**。

```js
const SUBMENUS = {
  badge: [{ id: 'swBadgeSpec', key: 'badgeSpectrum', name: '角标频谱', desc: '…' }],
  card:  [{ id: 'swCardSpec',  key: 'cardSpectrum',  name: '卡片频谱', desc: '…' }],
};
const subOpen = { badge: true, card: false };   // 只存内存，不落 state
```

- 组件行右侧渲染 `.sub-btn`（`▾`，展开时 180° 旋转 + 主色底），点击只切 `subOpen` 并重渲染
- 真正的开关在 `.sub-wrap` 里，`id` 保持 `swBadgeSpec` / `swCardSpec`，`change` 事件里逐项判断（没有做成通用绑定，因为每个键的语义不同，显式写更不容易错）
- 展开状态**故意不落盘**：刷新面板回到默认（角标展开、卡片收起），避免"上次收起后忘了有这么个开关"

CSS：`css/control.css:110-127`（`.sub-btn` / `.sub-wrap.open`）。

---

## 6. 面板侧：读数与重置

- `js/control.js:106-118` `specHtml()`：显示 `x, y · 倍率` + 「重置到通栏底部」；重置直接写 `specX=0, specY=0, specScale=1` 再 `save()`
- `js/control.js:354-362`：每秒轮询把服务端的 `specX/specY/specScale` 同步到读数（预览里拖完 1 秒内面板跟上）
- 卡片大小那行同理，但多一层"自己刚改的 800ms 内不回写"，否则拖滑杆会被轮询顶回去

---

## 7. 验证

| 项 | 结果 |
|---|---|
| `python tools/selftest.py` | **30 项 29 过 / 1 跳过 / 0 失败** |
| 新增断言（「开关校验」里） | `specScale 9 → 1.5`、`specX -9999 → -200`、`specY 9999 → 900`、`cardSpectrum` 布尔 |
| 语法 | `node --check`（overlay/control/badge）、`py_compile`（server/bridge/selftest） |
| 真机 | 用户把频谱拖到 `1006, 373 · 0.40×`、卡片 `1.5×`，无异常 |

**没验的**：手柄在屏幕最右侧时是否够得着（1920 宽条子拖到 `x=1200` 时手柄在 `x≈1200+1920×0.4`）。

---

## 8. 已知取舍 / 下次可以改的

1. **叠放顺序**：频谱现在是"垫底"，想让它盖在卡片上就加回 `z-index: 5`
2. **缩放灵敏度**：`/700` 是个手调常数（`overlay.js:724`），嫌慢改小（如 `/400`）
3. **卡片 208 高**：频谱 30 + 间距 10 是硬编码，改条高就要同步改卡片高度和 `.no-spec` 的 170
4. **窄条模式**不吃 `specX/specY/specScale`（它跟着条幅走），如果想让它也能自由放置，得在 `layout()` 的 narrow 分支里另开一套
5. **拖拽只能从"本体可见区域"开始**：频谱被卡片挡住的部分点不到（`pointer-events` 在卡片上）

---

## 9. 文件索引

| 文件 | 这轮改了哪些块 |
|---|---|
| `index.html` | 条幅：歌手移出 `.meta`；卡片：加 `<canvas id="cardSpec">` |
| `js/overlay.js` | L29-32 新字段、L81-91 夹取、L204-215 频谱布局、L347 引擎条件、L426-456 `drawCardSpec`、L657-751 频谱编辑器 |
| `js/control.js` | L37-51 卡片大小、L55-63 SUBMENUS、L106-118 频谱读数、L132-137 两个开关、L162-175 重置、L354-362 轮询同步 |
| `js/badge.js` | 未改（角标频谱的 30px 上限仍是规格来源） |
| `css/overlay.css` | L108-140 条幅、L194-217 卡片+频谱、L272-344 频谱层/编辑态 |
| `css/control.css` | L110-127 次级菜单、L185-202 滑杆/提示 |
| `server.py` | L58-61 默认值、L70-90 常量与夹取、L118-127 载入兜底、L174-185 写入校验 |
| `tools/selftest.py` | 「开关校验」加了频谱/卡片频谱断言 |
