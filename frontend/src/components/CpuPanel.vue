<template>
  <div class="panel glass" :class="levelClass">
    <div class="panel-head">
      <span class="panel-title">CPU</span>
      <span class="mono dim small">{{ cpu.model || '—' }} · {{ cpu.cores || '—' }} 核</span>
    </div>

    <div class="usage-row">
      <span class="mono big" :class="levelClass">{{ usageText }}</span>
      <div class="cores" :class="{ dense: dense }">
        <div v-for="(c, i) in perCore" :key="i" class="core" :title="`core${i}: ${c === null ? '—' : c.toFixed(1) + '%'}`">
          <div class="core-bar"><i :class="coreClass(c)" :style="{ height: (c === null ? 0 : c) + '%' }"></i></div>
          <span v-if="!dense" class="core-idx mono">{{ i }}</span>
        </div>
      </div>
    </div>

    <div class="kv-row">
      <div class="kv"><span class="k">Load 1/5/15</span><span class="v mono">{{ loadText }}</span></div>
      <div class="kv"><span class="k">主频</span><span class="v mono">{{ mhzText }}</span></div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  cpu: { type: Object, default: () => ({}) },
  alerts: { type: Array, default: () => [] }
})

const usage = computed(() => props.cpu.usage_pct ?? null)
const usageText = computed(() => (usage.value === null ? '—' : usage.value.toFixed(1) + '%'))
const perCore = computed(() => props.cpu.per_core_pct || [])
// 核心数 >24：隐藏序号标签、收窄间距，柱条变细以容纳（序号/占用率仍在 tooltip 里）
const dense = computed(() => perCore.value.length > 24)
const load = computed(() => props.cpu.load || [])
const loadText = computed(() => (load.value.length ? load.value.map((x) => x.toFixed(2)).join(' / ') : '—'))
const mhzText = computed(() => (props.cpu.mhz ? Math.round(props.cpu.mhz) + ' MHz' : '—'))

function coreClass(v) {
  if (v === null) return ''
  if (v >= 90) return 'danger'
  if (v >= 80) return 'warn'
  return ''
}

const level = computed(() => {
  const a = (props.alerts || []).find((x) => x.metric === 'cpu')
  return a ? a.level : 'normal'
})
const levelClass = computed(() => (level.value === 'danger' ? 'lv-danger' : level.value === 'warn' ? 'lv-warn' : ''))
</script>

<style scoped>
.panel { padding: 12px 16px; }
.panel-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
.panel-title { font-size: 11px; color: var(--text-dim); letter-spacing: 1px; }
.usage-row { display: flex; align-items: flex-end; gap: 16px; margin-bottom: 8px; }
.big { font-size: 26px; font-weight: 700; color: var(--text); line-height: 1; }
.cores { flex: 1; min-width: 0; display: flex; gap: 5px; align-items: flex-end; height: 44px; }
.cores.dense { gap: 2px; }
.core { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: center; gap: 2px; height: 100%; }
.core-bar {
  flex: 1;
  width: 100%;
  background: rgba(143, 163, 200, 0.12);
  border-radius: 3px;
  display: flex;
  align-items: flex-end;
  overflow: hidden;
}
.core-bar i {
  width: 100%;
  background: var(--cyan);
  border-radius: 3px;
  transition: height 0.3s ease;
  box-shadow: 0 0 5px rgba(0, 229, 255, 0.5);
}
.core-bar i.warn { background: var(--amber); box-shadow: 0 0 5px rgba(255, 197, 61, 0.5); }
.core-bar i.danger { background: var(--red); box-shadow: 0 0 5px rgba(255, 59, 92, 0.5); }
.core-idx { font-size: 9px; color: var(--text-faint); }
.kv-row { display: grid; grid-template-columns: 1fr 1fr; gap: 0 18px; }
</style>
