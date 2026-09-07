<template>
  <header class="topbar">
    <router-link to="/" class="back">← 门户</router-link>
    <div class="host-info">
      <span class="dot" :class="dotClass"></span>
      <span class="hostname" :title="hostName">{{ hostName }}</span>
      <span v-if="modelName" class="model mono dim" :title="modelTitle">· {{ modelName }}</span>
    </div>

    <div v-if="stats" class="stats">
      <span class="stat" :class="genLevel" :title="genTip">
        <span class="k">速度</span><span class="v mono">{{ genText }}</span>
      </span>
      <span class="stat" :class="mtpLevel" :title="mtpTip">
        <span class="k">MTP</span><span class="v mono">{{ mtpText }}</span>
      </span>
      <span class="stat" :class="ctxLevel" :title="ctxTip">
        <span class="k">上下文</span><span class="v mono">{{ ctxText }}</span>
      </span>
      <span v-for="g in gpuChips" :key="g.idx" class="stat" :class="g.level" :title="`GPU${g.idx} 利用率 / 温度 / 功耗 / 显存已用 / 总量`">
        <span class="k">GPU{{ g.idx }}</span><span class="v mono">{{ g.text }}</span>
      </span>
      <span class="stat" :class="memLevel" :title="memTip">
        <span class="k">内存</span><span class="v mono">{{ memText }}</span>
      </span>
      <span class="stat" :class="cpuLevel" title="CPU 利用率">
        <span class="k">CPU</span><span class="v mono">{{ cpuText }}</span>
      </span>
    </div>

    <div class="right">
      <button class="diag-btn" title="聚合系统/GPU/服务/API/事件/错误日志，生成诊断报告" @click="diagVisible = true">
        <el-icon style="margin-right:3px"><FirstAidKit /></el-icon>体检
      </button>
      <ThemeSwitcher />
      <AccountMenu />
      <span v-if="!llamaOnline" class="badge danger">llama 离线</span>
      <span v-else-if="!sshOk" class="badge warn">SSH 断开</span>
      <span v-else class="badge ok">在线</span>
      <LiveClock />

      <span class="mode-indicator" :class="modeDotClass" title="数据通道"></span>
      <select :value="mode" class="mode-select mono" @change="onModeChange">
        <option value="ws">实时 (WS)</option>
        <option value="1s">1s</option>
        <option value="2s">2s</option>
        <option value="5s">5s</option>
        <option value="paused">暂停</option>
      </select>
      <span v-if="degraded" class="badge warn small">WS 断线 · 轮询中</span>
    </div>

    <DiagnosticDialog v-model="diagVisible" :host-id="hostId" :host-label="hostName" />
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { fmtTokens, fmtGB, alertLevel } from '../utils'
import LiveClock from './LiveClock.vue'
import ThemeSwitcher from './ThemeSwitcher.vue'
import AccountMenu from './AccountMenu.vue'
import DiagnosticDialog from './DiagnosticDialog.vue'
import { ref } from 'vue'

const props = defineProps({
  hostId: { type: String, default: '' },
  hostName: { type: String, default: '' },
  modelName: { type: String, default: '' },
  modelTitle: { type: String, default: '' },
  llamaOnline: { type: Boolean, default: false },
  sshOk: { type: Boolean, default: false },
  stats: { type: Object, default: null },
  mode: { type: String, default: 'ws' },
  degraded: { type: Boolean, default: false },
  connected: { type: Boolean, default: false }
})
const emit = defineEmits(['update:mode'])

const diagVisible = ref(false)

const st = computed(() => props.stats || {})
const alerts = computed(() => st.value.alerts || [])
const num = (v) => (v === null || v === undefined || Number.isNaN(v) ? null : v)

const genText = computed(() => {
  const s = st.value
  if (!s.online) return '—'
  const p = num(s.prompt)
  const g = num(s.gen)
  const v = p > 0 ? p : g
  return v === null || v <= 0 ? '—' : `${v.toFixed(1)} t/s`
})
const genTip = computed(() => {
  const s = st.value
  if (!s.online) return 'llama offline'
  return `gen ${num(s.gen) || 0} t/s · prompt ${num(s.prompt) || 0} t/s · 来源 ${s.speedSource || '—'}`
})
const genLevel = computed(() => (st.value.online ? 'normal' : 'off'))

const mtpText = computed(() => {
  const v = num(st.value.mtp)
  return v === null ? '—' : `${v.toFixed(1)}%`
})
const mtpTip = computed(() => (num(st.value.mtp) === null ? '等待任务结束' : 'MTP draft 接受率'))
const mtpLevel = computed(() => (num(st.value.mtp) === null ? 'off' : alertLevel(alerts.value, 'mtp')))

const ctxText = computed(() => {
  const s = st.value
  const r = num(s.ctxRemain)
  return r === null ? '—' : `${fmtTokens(r)}/${fmtTokens(s.ctxTotal)}`
})
const ctxTip = computed(() => {
  const s = st.value
  const r = num(s.ctxRemain)
  if (r === null) return '等待任务结束'
  return `剩余 ${fmtTokens(r)} · 已用 ${fmtTokens(s.ctxUsed)} (${num(s.ctxPct) === null ? '—' : s.ctxPct}%)`
})
const ctxLevel = computed(() => (num(st.value.ctxRemain) === null ? 'off' : alertLevel(alerts.value, 'ctx')))

const gpuChips = computed(() =>
  (st.value.gpus || []).map((g) => ({
    idx: g.idx,
    level: alertLevel(alerts.value, `gpu${g.idx}.`),
    text: [
      num(g.util) === null ? '—' : `${Math.round(g.util)}%`,
      num(g.temp) === null ? '—' : `${Math.round(g.temp)}°`,
      num(g.power) === null ? '—' : `${Math.round(g.power)}W`,
      num(g.memUsed) === null || !g.memTotal ? '—' : `${(g.memUsed / 1024).toFixed(1)}/${(g.memTotal / 1024).toFixed(0)}G`
    ].join(' ')
  }))
)

const memText = computed(() => {
  const s = st.value
  const u = num(s.memUsed)
  return u === null || !s.memTotal ? '—' : `${(u / 1024).toFixed(1)}/${(s.memTotal / 1024).toFixed(1)}G`
})
const memTip = computed(() => {
  const s = st.value
  const u = num(s.memUsed)
  return u === null || !s.memTotal ? '数据不可用（SSH 断开）' : `内存已用 ${fmtGB(u)} / 总量 ${fmtGB(s.memTotal)}`
})
const memLevel = computed(() => (num(st.value.memUsed) === null ? 'off' : alertLevel(alerts.value, 'mem')))

const cpuText = computed(() => {
  const v = num(st.value.cpu)
  return v === null ? '—' : `${Math.round(v)}%`
})
const cpuLevel = computed(() => (num(st.value.cpu) === null ? 'off' : alertLevel(alerts.value, 'cpu')))

const dotClass = computed(() => {
  if (props.llamaOnline && props.sshOk) return 'online'
  if (props.sshOk) return 'warn'
  return 'offline'
})
const modeDotClass = computed(() => {
  if (props.mode === 'paused') return 'gray'
  if (props.mode === 'ws') return props.connected ? 'green' : 'amber'
  return 'amber'
})

function onModeChange(e) {
  emit('update:mode', e.target.value)
}
</script>

<style scoped>
.diag-btn {
  display: inline-flex;
  align-items: center;
  flex: none;
  white-space: nowrap;
  background: transparent;
  border: 1px solid var(--card-border);
  color: var(--text-dim);
  font-size: 12px;
  font-weight: 500;
  padding: 4px 12px;
  border-radius: 12px;
  cursor: pointer;
  transition: color .15s, border-color .15s, background .15s;
}
.diag-btn:hover {
  color: var(--cyan);
  border-color: rgba(0, 229, 255, 0.5);
  background: rgba(0, 229, 255, 0.08);
}
.topbar {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 0 24px;
  border-bottom: 1px solid rgba(0, 229, 255, 0.12);
  background: rgba(10, 14, 23, 0.6);
  backdrop-filter: blur(12px);
  position: sticky;
  top: var(--chrome-top, 0px);
  z-index: 20;
}
.back { color: var(--text-dim); font-size: 13px; flex: none; }
.back:hover { color: var(--cyan); text-decoration: none; }
.host-info { display: flex; align-items: center; gap: 10px; min-width: 0; }
.hostname { font-size: 15px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.model { color: var(--text-dim); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stats { display: flex; align-items: center; gap: 6px; min-width: 0; overflow-x: auto; scrollbar-width: none; }
.stats::-webkit-scrollbar { display: none; }
.stat {
  flex: none;
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 3px 9px;
  border: 1px solid var(--card-border);
  border-radius: 6px;
  background: rgba(16, 24, 40, 0.55);
  white-space: nowrap;
}
.stat .k { font-size: 10px; color: var(--text-faint); }
.stat .v { font-size: 12px; color: var(--text); }
.stat.warn .v { color: var(--amber); }
.stat.danger .v { color: var(--red); }
.stat.off .v { color: var(--text-faint); }
.right { margin-left: auto; display: flex; align-items: center; gap: 12px; flex: none; }
.mode-indicator { width: 8px; height: 8px; border-radius: 50%; }
.mode-indicator.green { background: var(--green); box-shadow: 0 0 6px var(--green); }
.mode-indicator.amber { background: var(--amber); box-shadow: 0 0 6px var(--amber); }
.mode-indicator.gray { background: var(--text-faint); }
.mode-select {
  background: rgba(16, 24, 40, 0.9);
  color: var(--text);
  border: 1px solid var(--card-border);
  border-radius: 6px;
  font-size: 12px;
  padding: 4px 8px;
  outline: none;
  cursor: pointer;
}
.mode-select:hover { border-color: var(--card-border-hover); }
</style>
