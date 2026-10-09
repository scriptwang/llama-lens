<template>
  <div class="ac">
    <!-- 连接参数 -->
    <section class="glass card">
      <h2>连接参数</h2>
      <div class="cfg">
        <label class="cfg-item">
          <span class="cfg-k">API Key</span>
          <select v-model="selKeyId" class="sel" :disabled="!keys.length" @change="onKeyChange">
            <option value="" disabled>选择 key</option>
            <option v-for="k in keys" :key="k.id" :value="k.id">{{ k.name || k.id }}（{{ mask(k.key) }}）</option>
          </select>
        </label>
        <label class="cfg-item grow">
          <span class="cfg-k">模型</span>
          <el-select v-model="model" filterable allow-create default-first-option
            placeholder="选择或手动输入（host/model 可指定主机）" :disabled="!selKey" class="model-sel">
            <el-option v-for="m in modelOptions" :key="m.value" :label="m.label" :value="m.value" />
          </el-select>
        </label>
      </div>
      <div v-if="!keys.length" class="warn-box">
        <b>没有 API Key</b> — 请先在
        <a href="javascript:void(0)" @click="$emit('goto-keys')">API Keys 页</a>
        创建，下方调用示例会自动填入参数
      </div>
      <p v-else-if="selKey && !modelOptions.length && !modelLoading" class="dim hint">
        模型列表为空（可能全部主机离线）；可手动输入模型名，或输入 host/model 指定主机</p>
      <p class="dim hint">模型下拉为网关聚合清单（逻辑模型名，跨主机去重）；同一模型在多台主机时按路由策略分流；需指定主机可手动输入 host/model</p>
    </section>

    <!-- 路由策略 -->
    <section class="glass card">
      <h2>路由策略</h2>
      <div v-if="status" class="strategy">
        <div class="st-row">
          <el-switch v-model="st.affinity" :loading="stBusy" @change="saveStrategy" />
          <span class="st-name">档1 模型亲和</span>
          <span class="dim st-desc">按 model 找装了它的主机（关 = 模型名不参与路由，请求走兜底主机）</span>
        </div>
        <div class="st-row">
          <el-switch v-model="st.same_model_lb" :loading="stBusy" :disabled="!st.affinity" @change="saveStrategy" />
          <span class="st-name">档2 同模型 LB</span>
          <span class="dim st-desc">同一模型多台主机时按生成速度分流（仅档1 开时有效）</span>
        </div>
        <div class="st-row">
          <el-switch v-model="st.cross_model_fallback" :loading="stBusy" :disabled="!st.affinity" @change="saveStrategy" />
          <span class="st-name">档3 跨模型降级</span>
          <span class="dim st-desc">目标模型不可用时按规则降级，响应透明标注 served_by/degraded（仅档1 开时有效）</span>
        </div>
        <div v-if="st.affinity && st.cross_model_fallback" class="fb">
          <div class="fb-head">
            <span class="ex-title">降级规则（JSON：model 支持 * 通配，fallback 按序尝试）</span>
            <button class="mini" @click="saveFallbackRules">保存规则</button>
          </div>
          <textarea v-model="fbText" class="fb-editor mono" rows="4"
            placeholder='[{"model": "qwen*", "fallback": ["llama"]}]'></textarea>
        </div>
        <p class="dim hint">开关立即生效并持久化（重启保留）；config.yaml gateway.strategy 为初始值。</p>
      </div>
      <p v-else class="dim">加载中…</p>
    </section>

    <!-- 模型单价（费用/费用额度的计费依据） -->
    <section class="glass card">
      <h2>模型单价</h2>
      <p class="dim hint">单价为元 / 1k tokens（prompt + completion）。配置后请求才会计费，API Key 的「费用额度」才会生效；未配置时费用记 0。</p>
      <div class="prices">
        <div class="price-row head">
          <span>模型</span><span>单价（元/1k tokens）</span><span></span>
        </div>
        <div v-for="(p, i) in priceRows" :key="i" class="price-row">
          <input v-model="p.name" class="sel grow" placeholder="模型名（如 qwen3.8，支持部分匹配）" />
          <input v-model.number="p.price" type="number" min="0" step="0.01" class="sel num" placeholder="0" />
          <button class="mini" @click="priceRows.splice(i, 1)">删除</button>
        </div>
        <p v-if="!priceRows.length" class="dim">未配置单价（费用均记 0，费用额度不生效）</p>
      </div>
      <div class="fb-head">
        <span class="dim hint">匹配规则：模型 basename 相等或包含（忽略大小写）；config.yaml gateway.model_prices 为初始值</span>
        <button class="mini" :disabled="priceBusy" @click="savePrices">保存单价</button>
      </div>
    </section>

    <!-- 调用示例（参数自动填入） -->
    <section class="glass card">
      <div class="card-head">
        <h2>调用示例</h2>
        <span v-if="!ready" class="dim">参数未填全，示例中为占位符</span>
        <span v-else class="ok">已填入所选参数</span>
      </div>
      <div class="ex">
        <div class="ex-head"><span class="ex-title">curl</span>
          <button class="mini" @click="copy(curlExample)">复制</button></div>
        <pre class="mono">{{ curlExample }}</pre>
      </div>
    </section>

    <!-- 调用链路 -->
    <section class="glass card">
      <h2>调用链路</h2>
      <div class="chain-wrap">
        <svg v-if="hosts.length" class="chain" :viewBox="`0 0 ${CW} ${chainH}`" preserveAspectRatio="xMidYMid meet">
          <defs>
            <linearGradient id="gwGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="rgba(0,229,255,0.18)" />
              <stop offset="100%" stop-color="rgba(0,229,255,0.03)" />
            </linearGradient>
          </defs>
          <!-- 入口 → 网关 -->
          <path class="chain-line main" :d="entryPath" />
          <path class="chain-line main flow" :d="entryPath" />
          <!-- 网关 → 各主机 -->
          <template v-for="(h, i) in hosts" :key="h.id">
            <path class="chain-line" :class="hostState(h)" :d="hostPath(i)" />
            <path v-if="hostState(h) === 'selected'" class="chain-line flow" :d="hostPath(i)" />
          </template>
          <!-- 入口节点 -->
          <g class="node entry">
            <rect :x="entry.x" :y="entry.y" :width="entry.w" :height="entry.h" rx="12" />
            <text :x="entry.x + entry.w / 2" :y="entry.y + 27" text-anchor="middle" class="n-name">入口</text>
            <text :x="entry.x + entry.w / 2" :y="entry.y + 46" text-anchor="middle" class="n-sub">Codex / curl / 应用</text>
          </g>
          <!-- 网关节点 -->
          <g class="node gateway">
            <rect :x="gw.x" :y="gw.y" :width="gw.w" :height="gw.h" rx="14" />
            <text :x="gw.x + gw.w / 2" :y="gw.y + 30" text-anchor="middle" class="n-name">统一网关</text>
            <text :x="gw.x + gw.w / 2" :y="gw.y + 50" text-anchor="middle" class="n-sub">{{ shortBase }}</text>
            <text :x="gw.x + gw.w / 2" :y="gw.y + 68" text-anchor="middle" class="n-sub">{{ status ? (status.hosts_online || 0) + ' / ' + (status.hosts_total || 0) + ' 在线' : '' }}</text>
          </g>
          <!-- 主机节点 -->
          <g v-for="(h, i) in hosts" :key="'n' + h.id" class="node" :class="hostState(h)"
             @mouseenter="hoverInfo = nodeTip(h)" @mouseleave="hoverInfo = ''">
            <title>{{ nodeTip(h) }}</title>
            <rect :x="hostX" :y="hostY(i)" :width="hostW" :height="hostH" rx="10" />
            <circle :cx="hostX + 18" :cy="hostY(i) + hostH / 2" r="4.5" class="dot" />
            <text :x="hostX + 32" :y="hostY(i) + 20" class="n-name">{{ h.name }}</text>
            <text :x="hostX + 32" :y="hostY(i) + 37" class="n-sub">{{ shortModel(h.model_name) || '—' }}</text>
            <text v-if="badge(h)" :x="hostX + hostW - 12" :y="hostY(i) + 20" text-anchor="end" class="n-badge">{{ badge(h) }}</text>
          </g>
        </svg>
        <p v-else class="dim">暂无主机</p>
        <p class="hover-tip" :class="hoverCls">{{ hoverInfo || '悬停主机节点查看路由说明' }}</p>
        <div class="legend">
          <span><i class="lg sel"></i>所选模型</span>
          <span><i class="lg fb"></i>兜底</span>
          <span><i class="lg deg"></i>跨模型降级</span>
          <span><i class="lg off"></i>离线 / 已排除</span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const props = defineProps({
  hosts: { type: Array, default: () => [] },
  status: { type: Object, default: null },
  keys: { type: Array, default: () => [] },
})
const emit = defineEmits(['refresh', 'goto-keys'])

// ---- 连接参数 ----
const selKeyId = ref('')
const model = ref('')
const modelOptions = ref([])
const modelLoading = ref(false)
const selKey = computed(() => props.keys.find((k) => k.id === selKeyId.value) || null)
const ready = computed(() => !!selKey.value && !!model.value)

function mask(k) { return k && k.length > 12 ? k.slice(0, 6) + '…' + k.slice(-4) : k }

// host/完整路径 → "host / 短名"（仅展示）
function shortModel(id) {
  if (!id) return ''
  const i = id.indexOf('/')
  if (i < 0) return id
  const host = id.slice(0, i)
  const base = id.slice(i + 1).split('/').filter(Boolean).pop()
  return base ? host + ' / ' + base : id
}

async function loadModels() {
  if (!selKey.value) return
  modelLoading.value = true
  try {
    const resp = await fetch('/v1/models', { headers: { Authorization: `Bearer ${selKey.value.key}` } })
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`
      try { const j = await resp.json(); msg = j.detail || (j.error && j.error.message) || msg } catch { /* ignore */ }
      ElMessage.error('获取模型列表失败：' + msg)
      return
    }
    const d = await resp.json()
    modelOptions.value = (d.data || []).filter((x) => x.id)
      .map((x) => ({ value: x.id, label: modelLabel(x) }))
    if (!model.value && modelOptions.value.length) model.value = modelOptions.value[0].value
  } catch (e) {
    ElMessage.error('获取模型列表失败：' + e.message)
  } finally {
    modelLoading.value = false
  }
}
// 逻辑模型名（跨主机去重）；value 与 label 均为裸模型名，主机由路由策略决策
function modelLabel(x) {
  return x.id
}
function onKeyChange() {
  modelOptions.value = []
  model.value = ''
  loadModels()
}

// ---- 路由策略（从总览移入：接入即配置） ----
const st = reactive({ affinity: true, same_model_lb: false, cross_model_fallback: false })
const stBusy = ref(false)
const fbText = ref('[]')
watch(() => props.status, (s) => {
  if (!s) return
  const s0 = s.strategy || {}
  st.affinity = !!s0.affinity
  st.same_model_lb = !!s0.same_model_lb
  st.cross_model_fallback = !!s0.cross_model_fallback
  fbText.value = JSON.stringify(s0.fallback_rules || [], null, 2)
}, { immediate: true })
async function saveStrategy() {
  if (!st.affinity) {
    // 档2/档3 依赖档1 的模型匹配：关档1 时同步关闭，避免"开着但永不生效"
    st.same_model_lb = false
    st.cross_model_fallback = false
  }
  stBusy.value = true
  try {
    await api.gatewaySetStrategy({
      affinity: st.affinity,
      same_model_lb: st.same_model_lb,
      cross_model_fallback: st.cross_model_fallback,
    })
    ElMessage.success('路由策略已更新')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    stBusy.value = false
    emit('refresh')
  }
}
async function saveFallbackRules() {
  let rules
  try { rules = JSON.parse(fbText.value || '[]') } catch {
    ElMessage.error('规则不是合法 JSON')
    return
  }
  if (!Array.isArray(rules)) { ElMessage.error('规则需为 JSON 数组'); return }
  stBusy.value = true
  try {
    await api.gatewaySetStrategy({ fallback_rules: rules })
    ElMessage.success('降级规则已保存')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    stBusy.value = false
    emit('refresh')
  }
}
function fbRules() {
  try {
    const r = JSON.parse(fbText.value || '[]')
    return Array.isArray(r) ? r : []
  } catch { return [] }
}

// ---- 模型单价（费用/费用额度的计费依据） ----
const priceRows = ref([])
const priceBusy = ref(false)
watch(() => props.status, (s) => {
  if (!s) return
  const prices = s.model_prices || {}
  priceRows.value = Object.entries(prices).map(([name, price]) => ({ name, price }))
}, { immediate: true })
async function savePrices() {
  const prices = {}
  for (const r of priceRows.value) {
    const name = (r.name || '').trim()
    if (!name) continue
    const p = Number(r.price)
    if (!isFinite(p) || p < 0) { ElMessage.error('单价需为非负数字：' + name); return }
    prices[name] = p
  }
  priceBusy.value = true
  try {
    await api.gatewaySetPrices({ model_prices: prices })
    ElMessage.success('模型单价已保存')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    priceBusy.value = false
    emit('refresh')
  }
}

// ---- 调用示例（参数自动填入） ----
const shortBase = computed(() => {
  const b = (props.status && props.status.base_url) || ''
  return b.replace(/^https?:\/\//, '')
})
const curlExample = computed(() => {
  const base = (props.status && props.status.base_url) || '<网关地址>'
  const key = selKey.value ? selKey.value.key : '<API_KEY>'
  const m = model.value || '<model>'
  return `curl ${base}/chat/completions \\\n  -H "Authorization: Bearer ${key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"${m}","stream":true,"messages":[{"role":"user","content":"hi"}]}'`
})
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
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(t)
    return new Promise((resolve, reject) => {
      if (doCopy()) resolve()
      else reject(new Error('copy failed'))
    })
  }
  run().then(() => ElMessage.success('已复制'))
    .catch(() => ElMessage.warning('复制失败，请手动选择'))
}

// ---- 调用链路（SVG 曲线） ----
const CW = 880
const hostX = 650
const hostW = 206
const hostH = 48
const hostGap = 14
const chainH = computed(() => Math.max(220, props.hosts.length * (hostH + hostGap) + 44))
const entry = computed(() => ({ x: 24, y: chainH.value / 2 - 32, w: 150, h: 64 }))
const gw = computed(() => ({ x: 330, y: chainH.value / 2 - 42, w: 220, h: 84 }))
function hostY(i) {
  const n = props.hosts.length
  const top = (chainH.value - n * (hostH + hostGap) + hostGap) / 2
  return top + i * (hostH + hostGap)
}
const entryPath = computed(() => {
  const y = chainH.value / 2
  return `M ${entry.value.x + entry.value.w} ${y} C ${entry.value.x + entry.value.w + 60} ${y}, ${gw.value.x - 60} ${y}, ${gw.value.x} ${y}`
})
function hostPath(i) {
  const gy = chainH.value / 2
  const hy = hostY(i) + hostH / 2
  const x1 = gw.value.x + gw.value.w
  const x2 = hostX
  return `M ${x1} ${gy} C ${x1 + 80} ${gy}, ${x2 - 80} ${hy}, ${x2} ${hy}`
}

// 主机状态：排除 > 离线 > 所选模型 > 降级目标 > 兜底 > 普通
function isModelHost(h) {
  if (!model.value) return false
  const i = model.value.indexOf('/')
  if (i > 0) return model.value.slice(0, i) === h.id
  const base = model.value.split('/').pop().toLowerCase()
  const hm = ((h.model_name || '').split('/').pop() || '').toLowerCase()
  return !!hm && (hm === base || hm.includes(base) || base.includes(hm))
}
function isDegradedTarget(h) {
  if (!st.cross_model_fallback) return false
  for (const r of fbRules()) {
    const fbs = r.fallback || []
    if (fbs.includes(h.id) || fbs.includes(h.name)) return true
  }
  return false
}
function hostState(h) {
  if (h.gateway_excluded) return 'excluded'
  if (!h.online) return 'offline'
  if (isModelHost(h)) return 'selected'
  if (isDegradedTarget(h)) return 'degraded'
  if (props.status && h.id === props.status.default_host) return 'fallback'
  return 'normal'
}
const selectedCount = computed(() => props.hosts.filter((h) => hostState(h) === 'selected').length)
function badge(h) {
  const s = hostState(h)
  return { selected: '选中', fallback: '兜底', degraded: '降级目标', offline: '离线', excluded: '已排除' }[s] || ''
}
function nodeTip(h) {
  const s = hostState(h)
  const m = shortModel(h.model_name) || '未知模型'
  if (s === 'excluded') return `${h.name}：已在主机详情中排除，对网关不可见`
  if (s === 'offline') return `${h.name}：离线（已自动摘除，不参与路由）`
  if (s === 'selected') {
    if (selectedCount.value > 1) {
      return st.same_model_lb
        ? `${h.name}：已加载所选模型（${m}），请求按生成速度分流到这 ${selectedCount.value} 台主机`
        : `${h.name}：已加载所选模型（${m}），请求路由到匹配的主机（多台时取第一台）`
    }
    return `${h.name}：已加载所选模型，请求将路由到此主机（${m}）`
  }
  if (s === 'degraded') return `${h.name}：跨模型降级目标——目标模型不可用时请求可能路由到此，响应带 X-Degraded 透明标注（${m}）`
  if (s === 'fallback') return `${h.name}：兜底主机——未指定模型或无匹配时，请求落到这里（${m}）`
  return `${h.name}：在线（${m}）`
}
const hoverInfo = ref('')
const hoverCls = computed(() => {
  if (!hoverInfo.value) return 'dim'
  const h = props.hosts.find((x) => nodeTip(x) === hoverInfo.value)
  if (!h) return 'dim'
  const s = hostState(h)
  return s === 'degraded' ? 'tip-deg' : s === 'fallback' ? 'tip-fb' : s === 'selected' ? 'tip-sel' : 'dim'
})
</script>

<style scoped>
.card { padding: 18px 20px; }
.card h2 {
  font-size: 15px; margin: 0 0 14px; color: var(--text);
  display: flex; align-items: center; gap: 8px;
}
.card h2::before {
  content: ''; width: 3px; height: 14px; background: var(--cyan);
  box-shadow: 0 0 6px var(--cyan); border-radius: 2px; flex: none;
}
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; gap: 12px; flex-wrap: wrap; }
.card-head h2 { margin: 0; }
.dim { color: var(--text-faint); }
.ok { color: var(--green); font-size: 12px; }
.mono { font-family: var(--mono, ui-monospace, monospace); }
.hint { font-size: 12px; margin: 10px 0 0; }
.mini {
  background: transparent; border: 1px solid var(--card-border); color: var(--text-dim);
  border-radius: 4px; font-size: 11px; padding: 2px 8px; cursor: pointer; margin-left: 6px; font-family: inherit;
}
.mini:hover { color: var(--cyan); border-color: var(--card-border-hover); }
.sel {
  background-color: var(--inset-bg-strong);
  background-image: var(--select-arrow);
  background-repeat: no-repeat;
  background-position: right 8px center;
  background-size: 10px 6px;
  color: var(--text); border: 1px solid var(--card-border-hover);
  border-radius: 6px; font-size: 12px; padding: 5px 26px 5px 10px; font-family: inherit;
}
.sel:focus { border-color: var(--cyan); }
.sel:disabled { opacity: 0.5; cursor: not-allowed; }
/* 连接参数 */
.cfg { display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap; }
.cfg-item { display: flex; flex-direction: column; gap: 5px; }
.cfg-item.grow { flex: 1; min-width: 260px; }
.cfg-k { font-size: 11px; color: var(--text-faint); }
.model-sel { width: 100%; }
.warn-box {
  margin-top: 12px; padding: 10px 14px; border-radius: 8px; font-size: 13px;
  color: var(--red); background: rgba(255, 59, 92, 0.08); border: 1px solid rgba(255, 59, 92, 0.35);
}
.warn-box a { color: var(--red); text-decoration: underline; cursor: pointer; }
/* 路由策略 */
.strategy { display: flex; flex-direction: column; gap: 10px; }
.st-row { display: flex; align-items: center; gap: 10px; }
.st-name { font-size: 13px; color: var(--text); min-width: 96px; }
.st-desc { font-size: 12px; }
.fb { margin-top: 6px; }
.fb-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.ex-title { font-size: 12px; color: var(--text-faint); }
.fb-editor {
  width: 100%; background: var(--inset-bg); border: 1px solid var(--card-border);
  border-radius: 6px; padding: 8px 10px; font-size: 12px; color: var(--text-dim);
  font-family: var(--mono, ui-monospace, monospace); resize: vertical;
}
/* 模型单价 */
.prices { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
.price-row { display: flex; align-items: center; gap: 10px; }
.price-row.head { font-size: 11px; color: var(--text-faint); }
.price-row.head span { font-size: 11px; }
.price-row .grow { flex: 1; min-width: 220px; }
.price-row .num { width: 140px; }
/* 调用示例 */
.ex { margin: 12px 0; }
.ex-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.ex pre {
  background: var(--inset-bg); border: 1px solid var(--card-border); border-radius: 6px;
  padding: 10px 12px; font-size: 12px; color: var(--text-dim);
  white-space: pre-wrap; word-break: break-all; margin: 0;
}
/* 调用链路 */
.chain-wrap { max-width: 880px; margin: 0 auto; }
.chain { width: 100%; height: auto; display: block; }
.chain-line { fill: none; stroke-width: 2; }
.chain-line.main { stroke: var(--cyan); opacity: 0.75; }
.chain-line.selected { stroke: var(--cyan); opacity: 0.9; }
.chain-line.fallback { stroke: var(--amber); opacity: 0.7; }
.chain-line.degraded { stroke: var(--red); opacity: 0.8; stroke-dasharray: 6 5; }
.chain-line.normal { stroke: var(--text-faint); opacity: 0.4; }
.chain-line.offline, .chain-line.excluded { stroke: var(--text-faint); opacity: 0.22; stroke-dasharray: 3 5; }
.chain-line.flow {
  stroke: #fff; opacity: 0.55; stroke-width: 1.5;
  stroke-dasharray: 4 12; animation: chain-flow 1.1s linear infinite;
}
@keyframes chain-flow { to { stroke-dashoffset: -16; } }
.node rect {
  fill: var(--inset-bg); stroke: var(--card-border); stroke-width: 1;
  transition: stroke .15s, fill .15s;
}
.node:hover rect { stroke: var(--card-border-hover); }
.node.selected rect { stroke: var(--cyan); fill: rgba(0, 229, 255, 0.08); }
.node.fallback rect { stroke: var(--amber); fill: rgba(255, 197, 61, 0.06); }
.node.degraded rect { stroke: var(--red); fill: rgba(255, 59, 92, 0.07); }
.node.offline rect, .node.excluded rect { opacity: 0.55; }
.node.gateway rect { stroke: var(--card-border-hover); fill: url(#gwGrad); }
.node .dot { fill: var(--green); }
.node.selected .dot { fill: var(--cyan); }
.node.fallback .dot { fill: var(--amber); }
.node.degraded .dot { fill: var(--red); }
.node.offline .dot, .node.excluded .dot { fill: var(--text-faint); }
.n-name { fill: var(--text); font-size: 13px; font-weight: 600; }
.n-sub { fill: var(--text-faint); font-size: 10px; }
.n-badge { font-size: 10px; font-weight: 600; }
.node.selected .n-badge { fill: var(--cyan); }
.node.fallback .n-badge { fill: var(--amber); }
.node.degraded .n-badge { fill: var(--red); }
.node.offline .n-badge, .node.excluded .n-badge { fill: var(--text-faint); }
.hover-tip { font-size: 12px; margin: 10px 0 0; min-height: 18px; }
.hover-tip.tip-sel { color: var(--cyan); }
.hover-tip.tip-fb { color: var(--amber); }
.hover-tip.tip-deg { color: var(--red); }
.legend { display: flex; gap: 16px; margin-top: 10px; font-size: 11px; color: var(--text-faint); flex-wrap: wrap; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; }
.lg.sel { background: var(--cyan); }
.lg.fb { background: var(--amber); }
.lg.deg { background: var(--red); }
.lg.off { background: var(--text-faint); opacity: 0.5; }
</style>
