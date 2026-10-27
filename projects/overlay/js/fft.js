/* 频谱分析：自写 radix-2 FFT + 频段映射（替代 numpy）
 *
 * Python 只负责录回环 PCM（int16 单声道，/api/pcm-stream），FFT 和频段映射都在这里算。
 * 映射规则和旧版 numpy 实现逐项对齐：
 *   - 64 个对数等距中心频率，40Hz → 16kHz
 *   - 4.5dB/oct 抬高频（音乐能量天然低频重，不抬频谱铺不开）
 *   - 以整体 RMS 为参考电平做相对映射（音量大小不改变画面高度）
 *   - 静音门 + 上升快、回落慢
 */
(function () {
  const F_LO = 40, F_HI = 16000;
  const TILT_PER_OCT = 4.5, TILT_REF = 120;
  const REF_OFFSET = -45, REF_SPAN = 39, SILENCE_DB = -58;

  class Analyzer {
    constructor(n = 2048, bands = 64) {
      this.n = n;
      this.bands = bands;
      this.re = new Float32Array(n);
      this.im = new Float32Array(n);
      this.win = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        this.win[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1));   // Hann
      }

      // 旋转因子表：每级复用同一张表（比逐级 cos/sin 快，也没有递推误差）
      const half = n >> 1;
      this.tw = new Float32Array(half);
      this.ti = new Float32Array(half);
      for (let k = 0; k < half; k++) {
        const a = (-2 * Math.PI * k) / n;
        this.tw[k] = Math.cos(a);
        this.ti[k] = Math.sin(a);
      }

      const bits = Math.log2(n) | 0;
      this.rev = new Uint32Array(n);
      for (let i = 0; i < n; i++) {
        let r = 0;
        for (let b = 0; b < bits; b++) if (i & (1 << b)) r |= 1 << (bits - 1 - b);
        this.rev[i] = r;
      }

      this.db = new Float32Array(half);
      this.smooth = new Float32Array(bands).fill(0.02);
      this.rate = 0;
      this.plan = [];
      this.refDb = -30;
    }

    /* 采样率变了就重算频段→bin 的映射（只在开头调用一次） */
    setRate(rate) {
      if (!rate || rate === this.rate) return;
      this.rate = rate;
      const n = this.n, df = rate / n;
      const hi = Math.min(F_HI, (rate / 2) * 0.95);
      const plan = [];
      for (let b = 0; b < this.bands; b++) {
        const c = F_LO * Math.pow(hi / F_LO, b / (this.bands - 1));
        let k = Math.floor(c / df);
        if (k < 1) k = 1;
        if (k > n / 2 - 2) k = n / 2 - 2;
        const f0 = k * df, f1 = (k + 1) * df;
        const f = Math.log(c / f0) / Math.log(f1 / f0);      // 对数轴插值系数
        const tilt = Math.max(0, TILT_PER_OCT * Math.log2(c / TILT_REF));
        plan.push({ i0: k, i1: k + 1, f, tilt });
      }
      this.plan = plan;
      this.smooth.fill(0.02);
    }

    fft() {
      const n = this.n, re = this.re, im = this.im;
      const rev = this.rev, tw = this.tw, ti = this.ti;

      for (let i = 0; i < n; i++) {                 // 位反转重排
        const j = rev[i];
        if (i < j) {
          let t = re[i]; re[i] = re[j]; re[j] = t;
          t = im[i]; im[i] = im[j]; im[j] = t;
        }
      }

      for (let len = 2; len <= n; len <<= 1) {      // 蝶形
        const h = len >> 1, step = n / len;
        for (let i = 0; i < n; i += len) {
          for (let j = 0, k = 0; j < h; j++, k += step) {
            const wr = tw[k], wi = ti[k];
            const a = i + j, b = a + h;
            const xr = re[b] * wr - im[b] * wi;
            const xi = re[b] * wi + im[b] * wr;
            re[b] = re[a] - xr; im[b] = im[a] - xi;
            re[a] += xr; im[a] += xi;
          }
        }
      }
    }

    /* pcm: Int16Array（单声道）→ 64 个 0..1 的频段值
       dt: 距上一帧的秒数（可选；给了就按时间做平滑，换帧率手感一致） */
    analyze(pcm, dt) {
      const n = this.n, re = this.re, im = this.im, win = this.win;
      const step = dt > 0 ? Math.min(0.1, dt) : 1 / 60;
      const len = pcm ? pcm.length : 0;
      let sum = 0;
      for (let i = 0; i < n; i++) {
        const v = (i < len ? pcm[i] : 0) / 32768;
        sum += v * v;
        re[i] = v * win[i];
        im[i] = 0;
      }

      const rms = Math.sqrt(sum / n);
      const rmsDb = 20 * Math.log10(rms + 1e-9);
      // 参考电平：涨得快、落得慢（时间常数 0.17s / 1.2s，不按帧数）
      const k = 1 - Math.exp(-step / (rmsDb > this.refDb ? 0.17 : 1.2));
      this.refDb += (rmsDb - this.refDb) * k;

      this.fft();

      const half = n >> 1, db = this.db, scale = 2 / n;
      db[0] = -120;
      for (let i = 1; i < half; i++) {
        const r = re[i], m = im[i];
        db[i] = 20 * Math.log10(Math.sqrt(r * r + m * m) * scale + 1e-9);
      }

      const gate = Math.max(0, Math.min(1, (rmsDb - SILENCE_DB) / 8));
      const ref = this.refDb + REF_OFFSET;
      const out = this.smooth;
      const fall = Math.exp(-step / 0.23);          // 回落时间常数 0.23s

      if (!this.plan.length) this.setRate(this.rate || 48000);
      for (let b = 0; b < this.bands; b++) {
        const p = this.plan[b];
        const v = db[p.i0] * (1 - p.f) + db[p.i1] * p.f + p.tilt;
        let norm = ((v - ref) / REF_SPAN) * gate;
        norm = norm < 0 ? 0 : norm > 1 ? 1 : norm;
        out[b] = norm > out[b] ? norm : out[b] * fall;
      }
      return out;
    }
  }

  window.FFT = { Analyzer };
})();
