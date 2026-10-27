# Cosmolayor v1.0.0

**给 OBS 的直播叠加层** —— 角标 / 条幅 / 播放卡片 / 频谱，一个页面、一个浏览器源、零第三方依赖、数据全本地。

---

## 下载

| 文件 | 大小 | 说明 |
|---|---:|---|
| `Cosmolayor-1.0.0-win64.zip` | 13.9 MB | 解压即用，**自带 Python**，不用安装任何东西 |

校验：

```
SHA256  da25c4ddff4282730fddb650389cf34ae9bec1be3c1941b58ce1b2def95ff8dd
```

```powershell
Get-FileHash .\Cosmolayor-1.0.0-win64.zip -Algorithm SHA256
```

---

## 这一版有什么

**四个组件，一个 OBS 源**

- **角标**：三角徽章（封面 / 歌名 / 进度），左下或左上镜像布局，斜边上是实时频谱
- **条幅**：一行到底 —— 封面 / 歌名 / 歌手 / 进度条 / 时长；超长文字来回滚动
- **播放卡片**：大封面 + 进度 + 底部 30px 小频谱，0.75×～1.5× 等比缩放
- **频谱**：64 段真实频谱，位置 / 大小 / 帧率（30 或 60fps）都能调

**数据全是真的，而且一步都不出本机**

| 数据 | 来源 |
|---|---|
| 歌名 / 歌手 / 播放状态 | Windows 媒体会话（SMTC） |
| 封面 | 自写 C# 读 SMTC 缩略图，系统自带 `csc.exe` 编译 |
| 进度 | 网易云 `cloudmusic.elog` 自写解码（真实 position / 时长 / seek） |
| 频谱 | WASAPI **进程回环**，只抓 `cloudmusic.exe`，浏览器的声音不会串进来 |

不联网、不上传、不需要登录。

**控制面板**

- 每个组件都有自己的 ▾ 菜单：开关 / 位置 / 透明度 / 大小 / 重置
- 四个组件全都能在预览里直接拖到任意位置，松手自动保存
- 8 套配色，其中 4 套霓虹撞色的强调色是**渐变**
- **跟随封面取色**：每首歌按封面主色生成一套配色，可再叠渐变
- 整体圆角 0～44、封面旋转（仅圆形）、封面形状切换、实时预览

**零第三方依赖**：numpy / pyaudiowpatch / winrt / netease 解析库四个依赖全部换成自写实现，
不用 `pip install`，标准库 + Windows 自带组件（PowerShell 5.1、.NET `csc.exe`、WASAPI）就够。

---

## 第一次运行

1. 解压到任意目录（**不要放在 C:\Program Files 这种需要管理员权限的地方**）
2. 双击 `Cosmolayor.exe`：弹出黑窗口（日志在里面）→ 2 秒后自动打开控制面板
3. OBS → 来源 → 加号 → **浏览器**：宽 `1920` 高 `1080`，地址 `http://127.0.0.1:8765/index.html`，勾「源不可见时关闭」

关掉黑窗口 = 停服务。只想起服务、不弹浏览器：`Cosmolayor.exe --no-open`。

> **第一次运行会被 SmartScreen 拦一下**（exe 还没做代码签名）：
> 「Windows 已保护你的电脑」→ 点 **更多信息 → 仍要运行**。
> 不想看到这个提示就双击 `Cosmolayor.cmd`，功能完全一样（cmd 不触发 SmartScreen）。

---

## 兼容性

| 项 | 说明 |
|---|---|
| 系统 | Windows 10 20348+ / Windows 11（按进程抓音频需要这个版本；更老的系统自动退回整机混音） |
| 播放器 | **网易云音乐**：元数据 / 封面 / 真实进度全部支持；其它播放器能拿元数据和封面，进度看它给不给 SMTC |
| OBS | 任意版本，浏览器源（CEF）即可 |

---

## 已知限制

- **只支持 Windows**：WASAPI / SMTC / WinRT 都是 Windows 组件
- **完整进度是网易云专属**：靠解析它自己的日志，客户端改格式时退回估算（诊断面板会写明来源）
- **没有歌词**：歌词面板在 1.0 前被整个移除了，这一版不包含
- **封面是系统媒体浮窗那张 108×108**，放大到卡片尺寸会有点虚
- **角标只有左下 / 左上**：右半边（右下 / 右上）试过，阅读方向不对，故意不做
- **换默认输出设备后要重启服务**：旧设备还活着时采集线程会继续录旧的（表现为频谱不动）
- **exe 未签名**：见上面的 SmartScreen 说明

---

## 文档

- 完整用法 / 全部开关 / 手改参数：[`projects/overlay/README.md`](projects/overlay/README.md)（发布包内同名文件）
- 更新日志：[`projects/overlay/CHANGELOG.md`](projects/overlay/CHANGELOG.md)
- 给测试者的检查清单：发布包内 `TESTING.md`

---

## 许可

- 代码：**MIT**（附中文参考译文）
- 文档与效果图：**CC BY 4.0**
- 作者：**11.2 / 天月焰**
- 音乐封面、艺人肖像、第三方商标不在授权范围内，权利归各自所有者

---

<details>
<summary>English</summary>

**Cosmolayor** is a now-playing overlay for OBS: corner badge, banner, player card and a real-time
spectrum — one browser source, one local process, zero third-party dependencies, no network access.

- Now-playing data comes from Windows SMTC; real playback position is decoded from NetEase Cloud
  Music's local `cloudmusic.elog`; cover art is read from the SMTC thumbnail by a small self-built
  C# helper; the spectrum is a WASAPI **process loopback** capture of `cloudmusic.exe` only, FFT'd
  in the page.
- Requires Windows 10 20348+ / Windows 11. The release zip bundles Python — unzip and run
  `Cosmolayor.exe`; accept the SmartScreen prompt on first launch (the exe is unsigned).
- MIT (code) / CC BY 4.0 (docs and artwork). Author: 11.2.

</details>
