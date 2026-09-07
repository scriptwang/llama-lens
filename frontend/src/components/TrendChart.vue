<template>
  <div class="trend glass">
    <div class="trend-head">
      <span class="trend-title">{{ title }}</span>
      <span v-if="unit" class="trend-unit mono">{{ unit }}</span>
    </div>
    <div ref="el" class="trend-body" :style="{ height: height + 'px' }"></div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { baseOption, lineSeries, initChart } from '../theme/echarts-dark'
import { themeState, palette, chartTheme } from '../theme'

const props = defineProps({
  title: { type: String, required: true },
  unit: { type: String, default: '' },
  // series: [{ name, ts: [], values: [], color?, step?, area?, stack?, markLine? }]
  series: { type: Array, default: () => [] },
  height: { type: Number, default: 180 },
  yMax: { type: Number, default: null },
  yMin: { type: Number, default: null }
})

const el = ref(null)
let chart = null
let ro = null

function buildOption() {
  const opt = baseOption()
  // 按数据跨度自适应 x 轴时间格式：<1h 时分秒，1h~1d 时分，>=1d 带日期
  opt.xAxis.axisLabel.formatter = (v) => formatAxisTime(v, dataSpan())
  opt.series = props.series.map((s, i) => {
    const pal = palette()
    const color = s.color || pal[i % pal.length]
    const data = (s.ts || []).map((t, j) => [t * 1000, s.values[j] === null || s.values[j] === undefined ? null : s.values[j]])
    return lineSeries(s.name, data, color, {
      step: s.step,
      area: s.area,
      stack: s.stack,
      markLine: s.markLine
    })
  })
  if (props.yMax !== null) opt.yAxis.max = props.yMax
  if (props.yMin !== null) opt.yAxis.min = props.yMin
  if (props.series.length > 1) {
    opt.legend = {
      show: true,
      top: 0,
      right: 8,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { color: chartTheme().dim, fontSize: 10 }
    }
  }
  return opt
}

// 所有序列的时间跨度（秒）；无数据返回 0
function dataSpan() {
  let min = Infinity, max = -Infinity
  for (const s of props.series) {
    const ts = s.ts || []
    if (ts.length) {
      if (ts[0] < min) min = ts[0]
      if (ts[ts.length - 1] > max) max = ts[ts.length - 1]
    }
  }
  return max > min ? max - min : 0
}

function formatAxisTime(v, span) {
  const d = new Date(v)
  const p = (n) => String(n).padStart(2, '0')
  if (span >= 86400) return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes())
  if (span >= 3600) return p(d.getHours()) + ':' + p(d.getMinutes())
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds())
}

function render() {
  if (!chart) return
  chart.setOption(buildOption(), { replaceMerge: ['series'] })
}

onMounted(async () => {
  await nextTick()
  chart = initChart(el.value)
  render()
  ro = new ResizeObserver(() => chart && chart.resize())
  ro.observe(el.value)
})

watch(() => props.series, render, { deep: false })
watch(() => [props.yMax, props.yMin], render)
watch(() => themeState.version, render)

onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  if (chart) { chart.dispose(); chart = null }
})
</script>

<style scoped>
.trend { padding: 10px 12px 6px; min-width: 0; }
.trend-head { display: flex; align-items: baseline; justify-content: space-between; padding: 0 4px 4px; }
.trend-title { font-size: 12px; color: var(--text-dim); letter-spacing: 1px; }
.trend-unit { font-size: 10px; color: var(--text-faint); }
.trend-body { width: 100%; }
</style>
