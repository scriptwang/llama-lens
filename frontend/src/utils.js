import { ref, watch, onScopeDispose, isRef } from 'vue'

// ---------------- 格式化 ----------------
export function fmtNum(v, digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number(v).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  })
}

export function fmtBytes(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let n = Number(v)
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return `${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`
}

export function fmtPct(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${Number(v).toFixed(digits)}%`
}

export function fmtParams(n) {
  if (!n) return '—'
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  return String(n)
}

export function fmtTokens(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  n = Number(n)
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`
  return String(Math.round(n))
}

export function fmtGB(mb) {
  if (mb === null || mb === undefined || Number.isNaN(mb)) return '—'
  return `${(mb / 1024).toFixed(1)}G`
}

export function fmtDuration(s) {
  if (s === null || s === undefined || Number.isNaN(s)) return '—'
  s = Math.floor(s)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}h${String(m).padStart(2, '0')}m`
  if (m > 0) return `${m}m${String(sec).padStart(2, '0')}s`
  return `${sec}s`
}

export function fmtClock(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return d.toTimeString().slice(0, 8)
}

export function fmtTimeShort(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

// 锚点时间范围起点（毫秒）：today=今日 0 点 / week=本周一 0 点 / month=本月 1 号 0 点
export function anchorStartMs(a) {
  const d = new Date()
  if (a === 'week') {
    const day = (d.getDay() + 6) % 7  // 周一=0
    return new Date(d.getFullYear(), d.getMonth(), d.getDate() - day).getTime()
  }
  if (a === 'month') return new Date(d.getFullYear(), d.getMonth(), 1).getTime()
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()  // today（缺省）
}

// ---------------- 阈值级别 ----------------
// alerts: [{metric, level, ...}]；返回某指标的最高级别
export function alertLevel(alerts, metricPrefix) {
  if (!alerts) return 'normal'
  let level = 'normal'
  for (const a of alerts) {
    if (metricPrefix && !a.metric.startsWith(metricPrefix)) continue
    if (a.level === 'danger') return 'danger'
    if (a.level === 'warn') level = 'warn'
  }
  return level
}

export function alertOf(alerts, metric) {
  if (!alerts) return null
  return alerts.find((a) => a.metric === metric) || null
}

// ---------------- 条形图动态量程（1/2/2.5/5 × 10^n 取整） ----------------
export function niceMax(v) {
  if (v === null || v === undefined || !Number.isFinite(v) || v <= 0) return 10
  const exp = Math.floor(Math.log10(v))
  const base = Math.pow(10, exp)
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (m * base >= v) return m * base
  }
  return 10 * base
}

// ---------------- sparkline（内联 SVG 路径） ----------------
export function sparkPath(points, w = 120, h = 28, pad = 2) {
  if (!points || points.length < 2) return ''
  let min = Infinity
  let max = -Infinity
  for (const p of points) {
    const v = p[1]
    if (v === null || v === undefined) continue
    if (v < min) min = v
    if (v > max) max = v
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return ''
  if (max - min < 1e-9) { min -= 1; max += 1 }
  const t0 = points[0][0]
  const t1 = points[points.length - 1][0]
  const span = Math.max(1e-9, t1 - t0)
  let d = ''
  let started = false
  for (const [t, v] of points) {
    if (v === null || v === undefined) { started = false; continue }
    const x = pad + ((t - t0) / span) * (w - pad * 2)
    const y = h - pad - ((v - min) / (max - min)) * (h - pad * 2)
    d += started ? ` L${x.toFixed(1)},${y.toFixed(1)}` : `M${x.toFixed(1)},${y.toFixed(1)}`
    started = true
  }
  return d
}

// ---------------- count-up 数字动画（300ms rAF） ----------------
export function useCountUp(target, duration = 300) {
  const value = ref(0)
  let raf = 0
  // target 可能是 ref/computed 或普通值；getter 必须解包，
  // 否则 watcher 追踪不到值变化（回调只触发一次且拿到 ref 对象）
  const getter = () => (isRef(target) ? target.value : target)
  watch(
    getter,
    (to) => {
      if (typeof to !== 'number' || Number.isNaN(to)) {
        value.value = to
        return
      }
      const from = typeof value.value === 'number' ? value.value : 0
      if (from === to) { value.value = to; return }
      const t0 = performance.now()
      cancelAnimationFrame(raf)
      const step = (now) => {
        const k = Math.min(1, (now - t0) / duration)
        const eased = 1 - Math.pow(1 - k, 3)
        value.value = from + (to - from) * eased
        if (k < 1) raf = requestAnimationFrame(step)
      }
      raf = requestAnimationFrame(step)
    },
    { immediate: true }
  )
  onScopeDispose(() => cancelAnimationFrame(raf))
  return value
}
