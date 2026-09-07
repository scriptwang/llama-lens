<template>
  <div class="strip">
    <div class="cell">
      <div class="cell-head">
        <span class="cell-icon"><el-icon><Grid /></el-icon></span>
        <span class="cell-label">匹配服务</span>
        <span class="cell-value">{{ services.length }}</span>
      </div>
      <div class="cell-sub" :title="hostLabel">{{ hostLabel || '—' }}</div>
    </div>
    <div class="cell">
      <div class="cell-head">
        <span class="cell-icon running"><el-icon><VideoPlay /></el-icon></span>
        <span class="cell-label">运行中</span>
        <span class="cell-value">{{ svcStats.running }}</span>
      </div>
      <div class="cell-sub">active</div>
    </div>
    <div class="cell">
      <div class="cell-head">
        <span class="cell-icon stopped"><el-icon><VideoPause /></el-icon></span>
        <span class="cell-label">已停止</span>
        <span class="cell-value">{{ svcStats.stopped }}</span>
      </div>
      <div class="cell-sub">inactive</div>
    </div>
    <div class="cell">
      <div class="cell-head">
        <span class="cell-icon failed"><el-icon><Warning /></el-icon></span>
        <span class="cell-label">异常</span>
        <span class="cell-value">{{ svcStats.failed }}</span>
      </div>
      <div class="cell-sub">failed</div>
    </div>

    <span class="cell-sep" />

    <el-tooltip
      v-for="card in cards"
      :key="card.key"
      placement="bottom"
      :show-after="250"
      :disabled="!card.rows"
    >
      <template v-if="card.rows" #content>
        <div class="gpu-tip">
          <div v-for="r in card.rows" :key="r.label" class="gpu-tip-row">
            <span class="gpu-tip-label">{{ r.label }}</span>
            <span class="gpu-tip-value">{{ r.value }}</span>
          </div>
        </div>
      </template>
      <div class="cell">
        <div class="cell-head">
          <span class="cell-icon"><el-icon><component :is="card.icon" /></el-icon></span>
          <span class="cell-label">{{ card.label }}</span>
          <span class="cell-value" :class="{ placeholder: card.value == null }">
            <template v-if="card.value != null">{{ card.value }}<small v-if="card.unit">{{ card.unit }}</small></template>
            <template v-else>—</template>
          </span>
        </div>
        <div v-if="card.bar != null && !card.rows" class="cell-bar">
          <div class="cell-bar-fill" :class="card.barColor" :style="{ width: card.bar + '%' }" />
        </div>
        <div class="cell-sub" :title="card.sub">{{ card.sub }}</div>
      </div>
    </el-tooltip>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  data: { type: Object, default: null },
  error: Boolean,
  services: { type: Array, default: () => [] },
  hostLabel: { type: String, default: '' },
})

const svcStats = computed(() => ({
  running: props.services.filter((s) => s.active_state === 'active').length,
  stopped: props.services.filter((s) => s.active_state === 'inactive').length,
  failed: props.services.filter((s) => s.active_state === 'failed').length,
}))

function level(pct, warn = 60, danger = 85) {
  if (pct == null) return 'ok'
  if (pct >= danger) return 'danger'
  if (pct >= warn) return 'warn'
  return 'ok'
}

function gb(mb) {
  return mb == null ? null : (mb / 1024).toFixed(1)
}

function avg(nums) {
  const v = nums.filter((x) => x != null)
  if (!v.length) return null
  return Math.round((v.reduce((a, b) => a + b, 0) / v.length) * 10) / 10
}

const cards = computed(() => {
  const d = props.data
  const noData = !d
  const errText = props.error && noData ? '获取失败，重试中' : noData ? '采集中…' : ''
  const gpus = d?.gpu?.devices || []
  const gpuSub = noData
    ? errText
    : gpus.length
      ? gpus.length === 1
        ? gpus[0].name
        : `${gpus.length} 块 GPU`
      : d?.gpu?.vendor
        ? 'GPU 无数据'
        : '未检测到 GPU 工具'

  const cpuPct = d?.cpu?.percent
  const mem = d?.memory
  const memPct = mem?.percent
  const memTotalGb = gb(mem?.total_mb)
  const memUsedGb = gb(mem?.used_mb)

  const utilVals = gpus.map((g) => g.util_percent)
  const utilAvg = avg(utilVals)
  const memUsedVals = gpus.map((g) => g.mem_used_mb)
  const memTotalVals = gpus.map((g) => g.mem_total_mb)
  const vramAvg = avg(gpus.map((g) => (g.mem_used_mb != null && g.mem_total_mb ? (g.mem_used_mb / g.mem_total_mb) * 100 : null)))
  const powerVals = gpus.map((g) => g.power_w)
  const powerAvg = avg(powerVals)
  const tempVals = gpus.map((g) => g.temp_c)
  const tempAvg = avg(tempVals)

  const gpuRows = gpus.length > 1
    ? gpus.map((g) => ({
        label: `GPU${g.index}`,
        value: g.util_percent != null ? `${Math.round(g.util_percent)}%` : '—',
        bar: g.util_percent ?? 0,
        barColor: level(g.util_percent),
      }))
    : null
  const vramRows = gpus.length > 1
    ? gpus.map((g) => {
        const pct = g.mem_used_mb != null && g.mem_total_mb ? (g.mem_used_mb / g.mem_total_mb) * 100 : null
        return {
          label: `GPU${g.index}`,
          value: g.mem_used_mb != null ? `${gb(g.mem_used_mb)} / ${gb(g.mem_total_mb)} GB` : '—',
          bar: pct ?? 0,
          barColor: level(pct),
        }
      })
    : null
  const powerRows = gpus.length > 1
    ? gpus.map((g) => {
        const pct = g.power_w != null && g.power_limit_w ? (g.power_w / g.power_limit_w) * 100 : null
        return {
          label: `GPU${g.index}`,
          value: g.power_w != null ? `${g.power_w.toFixed(1)} W` : '—',
          bar: pct ?? 0,
          barColor: level(pct),
        }
      })
    : null
  const tempRows = gpus.length > 1
    ? gpus.map((g) => ({
        label: `GPU${g.index}`,
        value: g.temp_c != null ? `${Math.round(g.temp_c)}°C` : '—',
        bar: g.temp_c != null ? Math.min(100, (g.temp_c / 90) * 100) : 0,
        barColor: level(g.temp_c, 70, 85),
      }))
    : null

  const g = gpus[0]
  const singleVramPct = g?.mem_used_mb != null && g?.mem_total_mb ? (g.mem_used_mb / g.mem_total_mb) * 100 : null
  const singlePowerPct = g?.power_w != null && g?.power_limit_w ? (g.power_w / g.power_limit_w) * 100 : null

  return [
    {
      key: 'cpu',
      icon: 'Cpu',
      label: 'CPU',
      value: cpuPct != null ? Math.round(cpuPct * 10) / 10 : null,
      unit: '%',
      bar: cpuPct,
      barColor: level(cpuPct),
      sub: noData
        ? errText
        : [d.cpu.cores ? `${d.cpu.cores} 核` : null, d.cpu.load?.[0] != null ? `负载 ${d.cpu.load[0].toFixed(2)}` : null]
            .filter(Boolean)
            .join(' · ') || '—',
      rows: null,
    },
    {
      key: 'memory',
      icon: 'Odometer',
      label: '内存',
      value: memPct != null ? Math.round(memPct * 10) / 10 : null,
      unit: '%',
      bar: memPct,
      barColor: level(memPct),
      sub: noData ? errText : memTotalGb != null ? `${memUsedGb} / ${memTotalGb} GB` : '—',
      rows: null,
    },
    {
      key: 'gpu',
      icon: 'Monitor',
      label: 'GPU 利用率',
      value: utilAvg,
      unit: '%',
      bar: gpus.length === 1 ? g?.util_percent ?? null : null,
      barColor: level(utilAvg),
      sub: gpuSub,
      rows: gpuRows,
    },
    {
      key: 'vram',
      icon: 'DataLine',
      label: '显存',
      value: vramAvg != null ? Math.round(vramAvg) : null,
      unit: '%',
      bar: gpus.length === 1 ? singleVramPct : null,
      barColor: level(vramAvg),
      sub: noData
        ? errText
        : gpus.length === 1
          ? g?.mem_used_mb != null
            ? `${gb(g.mem_used_mb)} / ${gb(g.mem_total_mb)} GB`
            : gpuSub
          : gpus.length
            ? `${gb(avg(memUsedVals))} / ${gb(avg(memTotalVals))} GB（均值）`
            : gpuSub,
      rows: vramRows,
    },
    {
      key: 'power',
      icon: 'Lightning',
      label: '功耗',
      value: powerAvg != null ? powerAvg.toFixed(1) : null,
      unit: 'W',
      bar: gpus.length === 1 ? singlePowerPct : null,
      barColor: level(singlePowerPct),
      sub: noData
        ? errText
        : gpus.length === 1
          ? g?.power_limit_w
            ? `上限 ${g.power_limit_w.toFixed(0)} W`
            : gpuSub
          : gpus.length
            ? '均值'
            : gpuSub,
      rows: powerRows,
    },
    {
      key: 'temp',
      icon: 'Thermometer',
      label: '温度',
      value: tempAvg != null ? Math.round(tempAvg) : null,
      unit: '°C',
      bar: gpus.length === 1 ? (g?.temp_c != null ? Math.min(100, (g.temp_c / 90) * 100) : null) : null,
      barColor: level(tempAvg, 70, 85),
      sub: gpuSub,
      rows: tempRows,
    },
  ]
})
</script>

<style scoped>
.strip {
  display: flex; align-items: stretch; flex-wrap: wrap;
  border-radius: var(--lc-radius);
  background: var(--lc-surface);
  backdrop-filter: var(--lc-glass-blur);
  -webkit-backdrop-filter: var(--lc-glass-blur);
  border: 1px solid var(--lc-border);
  box-shadow: var(--lc-shadow);
  padding: 6px;
  margin-bottom: 16px;
}
.cell {
  flex: 1 1 118px;
  min-width: 112px;
  padding: 8px 12px;
  display: flex; flex-direction: column; justify-content: center; gap: 5px;
  border-radius: 10px;
  transition: background 0.15s ease;
  min-width: 0;
}
.cell:hover { background: color-mix(in srgb, var(--lc-primary) 6%, transparent); }
.cell-sep { width: 1px; background: var(--lc-border); margin: 8px 2px; flex: none; }
.cell-head { display: flex; align-items: center; gap: 7px; min-width: 0; }
.cell-icon {
  width: 24px; height: 24px; border-radius: 7px; flex: none;
  display: flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--lc-primary) 14%, transparent); color: var(--lc-primary); font-size: 13px;
}
.cell-icon.running { background: color-mix(in srgb, var(--lc-success) 14%, transparent); color: var(--lc-success); }
.cell-icon.stopped { background: color-mix(in srgb, var(--lc-info) 16%, transparent); color: var(--lc-info); }
.cell-icon.failed { background: color-mix(in srgb, var(--lc-danger) 14%, transparent); color: var(--lc-danger); }
.cell-label { font-size: 12px; color: var(--lc-text-secondary); white-space: nowrap; }
.cell-value {
  margin-left: auto; font-size: 17px; font-weight: 700; line-height: 1;
  font-variant-numeric: tabular-nums; white-space: nowrap;
  min-width: 0; overflow: hidden; text-overflow: ellipsis;
}
.cell-value small { font-size: 11px; font-weight: 500; color: var(--lc-text-muted); margin-left: 1px; }
.cell-value.placeholder { color: var(--lc-text-muted); font-weight: 400; }
.cell-bar { height: 4px; border-radius: 2px; overflow: hidden; background: var(--lc-border); }
.cell-bar-fill { height: 100%; border-radius: 2px; transition: width 0.6s ease; }
.cell-bar-fill.ok { background: var(--lc-success); }
.cell-bar-fill.warn { background: var(--lc-warning); }
.cell-bar-fill.danger { background: var(--lc-danger); }
.cell-sub {
  font-size: 11px; color: var(--lc-text-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.gpu-tip { display: flex; flex-direction: column; gap: 4px; }
.gpu-tip-row { display: flex; justify-content: space-between; gap: 16px; font-size: 12px; }
.gpu-tip-label { color: var(--el-text-color-secondary); }
.gpu-tip-value { font-variant-numeric: tabular-nums; }
</style>
