<template>
  <div class="eff glass">
    <div class="eff-head">
      <span class="eff-title">效率统计</span>
      <span class="eff-ranges">
        <button v-for="r in ranges" :key="r.v" :class="{ on: range === r.v }" @click="load(r.v)">{{ r.label }}</button>
      </span>
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
        <div class="eff-stat">
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
import { ref, computed, onMounted } from 'vue'
import { fmtNum, fmtTokens } from '../utils'
import TrendChart from './TrendChart.vue'

// 效率统计（P1-2）：token 产出 + GPU 耗电 + tokens/瓦，数据来自历史库。
const props = defineProps({
  hostId: { type: String, required: true },
})

const ranges = [
  { v: '24h', label: '24h' },
  { v: '7d', label: '7 天' },
  { v: '30d', label: '30 天' },
]
const range = ref('7d')
const data = ref(null)
const loading = ref(false)

const bucketUnit = computed(() => (range.value === '24h' ? 'tokens/时' : 'tokens/天'))

const tokenSeries = computed(() => data.value
  ? [{ name: 'tokens', ts: data.value.series.tokens.ts, values: data.value.series.tokens.values, area: true }]
  : [])
const powerSeries = computed(() => data.value
  ? [{ name: 'W', ts: data.value.series.power_w.ts, values: data.value.series.power_w.values }]
  : [])

async function load(r) {
  range.value = r
  loading.value = true
  try {
    const token = localStorage.getItem('llama_token')
    const resp = await fetch(`/api/efficiency?host_id=${encodeURIComponent(props.hostId)}&range=${r}`, {
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
    })
    if (resp.ok) {
      const d = await resp.json()
      data.value = d.available ? d : 'none'
    } else {
      data.value = null
    }
  } catch (e) {
    data.value = null
  } finally {
    loading.value = false
  }
}

onMounted(() => load('7d'))
</script>

<style scoped>
.eff { padding: 14px 16px; display: flex; flex-direction: column; gap: 12px; }
.eff-head { display: flex; align-items: center; justify-content: space-between; }
.eff-title { font-weight: 600; }
.eff-ranges { display: inline-flex; gap: 4px; }
.eff-ranges button {
  background: none;
  border: 1px solid var(--card-border);
  color: var(--text-dim);
  font-size: 12px;
  padding: 3px 10px;
  border-radius: 4px;
  cursor: pointer;
}
.eff-ranges button.on { color: var(--text); border-color: var(--cyan); background: color-mix(in srgb, var(--cyan) 10%, transparent); }
.eff-empty { color: var(--text-faint); font-size: 13px; padding: 20px 0; text-align: center; }
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
