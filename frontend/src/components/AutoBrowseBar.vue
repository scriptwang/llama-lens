<template>
  <div ref="barRef" class="ab-bar" :style="{ top: stickyTop }">
    <span class="ab-label">自动浏览</span>
    <el-switch v-model="enabled" size="small" />
    <template v-if="enabled">
      <div class="ab-seg">
        <button :class="{ on: mode === 'scroll' }" @click="setMode('scroll')">自动滑动</button>
        <button :class="{ on: mode === 'carousel' }" @click="setMode('carousel')">轮播</button>
      </div>
      <div v-if="mode === 'scroll'" class="ab-ctl">
        <span class="ab-k">速度</span>
        <input type="range" v-model.number="speed" min="20" max="400" step="10" class="ab-range" />
        <span class="ab-v">{{ speed }} px/s</span>
      </div>
      <div v-else class="ab-ctl">
        <span class="ab-k">时长</span>
        <input type="range" v-model.number="interval" min="2" max="30" step="1" class="ab-range" />
        <span class="ab-v">{{ interval }} 秒/屏</span>
      </div>
      <span v-if="paused" class="ab-paused">已暂停 · 手动滚动后 5 秒自动恢复</span>
    </template>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'

// 监控页自动浏览（纯前端）：
// - 自动滑动：按速度连续向下滚动，到底回顶循环
// - 轮播：每 N 秒滚动一屏（视口高 - 顶部固定区），到底回顶循环
// 用户手动滚动（滚轮/触摸）时暂停 5 秒后自动恢复；设置持久化到 localStorage。
const props = defineProps({
  active: { type: Boolean, default: false },   // 仅监控 Tab 激活时运行
  stickyTop: { type: String, default: '0px' }, // sticky 定位 top（顶栏+tabs 之下）
  chrome: { type: Number, default: 56 },       // 本条之上的固定区高度（顶栏+tabs），用于计算一屏步长
})

const STORE_KEY = 'llamalens.autobrowse'
const enabled = ref(false)
const mode = ref('scroll')        // scroll=自动滑动 | carousel=轮播
const speed = ref(80)             // 自动滑动速度 px/s
const interval = ref(8)           // 轮播时长 秒/屏
const paused = ref(false)

// 恢复上次设置
try {
  const saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null')
  if (saved) {
    if (typeof saved.enabled === 'boolean') enabled.value = saved.enabled
    if (saved.mode === 'scroll' || saved.mode === 'carousel') mode.value = saved.mode
    if (Number.isFinite(saved.speed)) speed.value = Math.min(400, Math.max(20, saved.speed))
    if (Number.isFinite(saved.interval)) interval.value = Math.min(30, Math.max(2, saved.interval))
  }
} catch { /* 忽略损坏的本地设置 */ }

watch([enabled, mode, speed, interval], () => {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({ enabled: enabled.value, mode: mode.value, speed: speed.value, interval: interval.value }))
  } catch { /* 隐私模式等写入失败可忽略 */ }
}, { deep: true })

const barRef = ref(null)
let raf = null
let lastTs = 0
let nextFlipAt = 0
let resumeAt = 0

function maxScroll() {
  return document.documentElement.scrollHeight - window.innerHeight
}

// 自动滑动：向下推进 dy 像素，到底回顶
function stepScroll(dy) {
  const max = maxScroll()
  if (max <= 0) return
  const next = window.scrollY + dy
  window.scrollTo(0, next >= max - 1 ? 0 : next)
}

// 轮播：向下滚一屏（视口高 - 固定区），到底回顶
function flipScreen() {
  const max = maxScroll()
  if (max <= 0) return
  const step = Math.max(window.innerHeight - props.chrome - (barRef.value?.offsetHeight || 40), 200)
  const next = window.scrollY + step
  window.scrollTo({ top: next >= max - 1 ? 0 : next, behavior: 'smooth' })
}

function tick(ts) {
  raf = requestAnimationFrame(tick)
  if (!enabled.value || !props.active) { lastTs = 0; nextFlipAt = 0; return }
  if (paused.value) {
    if (performance.now() >= resumeAt) { paused.value = false; lastTs = 0; nextFlipAt = 0 }
    return
  }
  if (mode.value === 'scroll') {
    const dt = lastTs ? Math.min((ts - lastTs) / 1000, 0.1) : 0
    lastTs = ts
    stepScroll(speed.value * dt)
  } else {
    if (!nextFlipAt) nextFlipAt = ts + interval.value * 1000
    if (ts >= nextFlipAt) {
      nextFlipAt = ts + interval.value * 1000
      flipScreen()
    }
  }
}

function startLoop() {
  if (raf == null) { lastTs = 0; nextFlipAt = 0; raf = requestAnimationFrame(tick) }
}
function stopLoop() {
  if (raf != null) { cancelAnimationFrame(raf); raf = null }
  lastTs = 0
  nextFlipAt = 0
}

watch([enabled, () => props.active], ([e, a]) => {
  if (e && a) startLoop()
  else stopLoop()
})

// 手动滚动 → 暂停 5 秒
function onUserScroll() {
  if (!enabled.value || !props.active) return
  paused.value = true
  resumeAt = performance.now() + 5000
}

function setMode(m) {
  mode.value = m
  lastTs = 0
  nextFlipAt = 0
}

onMounted(() => {
  window.addEventListener('wheel', onUserScroll, { passive: true })
  window.addEventListener('touchmove', onUserScroll, { passive: true })
  if (enabled.value && props.active) startLoop()
})
onBeforeUnmount(() => {
  window.removeEventListener('wheel', onUserScroll)
  window.removeEventListener('touchmove', onUserScroll)
  stopLoop()
})
</script>

<style scoped>
.ab-bar {
  position: sticky;
  z-index: 18;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  margin: 0 0 4px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--bg) 85%, transparent);
  backdrop-filter: blur(12px);
  border: 1px solid var(--card-border);
  font-size: 12px;
}
.ab-label {
  font-weight: 600;
  color: var(--text-dim);
  letter-spacing: 1px;
}
.ab-seg {
  display: flex;
  border: 1px solid var(--card-border);
  border-radius: 8px;
  overflow: hidden;
}
.ab-seg button {
  padding: 4px 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-dim);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: color .15s, background .15s;
}
.ab-seg button + button { border-left: 1px solid var(--card-border); }
.ab-seg button:hover { color: var(--text); }
.ab-seg button.on { color: var(--cyan); background: color-mix(in srgb, var(--cyan) 10%, transparent); }
.ab-ctl { display: flex; align-items: center; gap: 8px; }
.ab-k { color: var(--text-dim); }
.ab-range {
  width: 120px;
  accent-color: var(--cyan);
  cursor: pointer;
}
.ab-v {
  min-width: 64px;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
.ab-paused {
  color: var(--amber);
  font-weight: 600;
}
</style>
