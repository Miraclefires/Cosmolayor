/* 频谱渲染（真实/模拟共用）—— 数据 30fps 推来，画面按 60fps 插值，
   平滑用时间常数算（不按帧数），换刷新率手感一致。 */
(function () {
  const BAR_W = 3, GAP = 7;         // 与角标同款：条宽 3、间距 7
  const FPS = 60;

  function smoothNoise(n, seed) {
    let s = seed;
    const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
    const raw = Array.from({ length: n }, () => 0.15 + rnd() * 0.85);
    // 左右各平滑两次，得到"低频包络"的手感
    let a = raw.slice();
    for (let k = 0; k < 2; k++) {
      const b = a.slice();
      for (let i = 0; i < n; i++) {
        const p = a[(i - 1 + n) % n], c = a[i], q = a[(i + 1) % n];
        b[i] = (p + c * 2 + q) / 4;
      }
      a = b;
    }
    const mx = Math.max(...a) || 1;
    return a.map(v => Math.pow(v / mx, 1.15));
  }

  class Spectrum {
    constructor(canvas) {
      this.cv = canvas;
      this.ctx = canvas.getContext('2d');
      this.acc = '255,45,149';
      this.accA = [255, 45, 149];
      this.accB = [255, 45, 149];
      this.grad = false;
      this.playing = true;
      this.running = false;
      this.real = null;
      this.realAt = 0;
      this.t = 0;
      this.last = 0;
      this.heights = [];
      this.base = [];
      this.resize();
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(canvas);
    }

    resize() {
      const r = this.cv.getBoundingClientRect();
      if (!r.width || !r.height) return;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      this.cv.width = Math.round(r.width * dpr);
      this.cv.height = Math.round(r.height * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.w = r.width; this.h = r.height;
      const n = Math.max(24, Math.floor(this.w / GAP));
      this.base = smoothNoise(n, 20261026);
      this.heights = this.base.map(v => v * 0.4);
      this.n = n;
    }

    /* 给角标用：把当前形状重采样成 count 个 0..1 的值 */
    getShape(count) {
      const n = this.n || 0;
      if (!n) return new Array(count).fill(0.1);
      const out = new Array(count);
      for (let i = 0; i < count; i++) {
        const p = count === 1 ? 0 : (i / (count - 1)) * (n - 1);
        const i0 = Math.floor(p);
        const f = p - i0;
        const a = this.heights[i0] || 0;
        const b = this.heights[Math.min(n - 1, i0 + 1)] || 0;
        out[i] = a * (1 - f) + b * f;
      }
      return out;
    }

    setPalette(p) {
      const hex = (h, dflt) => {
        const s = String(h || '').replace('#', '');
        if (s.length !== 6) return dflt;
        return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
      };
      this.accA = hex(p.acc, [255, 45, 149]);
      this.acc = p.accRgb || this.accA.join(',');
      this.grad = !!p.grad;
      this.accB = p.grad ? hex(p.c2, this.accA) : this.accA;
    }
    setPlaying(v) { this.playing = !!v; }
    setFlip(v) { this.flip = !!v; }

    /* 真实频谱：64 个 0..1 的频段（来自 /api/spectrum-stream）。
       传 null 就退回模拟动效。 */
    setLevels(arr) {
      if (arr && arr.length) {
        this.real = arr;
        this.realAt = performance.now();
      } else {
        this.real = null;
      }
    }

    start() {
      if (this.running) return;
      this.running = true;
      this.last = performance.now();
      const step = (now) => {
        if (!this.running) return;
        this._raf = requestAnimationFrame(step);
        if (now - this.last < 1000 / FPS) return;
        const dt = Math.min(0.1, (now - this.last) / 1000);
        this.last = now;
        this.t += dt;
        this.draw(dt);
      };
      this._raf = requestAnimationFrame(step);
    }

    stop() {
      this.running = false;
      if (this._raf) cancelAnimationFrame(this._raf);
    }

    draw(dt) {
      const ctx = this.ctx, W = this.w, H = this.h, n = this.n;
      if (!W || !H) return;
      dt = dt || 1 / FPS;
      ctx.clearRect(0, 0, W, H);

      const energy = this.playing ? 1 : 0.28;
      const speed = this.playing ? 1 : 0.35;
      const beat = this.playing ? Math.pow(1 - ((this.t % 0.55) / 0.55), 3.2) : 0;

      const step = W / n;
      ctx.lineCap = 'round';
      ctx.lineWidth = BAR_W;

      // 真实数据超过 1.5 秒没来就当断了，退回模拟
      const real = this.real && this.real.length &&
        (performance.now() - (this.realAt || 0) < 1500) ? this.real : null;
      const L = real ? real.length : 0;

      for (let i = 0; i < n; i++) {
        let target;
        if (L) {
          const p = L === 1 ? 0 : (i / (n - 1)) * (L - 1);
          const i0 = Math.floor(p);
          const f = p - i0;
          const v = real[i0] * (1 - f) + real[Math.min(L - 1, i0 + 1)] * f;
          target = Math.min(1, Math.pow(v, 1.12) * 1.04);
          const k = 1 - Math.exp(-dt / 0.040);       // 40ms 时间常数（跟拍快）
          this.heights[i] += (target - this.heights[i]) * k;
        } else {
          const b = this.base[i];
          const wob = 0.58 + 0.42 * Math.sin(this.t * (0.55 + b * 0.55) * speed + i * 0.41);
          target = Math.min(1, (0.05 + 0.72 * b * wob + 0.16 * beat * b) * energy);
          const k = 1 - Math.exp(-dt / 0.10);
          this.heights[i] += (target - this.heights[i]) * k;
        }
        const h = Math.max(2, this.heights[i] * H);

        const x = i * step + step * 0.5;
        const alpha = (0.22 + 0.68 * (h / H)).toFixed(3);
        if (this.grad) {
          // 渐变配色：按条的位置在 c1 → c2 之间插值（每条颜色不一样，像霓虹灯管）
          const t = n > 1 ? i / (n - 1) : 0;
          const a = this.accA, b = this.accB;
          const r = (a[0] + (b[0] - a[0]) * t) | 0;
          const g2 = (a[1] + (b[1] - a[1]) * t) | 0;
          const bl = (a[2] + (b[2] - a[2]) * t) | 0;
          ctx.strokeStyle = `rgba(${r},${g2},${bl},${alpha})`;
        } else {
          ctx.strokeStyle = `rgba(${this.acc},${alpha})`;
        }
        ctx.beginPath();
        ctx.moveTo(x, H);
        ctx.lineTo(x, H - h);
        ctx.stroke();
      }
    }
  }

  window.Spectrum = Spectrum;
})();
