<template>
  <div class="proc glass">
    <div class="panel-head">
      <span class="panel-title">{{ title }}</span>
      <span v-if="!found" class="badge warn">未找到进程</span>
    </div>

    <template v-if="found">
      <div class="metrics mono">
        <div class="m"><span class="v">{{ pid }}</span><span class="k">PID</span></div>
        <div class="m"><span class="v" :class="cpuLevelClass">{{ cpuRtText }}</span><span class="k">实时 CPU</span></div>
        <div class="m"><span class="v">{{ cpuLifeText }}</span><span class="k">累计 CPU</span></div>
        <div class="m"><span class="v">{{ rssText }}</span><span class="k">RSS</span></div>
        <div class="m"><span class="v">{{ vszText }}</span><span class="k">VSZ</span></div>
        <div class="m"><span class="v">{{ threads ?? '—' }}</span><span class="k">线程</span></div>
        <div class="m"><span class="v">{{ elapsed || '—' }}</span><span class="k">运行时长</span></div>
      </div>

      <div v-if="service && service.active" class="service mono">
        <span class="k">服务</span><span class="v">{{ service.unit }} · {{ service.active }}</span>
        <span class="k">启动于</span><span class="v">{{ service.since || '—' }}</span>
        <span class="k">CPU 累计</span><span class="v">{{ service.cpu_total || '—' }}</span>
        <span class="k">内存</span><span class="v">{{ service.memory || '—' }}<span v-if="service.memory_peak" class="faint"> (峰值 {{ service.memory_peak }})</span></span>
        <span class="k">Tasks</span><span class="v">{{ service.tasks || '—' }}</span>
      </div>

      <div class="collapse-head" :class="{ open: cmdOpen }" @click="cmdOpen = !cmdOpen">
        <span class="arrow">▸</span> 完整命令行
      </div>
      <div class="collapse-body" :class="{ open: cmdOpen }">
        <pre class="cmdline mono">{{ cmdline || '—' }}</pre>
      </div>

      <div v-if="flagRows.length" class="collapse-head" :class="{ open: flagOpen }" @click="flagOpen = !flagOpen">
        <span class="arrow">▸</span> 参数表（{{ flagRows.length }}）
      </div>
      <div class="collapse-body" :class="{ open: flagOpen }">
        <div class="flags">
          <div v-for="[k, v] in flagRows" :key="k" class="flag">
            <span class="fk mono">{{ k }}</span>
            <span class="fv mono">{{ v }}</span>
          </div>
        </div>
      </div>
    </template>

    <div v-else class="placeholder"><span class="icon">⌁</span>{{ notFoundText || '未找到 llama-server 进程（可能以其他名称运行）' }}</div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { fmtBytes } from '../utils'

const props = defineProps({
  process: { type: Object, default: () => ({}) },
  service: { type: Object, default: () => ({}) },
  title: { type: String, default: 'llama-server 进程' },
  notFoundText: { type: String, default: '' }
})

const cmdOpen = ref(true)
const flagOpen = ref(true)

const found = computed(() => !!props.process.found)
const pid = computed(() => props.process.pid)
const cmdline = computed(() => props.process.cmdline)
const threads = computed(() => props.process.threads)
const elapsed = computed(() => props.process.elapsed)

const cpuRt = computed(() => props.process.cpu_pct_realtime ?? null)
const cpuRtText = computed(() => (cpuRt.value === null ? '—' : cpuRt.value.toFixed(1) + '%'))
const cpuLifeText = computed(() => (props.process.cpu_pct_lifetime === null || props.process.cpu_pct_lifetime === undefined ? '—' : props.process.cpu_pct_lifetime.toFixed(1) + '%'))
const rssText = computed(() => (props.process.rss_mb ? fmtBytes(props.process.rss_mb * 1024 * 1024) : '—'))
const vszText = computed(() => (props.process.vsz_mb ? fmtBytes(props.process.vsz_mb * 1024 * 1024) : '—'))
const cpuLevelClass = computed(() => {
  const v = cpuRt.value
  if (v === null) return ''
  if (v >= 900) return 'lv-danger'
  if (v >= 800) return 'lv-warn'
  return ''
})

const flagRows = computed(() => Object.entries(props.process.flags || {}))
</script>

<style scoped>
.proc { padding: 12px 16px; }
.panel-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.panel-title { font-size: 11px; color: var(--text-dim); letter-spacing: 1px; }
.metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px 12px; margin-bottom: 10px; }
.m { display: flex; flex-direction: column; gap: 2px; }
.m .v { font-size: 16px; font-weight: 700; color: var(--text); }
.m .k { font-size: 10px; color: var(--text-faint); }
.service {
  display: grid;
  grid-template-columns: 52px 1fr;
  column-gap: 8px;
  row-gap: 3px;
  border-top: 1px solid rgba(143, 163, 200, 0.1);
  padding-top: 8px;
  margin-bottom: 6px;
  font-size: 11px;
}
.service .k { color: var(--text-faint); }
.service .v { color: var(--text); word-break: break-all; }
.cmdline {
  background: rgba(5, 8, 14, 0.6);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 11px;
  color: var(--text-dim);
  white-space: pre-wrap;
  word-break: break-all;
  margin: 4px 0 8px;
  max-height: 140px;
  overflow-y: auto;
}
.flags { display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px 18px; margin: 4px 0 8px; }
.flag { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; padding: 1px 0; }
.fk { color: var(--text-dim); }
.fv { color: var(--text); text-align: right; word-break: break-all; }
</style>
