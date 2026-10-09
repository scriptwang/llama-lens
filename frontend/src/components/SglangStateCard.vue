<template>
  <div class="state-card glass" :class="cardClass">
    <div class="head">
      <span class="phase" :class="phaseClass">{{ phaseText }}</span>
      <span v-if="spec.algorithm" class="badge ok small">{{ spec.algorithm }}</span>
      <span v-else class="badge dim small">API 实时</span>
    </div>

    <template v-if="online">
      <div class="stat3">
        <div class="stat">
          <span class="s-label">运行中请求</span>
          <span class="s-val mono cyan">{{ numText(running) }}</span>
          <span class="s-unit">/ {{ numText(maxRunning) }}</span>
        </div>
        <div class="stat">
          <span class="s-label">排队请求</span>
          <span class="s-val mono" :class="queueClass">{{ numText(waiting) }}</span>
          <span class="s-unit">个</span>
        </div>
        <div class="stat">
          <span class="s-label">生成速度</span>
          <span class="s-val mono cyan">{{ genText }}</span>
          <span class="s-unit">t/s</span>
        </div>
      </div>

      <div class="progress-wrap">
        <div class="bar"><i :class="barClass" :style="{ width: (kv.pct || 0) + '%' }"></i></div>
        <span class="mono small dim">{{ kvText }}</span>
      </div>

      <div class="kv-grid">
        <div class="kv">
          <span class="k">缓存命中率</span><span class="v mono">{{ cacheHitText }}</span>
        </div>
        <div class="kv">
          <span class="k">投机接受长度</span><span class="v mono">{{ acceptText }}</span>
        </div>
        <div class="kv">
          <span class="k">每轮 Draft</span><span class="v mono">{{ numText(spec.num_draft_tokens) }}</span>
        </div>
        <div class="kv">
          <span class="k">调度占用率</span><span class="v mono">{{ utilText }}</span>
        </div>
      </div>

      <div class="kv" v-if="spec.draft_model">
        <span class="k">Draft 模型</span><span class="v mono">{{ spec.draft_model }}</span>
      </div>

      <div class="kv-grid" v-if="memoryRows.length">
        <div class="kv" v-for="row in memoryRows" :key="row[0]">
          <span class="k">{{ row[0] }}</span><span class="v mono">{{ row[1] }}</span>
        </div>
      </div>

      <div class="cfg-strip" v-if="cfgRows.length">
        <span v-for="r in cfgRows" :key="r[0]" class="cfg">
          <span class="cfg-k">{{ r[0] }}</span><span class="cfg-v mono">{{ r[1] }}</span>
        </span>
      </div>
    </template>

    <div v-else class="placeholder"><span class="icon">⌁</span>SGLang 离线，暂无服务数据</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtNum } from '../utils'

const props = defineProps({
  engine: { type: Object, default: () => ({}) },
  online: { type: Boolean, default: false }
})

const e = computed(() => props.engine || {})
const spec = computed(() => e.value.speculative || {})
const req = computed(() => e.value.requests || {})
const kv = computed(() => e.value.kv || {})
const mem = computed(() => e.value.memory || {})
const server = computed(() => e.value.server || {})

const numText = (v) => (v === null || v === undefined ? '—' : fmtNum(v, 0))

const running = computed(() => req.value.running)
const waiting = computed(() => req.value.waiting)
const maxRunning = computed(() => req.value.max_running)

const queueClass = computed(() => {
  const w = waiting.value
  if (w === null || w === undefined) return ''
  if (w >= 16) return 'lv-danger'
  if (w >= 8) return 'lv-warn'
  return ''
})

const genText = computed(() => {
  const g = e.value.gen_speed_tps
  return g === null || g === undefined ? '—' : Number(g).toFixed(1)
})

const kvText = computed(() => {
  const k = kv.value
  if (k.used === null || k.used === undefined || !k.total) return '—'
  return `${fmtNum(k.used)} / ${fmtNum(k.total)} (${k.pct}%) · 含 Radix 缓存`
})
const barClass = computed(() => {
  const p = kv.value.pct
  if (p === null || p === undefined) return ''
  if (p >= 90) return 'danger'
  if (p >= 80) return 'warn'
  return 'green'
})

const cacheHit = computed(() => {
  const v = e.value.cache_hit_rate
  return v === null || v === undefined ? null : v
})
const cacheHitText = computed(() => {
  const v = cacheHit.value
  if (v === null) return '—'
  return (v <= 1 ? v * 100 : v).toFixed(1) + '%'
})
const acceptText = computed(() => {
  const a = spec.value.accept_length
  return a === null || a === undefined ? '—' : `${a.toFixed(2)} tok/step`
})
const utilText = computed(() => {
  const v = e.value.utilization
  if (v === null || v === undefined) return '—'
  if (v === 0) return '—'   // 上游 bug：未开 metrics 时 utilization 恒 0
  return `${(v <= 1 ? v * 100 : v).toFixed(1)}%`
})

const memoryRows = computed(() => {
  const out = []
  if (mem.value.weight_gb != null) out.push(['Weight 显存', `${mem.value.weight_gb} GB`])
  if (mem.value.kv_cache_gb != null) out.push(['KV Cache 显存', `${mem.value.kv_cache_gb} GB`])
  if (mem.value.graph_gb != null) out.push(['CUDA Graph', `${mem.value.graph_gb} GB`])
  if (mem.value.token_capacity != null) out.push(['Token 容量', fmtNum(mem.value.token_capacity)])
  return out
})

const cfgRows = computed(() => {
  const s = server.value
  const out = []
  if (s.tp_size) out.push(['TP', s.tp_size])
  if (s.dp_size) out.push(['DP', s.dp_size])
  if (s.max_running_requests) out.push(['最大并发', s.max_running_requests])
  if (s.max_total_num_tokens) out.push(['Token 容量', fmtNum(s.max_total_num_tokens)])
  if (s.mem_fraction_static != null) out.push(['mem_fraction', s.mem_fraction_static])
  return out
})

const phaseText = computed(() => (props.online ? 'Serving' : 'SGLang 离线'))
const phaseClass = computed(() => (props.online ? 'lv-cyan' : 'lv-danger'))
const cardClass = computed(() => (props.online ? 'active' : ''))
</script>

<style scoped>
.state-card { padding: 12px 16px; display: flex; flex-direction: column; gap: 4px; overflow-y: auto; }
.state-card.active { border-color: rgba(0, 229, 255, 0.35); box-shadow: 0 0 12px rgba(0, 229, 255, 0.15); }
.head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.phase { font-size: 16px; font-weight: 700; }
.lv-cyan { color: var(--cyan); text-shadow: 0 0 10px rgba(0, 229, 255, 0.5); }
.lv-danger { color: var(--red); }
.progress-wrap { display: flex; align-items: center; gap: 8px; padding: 4px 0; }
.progress-wrap .bar { flex: 1; }
.stat3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; padding: 4px 0; }
.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 8px 10px;
  background: rgba(143, 163, 200, 0.06);
  border: 1px solid rgba(143, 163, 200, 0.12);
  border-radius: 8px;
}
.s-label { font-size: 10px; color: var(--text-faint); letter-spacing: 1px; }
.s-val { font-size: 18px; font-weight: 700; line-height: 1.15; }
.s-val.cyan { color: var(--cyan); text-shadow: 0 0 10px rgba(0, 229, 255, 0.4); }
.s-unit { font-size: 10px; color: var(--text-faint); }
.kv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 18px; }
.cfg-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(143, 163, 200, 0.12);
}
.cfg { display: inline-flex; gap: 6px; font-size: 10px; }
.cfg-k { color: var(--text-faint); }
.cfg-v { color: var(--text-dim); }
</style>
