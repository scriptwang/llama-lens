<template>
  <router-link :to="`/host/${host.id}`" class="host-card glass glass-hover" :class="cardClass">
    <div class="top">
      <span class="dot" :class="dotClass"></span>
      <span class="hostname">{{ host.name }}</span>
      <span class="hid mono faint">{{ host.id }}</span>
      <span v-if="host.alerts_count > 0" class="alert-badge mono">{{ host.alerts_count }}</span>
    </div>

    <div class="model">
      <span class="model-name" :class="{ muted: !host.online }">{{ host.model_name || '未加载模型' }}</span>
      <span v-if="host.n_params" class="model-params mono dim">{{ fmtParams(host.n_params) }} 参数</span>
    </div>

    <div class="speed" :class="{ muted: !host.online }">
      <div class="speed-num">
        <span class="mono big" :class="speedLevel">{{ speedText }}</span>
        <span class="unit">tok/s</span>
      </div>
      <svg v-if="sparkD" class="spark" :viewBox="`0 0 ${sparkW} ${sparkH}`" preserveAspectRatio="none">
        <path :d="sparkD" fill="none" :stroke="sparkColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
      </svg>
    </div>

    <div class="gpus">
      <div v-for="g in host.gpus" :key="g.index" class="gpu-row">
        <span class="gpu-label mono">GPU{{ g.index }}</span>
        <div class="bar"><i :class="barClass(g.util_pct)" :style="{ width: (g.util_pct || 0) + '%' }"></i></div>
        <span class="gpu-val mono" :class="valClass(g.util_pct)">{{ g.util_pct === null ? '—' : Math.round(g.util_pct) + '%' }}</span>
        <span class="gpu-mem mono faint">{{ g.mem_pct === null ? '' : '显存 ' + Math.round(g.mem_pct) + '%' }}</span>
      </div>
      <div v-if="!host.gpus.length" class="faint small">无 GPU 数据</div>
    </div>

    <div class="bottom mono">
      <span>CPU <b :class="valClass(host.cpu_pct)">{{ host.cpu_pct === null ? '—' : Math.round(host.cpu_pct) + '%' }}</b></span>
      <span>MEM <b :class="valClass(host.mem_pct)">{{ host.mem_pct === null ? '—' : Math.round(host.mem_pct) + '%' }}</b></span>
      <span v-if="tokensPerDay !== null" class="eff" title="近 24h Token 产出">⚡ {{ fmtTokens(tokensPerDay) }}/天</span>
      <span v-if="!host.ssh_ok" class="lv-warn">SSH 断开</span>
      <span v-if="!host.online" class="lv-danger">llama 离线</span>
    </div>
  </router-link>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { fmtParams, fmtTokens, sparkPath, useCountUp, alertLevel } from '../utils'

const props = defineProps({
  host: { type: Object, required: true }
})

// 近 24h Token 产出（P1-2，静默拉取，失败不显示）
const tokensPerDay = ref(null)
onMounted(async () => {
  try {
    const token = localStorage.getItem('llama_token')
    const resp = await fetch(`/api/efficiency?host_id=${encodeURIComponent(props.host.id)}&range=24h`, {
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
    })
    if (resp.ok) {
      const d = await resp.json()
      if (d.available) tokensPerDay.value = d.tokens_per_day
    }
  } catch (e) { /* 忽略 */ }
})

const speed = useCountUp(computed(() => (props.host.online ? props.host.gen_speed_tps || 0 : 0)))
const speedText = computed(() => (speed.value === null || speed.value === undefined ? '—' : Number(speed.value).toFixed(1)))

const sparkW = 130
const sparkH = 30
const sparkD = computed(() => sparkPath(props.host.speed_spark || [], sparkW, sparkH))
const sparkColor = computed(() => (props.host.online ? 'var(--cyan)' : 'var(--text-faint)'))

const cardLevel = computed(() => alertLevel(props.host.alerts, ''))
const cardClass = computed(() => (cardLevel.value === 'danger' ? 'card-danger' : cardLevel.value === 'warn' ? 'card-warn' : ''))
const dotClass = computed(() => {
  if (props.host.online) return 'online'
  if (props.host.ssh_ok) return 'warn'
  return 'offline'
})
const speedLevel = computed(() => (props.host.online ? '' : 'muted'))

function barClass(v) {
  if (v === null) return ''
  if (v >= 90) return 'danger'
  if (v >= 80) return 'warn'
  return ''
}
function valClass(v) {
  if (v === null) return ''
  if (v >= 90) return 'lv-danger'
  if (v >= 80) return 'lv-warn'
  return ''
}
</script>

<style scoped>
.host-card {
  display: block;
  padding: 16px 18px;
  color: var(--text);
  text-decoration: none;
  position: relative;
}
.host-card:hover { text-decoration: none; }
.top { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.hostname { font-size: 15px; font-weight: 600; }
.hid { font-size: 11px; }
.alert-badge {
  margin-left: auto;
  background: var(--red);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  min-width: 18px;
  height: 18px;
  border-radius: 9px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 5px;
  box-shadow: 0 0 10px rgba(255, 59, 92, 0.6);
}
.model { display: flex; align-items: baseline; gap: 8px; margin-bottom: 12px; min-height: 18px; }
.model-name { font-size: 13px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.model-params { font-size: 11px; flex: none; }
.speed {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.speed-num { display: flex; align-items: baseline; gap: 6px; }
.big { font-size: 32px; font-weight: 700; color: var(--cyan); text-shadow: 0 0 14px rgba(0, 229, 255, 0.45); line-height: 1; }
.unit { color: var(--text-dim); font-size: 11px; }
.muted { opacity: 0.45; }
.spark { width: 130px; height: 30px; flex: none; }
.gpus { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.gpu-row { display: flex; align-items: center; gap: 8px; }
.gpu-label { width: 38px; font-size: 11px; color: var(--text-dim); flex: none; }
.gpu-row .bar { flex: 1; }
.gpu-val { width: 38px; text-align: right; font-size: 12px; flex: none; }
.gpu-mem { width: 64px; text-align: right; font-size: 10px; flex: none; }
.bottom { display: flex; gap: 16px; font-size: 12px; color: var(--text-dim); flex-wrap: wrap; }
.bottom b { color: var(--text); font-weight: 600; }
.bottom .eff { color: var(--cyan); }
</style>
