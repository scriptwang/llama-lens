<template>
  <div class="gcard glass" :class="levelClass">
    <div class="gcard-head">
      <span class="gcard-title">{{ title }}</span>
      <span v-if="sub" class="gcard-sub mono">{{ sub }}</span>
    </div>
    <div class="gcard-body">
      <div ref="el" class="gcard-chart"></div>
      <div class="gcard-center">
        <span class="mono val" :class="levelClass" :style="zoneColors ? { color: color } : null">{{ text }}</span>
        <span v-if="unit" class="unit">{{ unit }}</span>
      </div>
    </div>
    <svg v-if="sparkD" class="gcard-spark" :viewBox="`0 0 ${sparkW} ${sparkH}`" preserveAspectRatio="none">
      <path :d="sparkD" fill="none" :stroke="sparkColor" stroke-width="1.2" vector-effect="non-scaling-stroke" />
    </svg>
    <div v-if="footText" class="gcard-foot mono">{{ footText }}</div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { chartTheme, themeState } from '../theme'
import { sparkPath, useCountUp } from '../utils'

const props = defineProps({
  title: { type: String, required: true },
  value: { type: Number, default: null },
  unit: { type: String, default: '%' },
  digits: { type: Number, default: 1 },
  level: { type: String, default: 'normal' }, // normal | warn | danger
  sub: { type: String, default: '' },
  spark: { type: Array, default: () => [] }, // [[ts, value], ...] 60s
  foot: { type: String, default: '' }, // 自定义底行；缺省用 60s 峰/谷/均
  zones: { type: Array, default: null }, // 阈值色带 [[frac, color], ...]
  zoneColors: { type: Array, default: null } // 值驱动颜色 [[frac, 饱和色], ...]：value 落在哪个区间用哪个色
})

const el = ref(null)
let chart = null
let ro = null

const color = computed(() => {
  const t = chartTheme()
  if (props.value === null || props.value === undefined) return t.faint
  if (props.zoneColors && props.zoneColors.length) {
    const v = props.value / 100
    for (const [frac, c] of props.zoneColors) {
      if (v < frac) return c
    }
    return props.zoneColors[props.zoneColors.length - 1][1]
  }
  if (props.level === 'danger') return t.red
  if (props.level === 'warn') return t.amber
  return t.cyan
})

const animated = useCountUp(computed(() => (typeof props.value === 'number' ? props.value : 0)))
const text = computed(() => {
  if (props.value === null || props.value === undefined) return '—'
  const n = Number(animated.value)
  return Number.isFinite(n) ? n.toFixed(props.digits) : '—'
})

const levelClass = computed(() => (props.level === 'danger' ? 'lv-danger' : props.level === 'warn' ? 'lv-warn' : ''))

const sparkW = 150
const sparkH = 20
const sparkD = computed(() => sparkPath(props.spark || [], sparkW, sparkH))
const sparkColor = computed(() => (props.value === null || props.value === undefined ? chartTheme().faint : color.value))

const statsText = computed(() => {
  const vals = (props.spark || []).map((p) => p[1]).filter((v) => v !== null && v !== undefined)
  if (!vals.length) return ''
  const max = Math.max(...vals)
  const min = Math.min(...vals)
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length
  return `60s 峰 ${max.toFixed(0)}${props.unit} · 谷 ${min.toFixed(0)}${props.unit} · 均 ${avg.toFixed(0)}${props.unit}`
})
const footText = computed(() => props.foot || statsText.value)

function render() {
  if (!chart) return
  chart.setOption(ed.gaugeOption(props.value, color.value, props.zones))
}

// echarts-dark 懒加载：echarts 不进首屏 vendor，仅在图表真正挂载时下载
let ed = null
onMounted(async () => {
  await nextTick()
  ed = await import('../theme/echarts-dark')
  if (!el.value) return // 加载期间组件已卸载
  chart = ed.initChart(el.value)
  render()
  ro = new ResizeObserver(() => chart && chart.resize())
  ro.observe(el.value)
})

watch(() => [props.value, props.level], render)
watch(() => themeState.version, render)

onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  if (chart) { chart.dispose(); chart = null }
})
</script>

<style scoped>
.gcard { padding: 10px 12px 8px; display: flex; flex-direction: column; min-width: 0; }
.gcard-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 0 4px; }
.gcard-title {
  font-size: 11px;
  color: var(--text-dim);
  letter-spacing: 1px;
  flex: none;
}
.gcard-sub {
  font-size: 10px;
  color: var(--text-faint);
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.gcard-body { position: relative; height: 118px; }
.gcard-chart { width: 100%; height: 100%; }
.gcard-center {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 4px;
  pointer-events: none;
}
.gcard-center .val { font-size: 20px; font-weight: 700; color: var(--text); line-height: 1; }
.lv-danger .val { text-shadow: 0 0 12px rgba(255, 59, 92, 0.5); }
.lv-warn .val { text-shadow: 0 0 12px rgba(255, 197, 61, 0.4); }
.gcard-center .unit { font-size: 10px; color: var(--text-faint); }
.gcard-spark { width: 100%; height: 20px; margin-top: 2px; }
.gcard-foot {
  font-size: 10px;
  color: var(--text-faint);
  text-align: center;
  padding: 2px 4px 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
