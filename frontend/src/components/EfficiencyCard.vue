<template>
  <div class="eff glass">
    <div class="eff-head">
      <span class="eff-title">效率统计</span>
      <span v-if="rangeLabel" class="eff-range mono" title="统计范围与「历史趋势」共用时间选择">{{ rangeLabel }}</span>
    </div>

    <div v-if="!data" class="eff-empty">
      <span v-if="loading">加载中…</span>
      <span v-else-if="data === 'none'">历史存储未启用</span>
      <span v-else>暂无数据</span>
    </div>

    <template v-else>
      <div class="eff-stats">
        <div class="eff-stat">
          <div class="eff-num mono">{{ fmtTokens(data.tokens_total) }}</div>
          <div class="eff-label">Token 产出</div>
        </div>
        <div v-if="data.tokens_per_day != null" class="eff-stat">
          <div class="eff-num mono">{{ fmtTokens(data.tokens_per_day) }}</div>
          <div class="eff-label">日均 Token</div>
        </div>
        <div class="eff-stat">
          <div class="eff-num mono">{{ data.energy_kwh.toFixed(2) }} <small>kWh</small></div>
          <div class="eff-label">GPU 耗电</div>
        </div>
        <div class="eff-stat">
          <div class="eff-num mono">{{ data.tokens_per_wh === null ? '—' : fmtNum(data.tokens_per_wh) }}</div>
          <div class="eff-label">tokens / Wh</div>
        </div>
        <div v-if="data.cost !== null" class="eff-stat">
          <div class="eff-num mono">¥{{ data.cost }}</div>
          <div class="eff-label">电费（{{ data.electricity_price }} 元/kWh）</div>
        </div>
      </div>

      <div v-if="data.gpus.length" class="eff-gpus">
        <span v-for="g in data.gpus" :key="g.index" class="eff-gpu mono">
          {{ g.name || `GPU${g.index}` }} {{ g.kwh }} kWh
        </span>
      </div>

      <div class="eff-charts">
        <TrendChart title="Token 产出" :unit="bucketUnit" :series="tokenSeries" :height="150" />
        <TrendChart title="GPU 总功耗" unit="W" :series="powerSeries" :height="150" />
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { fmtNum, fmtTokens, anchorStartMs } from '../utils'
import TrendChart from './TrendChart.vue'

// 效率统计（P1-2）：token 产出 + GPU 耗电 + tokens/瓦。
// 时间范围与「历史趋势」共用（sel 由父组件传入），不再持有独立选择器：
//   sel = {type:'window',s} | {type:'anchor',a} | {type:'custom',start,end}
// 实时窗口（window/anchor）每 30s 轮询（效率数据变化慢，不必跟随趋势的 5s）；自定义只加载一次。
const props = defineProps({
  hostId: { type: String, required: true },
  sel: { type: Object, required: true },
})

const DAY = 86400
const data = ref(null)
const loading = ref(false)
const cur = ref(null)  // { s, e, span } 最近一次计算的范围
let timer = null
let reqSeq = 0

const isLive = computed(() => props.sel.type !== 'custom')

// 调用时计算范围（实时窗口终点=现在，避免缓存过期）
function computeRange() {
  const now = Math.floor(Date.now() / 1000)
  const sel = props.sel
  if (sel.type === 'custom') return [sel.start, sel.end]
  if (sel.type === 'anchor') return [Math.floor(anchorStartMs(sel.a) / 1000), now]
  return [now - sel.s, now]
}

function refreshCur() {
  const [s, e] = computeRange()
  cur.value = { s, e, span: e - s }
}

const spanSec = computed(() => (cur.value ? cur.value.span : 0))
const rangeLabel = computed(() => {
  if (!cur.value) return ''
  const f = (t) => {
    const d = new Date(t * 1000)
    const p = (n) => String(n).padStart(2, '0')
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  }
  const endTxt = isLive.value ? '现在' : f(cur.value.e)
  return `${f(cur.value.s)} ~ ${endTxt}`
})

const bucketUnit = computed(() => (spanSec.value <= DAY ? 'tokens/时' : 'tokens/天'))

const tokenSeries = computed(() => data.value
  ? [{ name: 'tokens', ts: data.value.series.tokens.ts, values: data.value.series.tokens.values, bar: true }]
  : [])
const powerSeries = computed(() => data.value
  ? [{ name: 'W', ts: data.value.series.power_w.ts, values: data.value.series.power_w.values, bar: true }]
  : [])

async function load() {
  refreshCur()
  const [s, e] = computeRange()
  const seq = ++reqSeq
  loading.value = true
  try {
    const token = localStorage.getItem('llama_token')
    const resp = await fetch(`/api/efficiency?host_id=${encodeURIComponent(props.hostId)}&start=${s}&end=${e}`, {
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
    })
    if (seq !== reqSeq) return  // 已有更新请求，丢弃过期响应
    if (resp.ok) {
      const d = await resp.json()
      data.value = d.available ? d : 'none'
    } else {
      data.value = null
    }
  } catch (err) {
    if (seq === reqSeq) data.value = null
  } finally {
    if (seq === reqSeq) loading.value = false
  }
}

// 时间选择变化立即重载
watch(() => props.sel, load, { deep: true })
onMounted(() => {
  refreshCur()
  load()
  timer = setInterval(() => { if (isLive.value) load() }, 30000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.eff { padding: 14px 16px; display: flex; flex-direction: column; gap: 12px; }
.eff-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.eff-title { font-weight: 600; }
.eff-range { font-size: 12px; color: var(--text-dim); }
.eff-empty { color: var(--text-faint); font-size: 13px; padding: 20px 0; text-align: center; line-height: 1.7; }
.eff-stats { display: flex; gap: 12px; flex-wrap: wrap; }
.eff-stat {
  flex: 1;
  min-width: 120px;
  padding: 10px 14px;
  background: var(--bg);
  border-radius: 8px;
}
.eff-num { font-size: 20px; font-weight: 600; }
.eff-num small { font-size: 12px; color: var(--text-dim); font-weight: 400; }
.eff-label { font-size: 11px; color: var(--text-dim); margin-top: 2px; }
.eff-gpus { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--text-dim); }
.eff-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 900px) { .eff-charts { grid-template-columns: 1fr; } }
</style>
