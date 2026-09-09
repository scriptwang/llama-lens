<template>
  <div class="tab-pane req-scope">
    <div class="req-toolbar">
      <span class="req-title">请求历史</span>
      <span class="req-sub">llama-server 日志解析的已完成任务明细（最近 {{ tasks.length }} 条）</span>
      <span class="req-spacer" />
      <el-switch v-model="autoRefresh" size="small" active-text="自动刷新" />
      <el-button size="small" @click="load">刷新</el-button>
    </div>
    <div v-if="!logAvailable" class="req-warn">日志采集不可用（该主机未配置日志源），无法解析请求明细</div>
    <div class="req-table-wrap">
      <table v-if="tasks.length" class="req-table">
        <thead>
          <tr>
            <th>时间</th><th>任务</th><th>槽位</th>
            <th class="num">Prompt tokens</th><th class="num">预填充</th>
            <th class="num">生成 tokens</th><th class="num">生成耗时</th>
            <th class="num">总耗时</th><th class="num">总 tokens</th>
            <th class="num">MTP</th><th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in tasks" :key="i">
            <td class="mono">{{ fmtTime(t.ended_at) }}</td>
            <td class="mono">#{{ t.task_id }}</td>
            <td class="mono">{{ t.slot != null ? t.slot : '—' }}</td>
            <td class="num mono">{{ t.prompt_tokens != null ? t.prompt_tokens : '—' }}</td>
            <td class="num mono">{{ fmtMs(t.prompt_ms) }}<span v-if="t.prompt_speed_tps" class="dim">（{{ t.prompt_speed_tps.toFixed(0) }} t/s）</span></td>
            <td class="num mono">{{ t.decoded_tokens != null ? t.decoded_tokens : '—' }}</td>
            <td class="num mono">{{ fmtMs(t.eval_ms) }}<span v-if="t.gen_speed_tps" class="dim">（{{ t.gen_speed_tps.toFixed(1) }} t/s）</span></td>
            <td class="num mono">{{ fmtMs(t.total_ms) }}</td>
            <td class="num mono">{{ t.total_tokens != null ? t.total_tokens : '—' }}</td>
            <td class="num mono">{{ t.mtp && t.mtp.acceptance != null ? (t.mtp.acceptance * 100).toFixed(0) + '%' : '—' }}</td>
            <td>
              <span class="req-st" :class="t.interrupted ? 'st-int' : (t.truncated ? 'st-trunc' : 'st-ok')">
                {{ t.interrupted ? '已中断' : (t.truncated ? '截断' : '完成') }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else class="req-empty">暂无请求记录，llama-server 处理请求后自动出现</div>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import http from '../api/client'

const props = defineProps({
  hostId: String,
  active: Boolean,
  hostLabel: { type: String, default: '' },
})

const tasks = ref([])
const logAvailable = ref(true)
const autoRefresh = ref(true)
let timer = null

// /tasks 是主应用端点，host_id 用 mid（props.hostId 即 mid）
async function load() {
  if (!props.hostId) return
  try {
    const data = await http.get(`/hosts/${props.hostId}/tasks`, { params: { limit: 100 } })
    tasks.value = data.tasks || []
    logAvailable.value = !!data.log_available
  } catch { /* client 已提示 */ }
}
function startTimer() {
  stopTimer()
  timer = setInterval(() => { if (autoRefresh.value && props.active) load() }, 5000)
}
function stopTimer() { if (timer) { clearInterval(timer); timer = null } }
function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const p = (x) => String(x).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
function fmtMs(ms) {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms.toFixed(0)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}
onMounted(() => {
  if (props.active) { load(); startTimer() }
})
watch(() => props.active, (a) => {
  if (a) { load(); startTimer() } else stopTimer()
})
watch(() => props.hostId, () => {
  tasks.value = []
  if (props.active) load()
})
onBeforeUnmount(stopTimer)
</script>

<style scoped>
.req-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.req-title { font-size: 14px; font-weight: 600; color: var(--text, #eee); }
.req-sub { font-size: 12px; color: var(--lc-text-muted, #999); }
.req-spacer { flex: 1; }
.req-warn { padding: 10px 12px; margin-bottom: 10px; border-radius: 8px; font-size: 12px; color: #eab308; background: color-mix(in srgb, #eab308 10%, transparent); border: 1px solid color-mix(in srgb, #eab308 30%, transparent); }
.req-table-wrap { border: 1px solid var(--lc-border, #333); border-radius: 10px; overflow: auto; max-height: calc(100vh - 260px); }
.req-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.req-table th { position: sticky; top: 0; background: var(--lc-bg-2, #1e2230); color: var(--lc-text-muted, #999); font-weight: 500; text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--lc-border, #333); white-space: nowrap; }
.req-table td { padding: 7px 10px; border-bottom: 1px solid color-mix(in srgb, var(--lc-border, #333) 40%, transparent); white-space: nowrap; color: var(--text, #ddd); }
.req-table tr:hover td { background: color-mix(in srgb, var(--lc-primary, #409eff) 6%, transparent); }
.req-table .num { text-align: right; }
.req-table th.num { text-align: right; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.dim { color: var(--lc-text-muted, #888); font-size: 11px; }
.req-st { font-size: 11px; padding: 1px 8px; border-radius: 10px; }
.st-ok { color: #22c55e; background: color-mix(in srgb, #22c55e 12%, transparent); }
.st-trunc { color: #eab308; background: color-mix(in srgb, #eab308 12%, transparent); }
.st-int { color: #ef4444; background: color-mix(in srgb, #ef4444 12%, transparent); }
.req-empty { padding: 40px 0; text-align: center; color: var(--lc-text-muted, #999); font-size: 13px; }
</style>
