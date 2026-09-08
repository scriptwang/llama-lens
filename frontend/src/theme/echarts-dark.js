// 模块化引入：只注册实际用到的 line/gauge 图表与组件（全量包 ~1.2MB → 按需 ~400KB）。
// 本文件仅被 TrendChart / GaugeCard 动态 import()，故 echarts 整体成为懒加载 chunk，不进首屏 vendor。
import * as echarts from 'echarts/core'
import { LineChart, GaugeChart, BarChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { chartTheme } from './index'

echarts.use([
  LineChart,
  GaugeChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  CanvasRenderer,
])

const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

export function baseOption() {
  const t = chartTheme()
  const mono = t.glow ? {} : { fontFamily: MONO }
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 52, right: 14, top: 26, bottom: 22 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: t.tooltipBg,
      borderColor: t.tooltipBorder,
      borderWidth: 1,
      ...(t.glow ? {} : { borderRadius: 0 }),
      textStyle: { color: t.tooltipText, fontSize: 11, ...mono },
      axisPointer: { lineStyle: { color: t.axisPointer } }
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: t.axisLine } },
      axisLabel: {
        color: t.dim,
        fontSize: 10,
        ...mono,
        formatter: (v) => {
          const d = new Date(v)
          return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
        }
      },
      splitLine: { show: false }
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: t.dim, fontSize: 10, ...mono },
      splitLine: { lineStyle: { color: t.splitLine } }
    }
  }
}

export function lineSeries(name, data, color, opts = {}) {
  const t = chartTheme()
  const s = {
    name,
    type: 'line',
    data,
    smooth: opts.step ? false : 0.2,
    step: opts.step ? 'start' : false,
    symbol: 'none',
    connectNulls: !!opts.connectNulls,
    lineStyle: {
      width: 1.5,
      color,
      ...(t.glow ? { shadowBlur: 8, shadowColor: `${color}55` } : {})
    },
    itemStyle: { color },
    emphasis: { focus: 'series' }
  }
  if (opts.stack) s.stack = opts.stack
  if (opts.area) {
    s.areaStyle = t.glow
      ? {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: `${color}40` },
            { offset: 1, color: `${color}00` }
          ])
        }
      : { color: `${color}14` }
  }
  if (opts.yAxisIndex !== undefined) s.yAxisIndex = opts.yAxisIndex
  if (opts.markLine) s.markLine = opts.markLine
  return s
}

export function barSeries(name, data, color, opts = {}) {
  const t = chartTheme()
  return {
    name,
    type: 'bar',
    data,
    barMaxWidth: opts.barMaxWidth != null ? opts.barMaxWidth : 28,
    itemStyle: {
      color,
      borderRadius: t.glow ? [3, 3, 0, 0] : 0,
      ...(t.glow ? { shadowBlur: 6, shadowColor: `${color}44` } : {})
    },
    emphasis: { focus: 'series' }
  }
}

export function initChart(el) {
  return echarts.init(el, null, { renderer: 'canvas' })
}

export function gaugeOption(value, color, zones) {
  const t = chartTheme()
  const has = value !== null && value !== undefined && Number.isFinite(value)
  const v = has ? Math.max(0, Math.min(100, value)) : 0
  const zoneColors = zones || t.gaugeZones
  return {
    backgroundColor: 'transparent',
    animation: true,
    animationDuration: 300,
    animationDurationUpdate: 300,
    animationEasingUpdate: 'cubicOut',
    series: [
      {
        type: 'gauge',
        min: 0,
        max: 100,
        startAngle: 210,
        endAngle: -30,
        radius: '80%',
        center: ['50%', '58%'],
        splitNumber: 4,
        axisLine: {
          lineStyle: {
            width: 12,
            color: zoneColors
          }
        },
        pointer: {
          length: '58%',
          width: 3,
          itemStyle: {
            color,
            ...(t.glow ? { shadowBlur: 6, shadowColor: `${color}88` } : {})
          }
        },
        anchor: {
          show: true,
          size: 8,
          itemStyle: { color: t.gaugeAnchorBg, borderColor: color, borderWidth: 2 }
        },
        progress: {
          show: true,
          width: 12,
          roundCap: t.glow,
          itemStyle: {
            color,
            ...(t.glow ? { shadowBlur: 10, shadowColor: `${color}55` } : {})
          }
        },
        axisTick: {
          show: true,
          splitNumber: 5,
          distance: -14,
          length: 3,
          lineStyle: { color: t.gaugeTick, width: 1 }
        },
        splitLine: {
          show: true,
          distance: -15,
          length: 7,
          lineStyle: { color: t.gaugeSplit, width: 1 }
        },
        axisLabel: {
          show: true,
          distance: 10,
          color: t.faint,
          fontSize: 9,
          fontFamily: 'JetBrains Mono, Roboto Mono, monospace'
        },
        title: { show: false },
        detail: { show: false },
        data: [{ value: Math.round(v * 10) / 10 }]
      }
    ]
  }
}
