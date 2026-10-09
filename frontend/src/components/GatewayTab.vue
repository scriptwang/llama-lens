<template>
  <div class="tab-pane gw-scope">
    <div class="gw-toolbar">
      <span class="gw-title">网关</span>
      <span class="gw-sub">该主机在统一网关（集群视角）里的状态与流量切片；全局路由策略在「集群」页配置</span>
      <span class="gw-spacer" />
      <el-button size="small" @click="load">刷新</el-button>
    </div>

    <div class="gw-grid">
      <div class="gw-card">
        <div class="gw-card-title">网关状态</div>
        <div class="gw-row">
          <span class="gw-k">状态</span>
          <span :class="stateClass">{{ stateText }}</span>
        </div>
        <div class="gw-row">
          <span class="gw-k">纳入网关</span>
          <el-switch :model-value="!gwHost?.excluded" :loading="switching" @change="toggleExcluded" />
        </div>
        <div class="gw-row">
          <span class="gw-k">网关内模型</span>
          <code class="gw-v">{{ modelName || '—' }}</code>
        </div>
        <div class="gw-row">
          <span class="gw-k">llama 在线</span>
          <span :class="gwHost?.online ? 'ok' : 'off'">{{ gwHost?.online ? '是' : '否' }}</span>
        </div>
        <p class="gw-note">排除后该主机对网关完全不可见（显式前缀 / 模型亲和 / 兜底均跳过），直连 llama-server 不受影响。</p>
      </div>

      <div class="gw-card">
        <div class="gw-card-title">流量 / 成本切片（最近 {{ reqs.length }} 条审计）</div>
        <div class="gw-row"><span class="gw-k">请求数</span><b class="gw-v">{{ reqs.length }}</b></div>
        <div class="gw-row"><span class="gw-k">Tokens</span><b class="gw-v">{{ fmtNum(totalTokens) }}</b></div>
        <div class="gw-row"><span class="gw-k">费用</span><b class="gw-v">{{ totalCost > 0 ? '¥' + totalCost.toFixed(4) : '—' }}</b></div>
        <div class="gw-row"><span class="gw-k">降级次数</span><b class="gw-v">{{ degradedCount }}</b></div>
      </div>
    </div>

    <div class="gw-card">
      <div class="gw-card-title">最近请求</div>
      <table class="gw-table">
        <thead>
          <tr><th>时间</th><th>Key</th><th>模型</th><th>状态</th><th>Tokens</th><th>费用</th><th>TTFT</th><th>降级</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in reqs" :key="r.id" class="gw-row-click" title="点击查看该请求的网关链路" @click="openTrace(r)">
            <td class="nowrap">{{ fmtTs(r.ts) }}</td>
            <td>{{ keyName(r.api_key_id) }}</td>
            <td><code class="gw-v">{{ r.req_model }}</code></td>
            <td :class="r.status >= 200 && r.status < 300 ? 'ok' : 'err'">{{ r.status || '—' }}</td>
            <td>{{ r.prompt_tokens + r.completion_tokens }}</td>
            <td>{{ r.cost > 0 ? '¥' + r.cost.toFixed(4) : '—' }}</td>
            <td>{{ r.ttft_ms != null ? r.ttft_ms.toFixed(0) + 'ms' : '—' }}</td>
            <td>{{ r.degraded ? '是' : '' }}</td>
          </tr>
          <tr v-if="!reqs.length"><td colspan="8" class="gw-empty">该主机暂无网关请求记录</td></tr>
        </tbody>
      </table>
      <p class="gw-note">点击任意一行查看该请求的网关链路（span 瀑布 + 内容详情）。</p>
    </div>

    <!-- 请求链路抽屉（旁路观测：内容详情 + span 瀑布） -->
    <el-drawer v-model="showTrace" title="请求链路" size="680px">
      <div v-if="traceReq" class="trace">
        <p class="row">请求 <code class="mono">{{ traceReq.id }}</code>
          <span v-if="traceReq.ts" class="dim">· {{ fmtTs(traceReq.ts) }}</span></p>
        <p class="row">模型 <code class="mono">{{ traceReq.req_model || '—' }}</code> → 目标 {{ traceReq.target_host || '—' }}（实际 {{ traceReq.served_by || '—' }}）</p>
        <p class="row">状态 <b :class="traceReq.status >= 200 && traceReq.status < 300 ? 'ok' : 'err'">{{ traceReq.status || '—' }}</b>
          · Tokens {{ (traceReq.prompt_tokens || 0) + (traceReq.completion_tokens || 0) }}（{{ traceReq.prompt_tokens || 0 }} + {{ (traceReq.completion_tokens || 0) }}
          · 费用 {{ traceReq.cost > 0 ? '¥' + traceReq.cost.toFixed(4) : '—' }}</p>
        <p class="row dim">路径 {{ traceReq.path || '—' }} · Key {{ keyName(traceReq.api_key_id) }}
          · 首字 {{ traceReq.ttft_ms != null ? Math.round(traceReq.ttft_ms) + ' ms' : '—' }}
          · 总耗时 {{ traceReq.total_ms != null ? (traceReq.total_ms / 1000).toFixed(2) + ' s' : '—' }}
          · 重试 {{ traceReq.retries || 0 }}</p>
        <p v-if="traceReq.degraded" class="row warn">降级：{{ traceReq.served_by }}（served_by 标注）</p>
        <p v-if="traceReq.error" class="row err">错误：{{ traceReq.error }}</p>

        <!-- 内容详情（请求/响应预览，网关旁路捕获） -->
        <div v-if="traceReq.req_preview || traceReq.resp_preview" class="td-sec">
          <p class="ex-title">内容详情</p>
          <div v-if="traceReq.req_preview" class="td-block">
            <span class="td-k">请求<button class="mini" @click="copy(traceReq.req_preview)">复制</button></span>
            <pre class="mono td-pre">{{ traceReq.req_preview }}</pre>
          </div>
          <div v-if="traceReq.resp_preview" class="td-block">
            <span class="td-k">响应<button class="mini" @click="copy(traceReq.resp_preview)">复制</button></span>
            <pre class="mono td-pre">{{ traceReq.resp_preview }}</pre>
          </div>
        </div>

        <!-- span 瀑布（中文名） -->
        <div v-if="traceSpans.length" class="wf">
          <p class="ex-title">链路时间线</p>
          <div class="wf-axis">
            <span>0ms</span><span>{{ Math.round(maxSpanEnd) }}ms</span>
          </div>
          <div v-for="(sp, i) in traceSpans" :key="i" class="wf-row">
            <div class="wf-name">{{ spanName(sp.name) }}</div>
            <div class="wf-track">
              <div class="wf-bar" :class="barClass(sp)"
                   :style="{ left: (sp.start_ms / maxSpanEnd * 100) + '%', width: Math.max(0.8, (sp.end_ms - sp.start_ms) / maxSpanEnd * 100) + '%' }"></div>
            </div>
            <div class="wf-time">{{ sp.start_ms.toFixed(0) }}→{{ sp.end_ms.toFixed(0) }}ms</div>
            <div v-if="sp.meta" class="wf-meta-lines">
              <div v-for="(kv, j) in metaLines(sp.meta)" :key="j" class="wf-meta-line">
                <span class="wk">{{ kv.k }}</span><span class="wv">{{ kv.v }}</span>
              </div>
            </div>
          </div>
          <div class="wf-legend">
            <span><i class="lg gw"></i>网关</span><span><i class="lg up"></i>上游</span>
            <span><i class="lg retry"></i>重试</span><span><i class="lg deg"></i>降级</span>
          </div>
        </div>
        <p v-else class="dim">无 span 记录</p>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { fmtNum } from '../utils'

const props = defineProps({
  hostId: String,
  active: Boolean,
  hostLabel: { type: String, default: '' },
  snap: { type: Object, default: null },
})

const gwHost = ref(null)
const reqs = ref([])
const keys = ref([])
const switching = ref(false)
let loaded = false

async function load() {
  try {
    const [gh, rq, ks] = await Promise.all([
      api.gatewayHosts(),
      api.gatewayRequests({ target_host: props.hostId, limit: 100 }),
      api.gatewayKeys(),
    ])
    gwHost.value = (gh.hosts || []).find((h) => h.mid === props.hostId) || null
    reqs.value = rq.requests || []
    keys.value = ks.keys || []
    loaded = true
  } catch (e) {
    ElMessage.error('加载网关信息失败：' + e.message)
  }
}

async function toggleExcluded(val) {
  switching.value = true
  try {
    await api.gatewaySetHostExcluded(props.hostId, !val)
    ElMessage.success(val ? '已纳入网关' : '已排除出网关')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
    await load()
  } finally {
    switching.value = false
  }
}

const modelName = computed(() => props.snap?.llama?.model?.name || gwHost.value?.model_name || '')
const stateText = computed(() => {
  if (!gwHost.value) return '—'
  if (gwHost.value.excluded) return '已排除'
  return gwHost.value.online ? '服务中' : '离线（已摘除）'
})
const stateClass = computed(() => {
  if (!gwHost.value) return 'off'
  if (gwHost.value.excluded) return 'warn'
  return gwHost.value.online ? 'ok' : 'off'
})
const totalTokens = computed(() => reqs.value.reduce((s, r) => s + r.prompt_tokens + r.completion_tokens, 0))
const totalCost = computed(() => reqs.value.reduce((s, r) => s + (r.cost || 0), 0))
const degradedCount = computed(() => reqs.value.filter((r) => r.degraded).length)
function keyName(id) {
  const k = keys.value.find((x) => x.id === id)
  return k ? (k.name || k.id) : (id || '—')
}
function fmtTs(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

// ---- 链路抽屉（与集群页同款：span 瀑布 + 内容详情） ----
const showTrace = ref(false)
const traceReq = ref(null)
const traceSpans = ref([])
const SPAN_NAMES = {
  received: '接收 (received)', auth: '鉴权 (auth)', route: '路由 (route)',
  normalize: '规范化 (normalize)', forward: '转发 (forward)',
  ttft: '首字 (ttft)', done: '完成 (done)', retry: '重试 (retry)',
}
const META_KEYS = {
  path: '路径', model: '模型', ip: '来源IP', api_key: 'Key', ok: '结果',
  error: '错误', host: '主机', upstream: '上游', explicit: '显式指定',
  new_model: '实际模型', degraded: '降级原因', status: '状态码',
  tokens: 'Tokens', ms: '耗时(ms)', attempt: '重试次数', wait: '等待(s)',
  changes: '变更',
}
function spanName(n) { return SPAN_NAMES[n] || n }
function metaLines(meta) {
  try {
    const obj = JSON.parse(meta)
    return Object.entries(obj).map(([k, v]) => ({
      k: META_KEYS[k] || k,
      v: typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v),
    }))
  } catch { return [{ k: '', v: meta }] }
}
async function openTrace(r) {
  traceReq.value = { ...r }
  traceSpans.value = []
  showTrace.value = true
  try {
    const d = await api.gatewayTrace(r.id)
    traceSpans.value = d.spans || []
    if (d.request) traceReq.value = { ...r, ...d.request }
  } catch (e) {
    ElMessage.error('加载链路失败：' + e.message)
  }
}
const maxSpanEnd = computed(() => Math.max(1, traceSpans.value.reduce((m, s) => Math.max(m, s.end_ms), 1)))
function barClass(sp) {
  if (sp.name === 'retry') return 'retry'
  if (sp.name === 'route' && sp.meta && sp.meta.includes('"degraded": "')) return 'deg'
  if (sp.name === 'forward') return 'up'
  return 'gw'
}
function copy(t) {
  const doCopy = () => {
    const ta = document.createElement('textarea')
    ta.value = t
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    let okc = false
    try { okc = document.execCommand('copy') } catch { okc = false }
    document.body.removeChild(ta)
    return okc
  }
  const run = () => {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(t)
    }
    return new Promise((resolve, reject) => {
      if (doCopy()) resolve()
      else reject(new Error('copy failed'))
    })
  }
  run().then(() => ElMessage.success('已复制'))
    .catch(() => ElMessage.warning('复制失败，请手动选择'))
}

watch(() => props.active, (v) => { if (v && !loaded) load() })
onMounted(() => { if (props.active) load() })
</script>

<style scoped>
.gw-scope { display: flex; flex-direction: column; gap: 16px; }
.gw-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.gw-title { font-size: 15px; font-weight: 600; color: var(--text); }
.gw-sub { font-size: 12px; color: var(--text-faint); }
.gw-spacer { flex: 1; }
.gw-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
.gw-card {
  background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 10px;
  padding: 14px 16px;
}
.gw-card-title { font-size: 13px; font-weight: 600; color: var(--text-dim); margin-bottom: 10px; }
.gw-row { display: flex; align-items: center; gap: 10px; margin: 8px 0; font-size: 13px; }
.gw-k { color: var(--text-faint); min-width: 84px; }
.gw-v { font-family: var(--mono, ui-monospace, monospace); color: var(--text); }
code.gw-v { background: rgba(0, 229, 255, 0.08); padding: 2px 6px; border-radius: 4px; color: var(--cyan); }
.ok { color: var(--green); }
.off { color: var(--text-faint); }
.err { color: var(--red); }
.warn { color: var(--amber); }
.nowrap { white-space: nowrap; }
.gw-note { font-size: 12px; color: var(--text-faint); margin: 10px 0 0; line-height: 1.5; }
.gw-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.gw-table th, .gw-table td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--card-border); }
.gw-table th { color: var(--text-faint); font-weight: 500; }
.gw-empty { text-align: center; color: var(--text-faint); padding: 18px; }
.gw-row-click { cursor: pointer; }
.gw-row-click:hover td { background: rgba(0, 229, 255, 0.05); }
/* 链路抽屉 */
.trace .row { margin: 8px 0; font-size: 13px; color: var(--text-dim); }
.trace .dim { color: var(--text-faint); }
.trace .mono { font-family: var(--mono, ui-monospace, monospace); }
.trace code.mono { background: rgba(0, 229, 255, 0.08); padding: 2px 6px; border-radius: 4px; color: var(--cyan); }
.trace .ok { color: var(--green); }
.trace .err { color: var(--red); }
.trace .warn { color: var(--amber); }
.ex-title { font-size: 12px; color: var(--text-faint); margin: 0 0 6px; }
.mini {
  background: transparent; border: 1px solid var(--card-border); color: var(--text-dim);
  border-radius: 4px; font-size: 11px; padding: 1px 8px; cursor: pointer; margin-left: 6px; font-family: inherit;
}
.mini:hover { color: var(--cyan); border-color: var(--card-border-hover); }
/* 内容详情 */
.td-sec { margin: 14px 0; }
.td-block { margin-bottom: 10px; }
.td-k {
  display: inline-block; font-size: 11px; color: var(--text-faint);
  border: 1px solid var(--card-border); border-radius: 4px 4px 0 0;
  padding: 1px 8px; margin-bottom: -1px; background: var(--chip-inset);
}
.td-pre {
  background: var(--inset-bg); border: 1px solid var(--card-border); border-radius: 6px;
  padding: 8px 12px; font-size: 12px; color: var(--text-dim);
  white-space: pre-wrap; word-break: break-word; margin: 0;
  max-height: 220px; overflow-y: auto;
}
/* span 瀑布 */
.wf { margin-top: 16px; }
.wf-axis { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-faint); margin-bottom: 4px; padding-left: 118px; padding-right: 84px; }
.wf-row { margin-bottom: 8px; }
.wf-row .wf-name, .wf-row .wf-track, .wf-row .wf-time { display: inline-block; vertical-align: middle; }
.wf-name { width: 112px; font-size: 11px; color: var(--cyan); font-weight: 600; white-space: nowrap; }
.wf-track {
  position: relative; width: calc(100% - 202px); height: 12px; margin: 0 6px;
  background: var(--track-bg); border: 1px solid var(--card-border); border-radius: 3px;
}
.wf-bar { position: absolute; top: 1px; bottom: 1px; border-radius: 2px; }
.wf-bar.gw { background: rgba(0, 150, 255, 0.75); }
.wf-bar.up { background: rgba(0, 255, 136, 0.65); }
.wf-bar.retry { background: rgba(255, 180, 84, 0.8); }
.wf-bar.deg { background: rgba(255, 107, 107, 0.8); }
.wf-time { width: 78px; font-size: 10px; color: var(--text-faint); text-align: right; }
.wf-meta-lines {
  display: block; width: 100%; box-sizing: border-box; margin: 4px 0 0 118px;
  background: var(--inset-bg); border: 1px solid var(--card-border); border-radius: 6px;
  padding: 6px 10px; font-size: 11px;
}
.wf-meta-line { display: flex; gap: 8px; line-height: 1.7; }
.wk { color: var(--cyan); min-width: 64px; flex-shrink: 0; }
.wv { color: var(--text-dim); word-break: break-all; }
.wf-legend { display: flex; gap: 14px; margin-top: 10px; font-size: 11px; color: var(--text-faint); }
.wf-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
.lg.gw { background: rgba(0, 150, 255, 0.75); }
.lg.up { background: rgba(0, 255, 136, 0.65); }
.lg.retry { background: rgba(255, 180, 84, 0.8); }
.lg.deg { background: rgba(255, 107, 107, 0.8); }
</style>
