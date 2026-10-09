<template>
  <div class="gt">
    <!-- 配置 -->
    <section class="glass card">
      <div class="card-head">
        <h2>网关测试</h2>
        <div class="ops">
          <button class="mini" :disabled="!selKey" @click="loadModels">刷新模型列表</button>
          <button class="mini" :disabled="!msgs.length" @click="clear">清空对话</button>
          <button class="mini" :disabled="!model" @click="openStress">推理压测</button>
        </div>
      </div>
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
        <label class="cfg-item">
          <span class="cfg-k">max_tokens</span>
          <input v-model.number="maxTokens" type="number" min="0" class="sel num" placeholder="不限" />
        </label>
      </div>
      <p v-if="!keys.length" class="dim hint">暂无 API key，请先在「API Keys」页创建。</p>
      <p v-else-if="selKey && !modelOptions.length" class="dim hint">模型列表为空（可能全部主机离线）；可手动输入模型名，或输入 host/model 指定主机。</p>
      <p class="dim hint">模型为逻辑名（跨主机去重）；同一模型在多台主机时按路由策略分流，需指定主机可手动输入 host/model。</p>
    </section>

    <!-- 内置测试用例 -->
    <section class="glass card">
      <h2>内置测试用例</h2>
      <div class="cases">
        <div v-for="c in cases" :key="c.name" class="case" :class="{ busy: running }" @click="runCase(c)">
          <span class="case-name">{{ c.name }}</span>
          <span class="case-desc">{{ c.desc }}</span>
        </div>
      </div>
      <p class="dim hint">点击用例自动填入 prompt 并立即执行（同一用例每次随机取一条样本，可反复点测不同输入），覆盖鉴权 / 路由 / 流式 / 长连接 / 结构化输出等网关能力。</p>
    </section>

    <!-- 对话 -->
    <section class="glass card">
      <div class="card-head">
        <h2>对话</h2>
        <span v-if="running" class="dim">生成中…</span>
      </div>
      <div ref="chatRef" class="chat">
        <div v-if="!msgs.length" class="empty">选择 key 与模型后，点内置用例或输入 prompt 开始测试</div>
        <template v-for="(m, i) in msgs" :key="i">
          <div v-if="m.role === 'user'" class="msg user">
            <div class="bubble">{{ m.content }}</div>
          </div>
          <div v-else class="msg asst">
            <div v-if="m.reasoning" class="reasoning">
              <div class="r-head" @click="toggleReasoning(i)">
                <el-icon class="r-caret" :class="{ open: isReasoningOpen(i) }"><CaretRight /></el-icon>
                <span class="r-label">思考过程</span>
                <span class="r-toggle">{{ isReasoningOpen(i) ? '收起' : '展开' }}</span>
              </div>
              <div v-show="isReasoningOpen(i)" class="r-body">{{ m.reasoning }}</div>
            </div>
            <div class="bubble" :class="{ err: !!m.error }">
              <span v-if="m.content" class="content">{{ m.content }}</span>
              <span v-else-if="m.reasoning && !m.error && !running" class="dim warn-text">
                无回复：模型输出被 max_tokens 截断（只生成了思考）。可加大 max_tokens 重试</span>
              <span v-else-if="!m.error && running && i === msgs.length - 1" class="dim">等待响应…</span>
              <span v-if="running && i === msgs.length - 1 && !m.error" class="cursor">▍</span>
            </div>
            <div v-if="m.error" class="err-text">{{ m.error }}</div>
            <!-- 网关调用详情（旁路观测） -->
            <div v-if="m.meta" class="gw-meta">
              <span class="gm" :class="m.meta.ok ? 'ok' : 'err'">HTTP {{ m.meta.status || '—' }}</span>
              <span v-if="m.meta.ttft_ms != null" class="gm">TTFT {{ Math.round(m.meta.ttft_ms) }} ms</span>
              <span v-if="m.meta.total_ms != null" class="gm">总耗时 {{ Math.round(m.meta.total_ms) }} ms</span>
              <span v-if="m.meta.tokens > 0" class="gm">{{ m.meta.tokens }} tokens</span>
              <span v-if="m.meta.served_by" class="gm">served_by <b class="ok">{{ m.meta.served_by }}</b></span>
              <span v-if="m.meta.degraded" class="gm warn">降级：{{ m.meta.degraded_reason || '是' }}</span>
              <span v-if="m.meta.request_id" class="gm rid" title="查看该请求的网关链路（span 瀑布图）" @click="openTrace(m)">
                <code class="mono">{{ m.meta.request_id.slice(0, 8) }}…</code> 查看调用详情 →
              </span>
            </div>
          </div>
        </template>
      </div>
      <div class="input-row">
        <textarea v-model="prompt" class="prompt" rows="2"
          placeholder="输入测试 prompt（Enter 发送，Shift+Enter 换行）"
          @keydown.enter.exact.prevent="send"></textarea>
        <div class="send-col">
          <button v-if="!running" class="btn" :disabled="!canSend" @click="send">发送</button>
          <button v-else class="btn danger" @click="stop">停止</button>
        </div>
      </div>
    </section>

    <!-- 推理压测（走网关路由，不写审计） -->
    <el-dialog v-model="stressVisible" title="网关推理压测" width="560px" :close-on-click-modal="false"
      :show-close="!stressRunning" :before-close="onStressBeforeClose">
      <div v-if="!stressRunning && !stressResult" class="st-config">
        <div class="st-row"><span class="st-label">目标模型</span>
          <code class="st-model">{{ model || '—' }}</code>
          <span class="st-hint">按当前路由策略选主机</span>
        </div>
        <div class="st-row"><span class="st-label">并发路数</span>
          <el-input-number v-model="stressCfg.concurrency" :min="1" :max="16" :step="1" size="small" controls-position="right" />
        </div>
        <div class="st-row"><span class="st-label">总请求数</span>
          <el-input-number v-model="stressCfg.count" :min="1" :max="100" :step="1" size="small" controls-position="right" />
        </div>
        <div class="st-row"><span class="st-label">每请求上限</span>
          <el-input-number v-model="stressCfg.max_tokens" :min="16" :max="512" :step="16" size="small" controls-position="right" />
          <span class="st-hint">max_tokens</span>
        </div>
        <div class="st-row st-row-col"><span class="st-label">Prompt</span>
          <el-input v-model="stressCfg.prompt" type="textarea" :rows="2" maxlength="500" show-word-limit
            placeholder="缺省：用一句话介绍你自己。" />
        </div>
        <div class="st-tip">压测走网关路由（不写审计、不污染费用统计），会占用上游推理资源（期间其他请求会变慢）。聚合吞吐为端到端口径（含 prefill）；解码吞吐与主机压测同口径（剔除 prefill）。</div>
      </div>

      <div v-if="stressRunning" class="st-progress">
        <el-progress :percentage="stressPct" :stroke-width="10" :status="stressFail ? 'exception' : undefined" />
        <div class="st-live">
          <span>完成 {{ stressDone }}/{{ stressTotal }}</span>
          <span class="st-ok">成功 {{ stressOk }}</span>
          <span class="st-fail">失败 {{ stressFail }}</span>
          <span v-if="stressLiveTps != null" class="st-tps">实时吞吐 {{ stressLiveTps }} tok/s</span>
        </div>
      </div>

      <div v-if="stressResult" class="st-result">
        <div class="st-hero">
          <span class="st-hero-num">{{ stressResult.throughput_tps }}</span>
          <span class="st-hero-unit">tokens/s · 聚合吞吐</span>
        </div>
        <div class="st-grid">
          <div class="st-cell"><span>服务主机</span><b>{{ stressResult.served_by || '—' }}</b></div>
          <div class="st-cell"><span>总 tokens</span><b>{{ stressResult.total_tokens }}</b></div>
          <div class="st-cell"><span>总耗时</span><b>{{ stressResult.wall_s }} s</b></div>
          <div class="st-cell"><span>解码吞吐</span><b>{{ stressResult.decode_tps != null ? stressResult.decode_tps + ' tok/s' : '—' }}</b></div>
          <div class="st-cell"><span>成功 / 失败</span><b>{{ stressResult.ok }} / {{ stressResult.fail }}</b></div>
          <div class="st-cell"><span>TTFT P50</span><b>{{ stressResult.ttft_p50_ms != null ? stressResult.ttft_p50_ms + ' ms' : '—' }}</b></div>
          <div class="st-cell"><span>TTFT P99</span><b>{{ stressResult.ttft_p99_ms != null ? stressResult.ttft_p99_ms + ' ms' : '—' }}</b></div>
          <div class="st-cell"><span>延迟 P50</span><b>{{ stressResult.latency_p50_ms != null ? stressResult.latency_p50_ms + ' ms' : '—' }}</b></div>
          <div class="st-cell"><span>延迟 P99</span><b>{{ stressResult.latency_p99_ms != null ? stressResult.latency_p99_ms + ' ms' : '—' }}</b></div>
        </div>
        <p v-if="stressResult.degraded" class="st-deg">⚠ 本次压测发生跨模型降级：{{ stressResult.degraded_reason }}</p>
      </div>

      <template #footer>
        <el-button v-if="stressRunning" type="danger" @click="stopStress">停止</el-button>
        <el-button v-else @click="stressVisible = false">关闭</el-button>
        <el-button v-if="!stressRunning" type="primary" :disabled="!model" @click="runStress">开始压测</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  keys: { type: Array, default: () => [] },
})
const emit = defineEmits(['trace'])

const selKeyId = ref('')
const model = ref('')
const modelOptions = ref([])
const maxTokens = ref(0)
const prompt = ref('')
const msgs = ref([])
const running = ref(false)
const chatRef = ref(null)
let abortCtrl = null

const selKey = computed(() => props.keys.find((k) => k.id === selKeyId.value) || null)
const canSend = computed(() => !!selKey.value && !!model.value && !running.value)

// 内置测试用例：每条验证一类网关能力；prompts 为样本池，每次点击随机取一条
const cases = [
  { name: '连通性', desc: '鉴权 / 路由 / 基础链路', max_tokens: 256, prompts: [
    '只回复两个字：收到', '只回复一个词：OK', '只输出：pong', '回复：链路正常',
  ] },
  { name: '流式输出', desc: 'SSE 流式稳定性', max_tokens: 512, prompts: [
    '从 1 数到 20，每行一个数字，不要其他内容。',
    '从 1 数到 30，每行一个数字，不要其他内容。',
    '依次输出 1 到 15 的偶数，每行一个，不要其他内容。',
  ] },
  { name: '长文本', desc: '长连接与长输出', max_tokens: 2048, prompts: [
    '以「海」为题写一段 300 字左右的散文。',
    '以「山」为题写一段 300 字左右的散文。',
    '以「秋」为题写一段 300 字左右的散文。',
    '以「夜」为题写一段 300 字左右的散文。',
  ] },
  { name: '代码生成', desc: '代码块与特殊字符', max_tokens: 1024, prompts: [
    '用 Python 写一个计算斐波那契数列第 n 项的函数，带注释。',
    '用 Python 写一个判断回文字符串的函数，带注释。',
    '用 Python 写一个二分查找函数，带注释。',
    '用 JavaScript 写一个数组去重函数，带注释。',
  ] },
  { name: '结构化输出', desc: 'JSON 格式输出', max_tokens: 512, prompts: [
    '只输出一个 JSON 对象，字段为 name、age、city，不要输出任何其他文字。',
    '只输出一个 JSON 数组，包含 3 个水果对象，字段为 name 和 color，不要输出任何其他文字。',
    '只输出一个 JSON 对象，字段为 id、title、price，不要输出任何其他文字。',
  ] },
  { name: '数学推理', desc: '多步推理', max_tokens: 1024, prompts: [
    '甲乙两车相距 400 公里，甲车以 80 公里/小时、乙车以 120 公里/小时相向而行，多久后相遇？请给出计算过程。',
    '一个水池单开进水管 6 小时注满、单开出水管 8 小时放完，同时开多久注满？请给出计算过程。',
    '一件商品先涨价 20% 再降价 20%，最终价格相比原价是涨还是跌？请给出计算过程。',
  ] },
  { name: '中文理解', desc: '中文语义', max_tokens: 512, prompts: [
    '用一句话解释成语「画蛇添足」的意思。',
    '用一句话解释成语「守株待兔」的意思。',
    '用一句话解释成语「亡羊补牢」的意思。',
    '用一句话解释成语「掩耳盗铃」的意思。',
  ] },
  { name: '逻辑判断', desc: '逻辑推理', max_tokens: 1024, prompts: [
    '如果所有 A 都是 B，有些 B 是 C，那么「有些 A 是 C」一定成立吗？请说明理由。',
    '三个开关控制三盏灯，你只能进房间一次，如何判断哪个开关对应哪盏灯？',
    '有 12 个外观相同的球，其中 1 个重量不同（不知轻重），用天平最少称几次一定能找出它？',
  ] },
  { name: '翻译', desc: '中英互译', max_tokens: 512, prompts: [
    '把这句话翻译成英文：科技改变生活。',
    '把这句话翻译成中文：The early bird catches the worm.',
    '把这句话翻译成英文：熟能生巧。',
    '把这句话翻译成中文：Actions speak louder than words.',
  ] },
  { name: '摘要', desc: '长文压缩', max_tokens: 512, prompts: [
    '把下面内容压缩成 20 字以内：随着大模型技术的成熟，越来越多的企业开始将 AI 能力集成到日常业务流程中，以提升效率并降低成本。',
    '用一句话概括：气候变化导致极端天气频发，对农业、能源和人类健康构成严峻挑战。',
    '用一句话概括：人工智能正在从实验室走向千行百业，成为推动产业升级的核心动力。',
  ] },
  { name: '角色扮演', desc: '人设一致性', max_tokens: 512, prompts: [
    '你是一位资深前端工程师，用 3 句话介绍 Vue 3 组合式 API 的优势。',
    '你是一位美食评论家，用 3 句话评价一碗兰州拉面。',
    '你是一位历史老师，用 3 句话介绍唐朝的繁荣。',
  ] },
  { name: '创意写作', desc: '想象力', max_tokens: 1024, prompts: [
    '写一首 4 行的关于月亮的短诗。',
    '写一个 50 字以内的科幻故事开头。',
    '给一只会说话的猫起 3 个名字并说明理由。',
  ] },
  { name: '指令遵循', desc: '格式与约束', max_tokens: 1024, prompts: [
    '列出 5 个编程语言，每个用一句话说明其特点。',
    '列出 5 种可再生能源，每个用一句话说明其原理。',
    '列出 5 个著名的世界奇迹，每个用一句话说明其位置。',
  ] },
  { name: '边界/拒答', desc: '安全与边界', max_tokens: 256, prompts: [
    '1+1 等于几？只回答数字。',
    '今天星期几？如果你不确定就回答「不知道」。',
    '用不超过 10 个字回答：水在标准大气压下多少度沸腾？',
  ] },
]

// 思考过程折叠（与主机下钻测试页一致：默认展开，可收起）
const reasoningCollapsed = ref({})
function isReasoningOpen(i) { return !reasoningCollapsed.value[i] }
function toggleReasoning(i) { reasoningCollapsed.value[i] = !reasoningCollapsed.value[i] }

function mask(k) { return k && k.length > 12 ? k.slice(0, 6) + '…' + k.slice(-4) : k }

// host/完整路径 → "host / 短名"（仅展示；请求值仍用完整 id）
function shortModel(id) {
  const i = id.indexOf('/')
  if (i < 0) return id
  const host = id.slice(0, i)
  const base = id.slice(i + 1).split('/').filter(Boolean).pop()
  return base ? host + ' / ' + base : id
}

// 模型清单：走网关 /v1/models（聚合各主机，带 host/ 前缀），用所选 key 鉴权
async function loadModels() {
  if (!selKey.value) return
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

function runCase(c) {
  if (running.value) return
  if (!selKey.value) { ElMessage.warning('请先选择 API key'); return }
  if (!model.value) {
    if (modelOptions.value.length) model.value = modelOptions.value[0].value
    else { ElMessage.warning('请先选择模型'); return }
  }
  const pool = c.prompts && c.prompts.length ? c.prompts : [c.prompt]
  prompt.value = pool[Math.floor(Math.random() * pool.length)]
  maxTokens.value = c.max_tokens || 0
  send()
}

function scrollBottom() {
  nextTick(() => {
    if (chatRef.value) chatRef.value.scrollTop = chatRef.value.scrollHeight
  })
}

function clear() {
  if (running.value) return
  msgs.value = []
}

function stop() {
  if (abortCtrl) abortCtrl.abort()
}

// SSE 帧：data: {...} / data: [DONE]；拼接 delta.content，捕获 usage
function handleFrame(frame, asst, onFirstToken, onUsage) {
  const dataLines = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return
  const payload = dataLines.join('\n')
  if (payload === '[DONE]') return
  let data
  try { data = JSON.parse(payload) } catch { return }
  if (data.error) { asst.error = data.error.message || '上游错误'; return }
  const u = data.usage
  if (u && (u.prompt_tokens || u.completion_tokens)) {
    onUsage(u.prompt_tokens || 0, u.completion_tokens || 0)
  }
  const ch = data.choices && data.choices[0]
  const delta = ch && ch.delta
  if (delta) {
    const r = delta.reasoning_content || delta.reasoning
    if (typeof r === 'string' && r) {
      asst.reasoning += r
      scrollBottom()
    }
    if (typeof delta.content === 'string' && delta.content) {
      onFirstToken()
      asst.content += delta.content
      scrollBottom()
    }
  }
}

async function send() {
  if (running.value || !canSend.value) return
  const text = prompt.value.trim()
  if (!text) return
  prompt.value = ''
  const asstMsg = { role: 'assistant', content: '', reasoning: '', error: '', meta: null }
  msgs.value.push({ role: 'user', content: text }, asstMsg)
  // 必须通过响应式数组取 proxy 再修改，直接改原对象不触发渲染
  const asst = msgs.value[msgs.value.length - 1]
  running.value = true
  abortCtrl = new AbortController()
  scrollBottom()
  const t0 = performance.now()
  let ttft = null
  let usage = { prompt: 0, completion: 0 }
  try {
    const body = {
      model: model.value,
      messages: [{ role: 'user', content: text }],
      stream: true,
      stream_options: { include_usage: true },
    }
    if (maxTokens.value > 0) body.max_tokens = maxTokens.value
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${selKey.value.key}` },
      signal: abortCtrl.signal,
      body: JSON.stringify(body),
    })
    const meta = {
      status: resp.status,
      ok: resp.status >= 200 && resp.status < 300,
      served_by: resp.headers.get('X-Served-By') || '',
      degraded: resp.headers.get('X-Degraded') === '1',
      degraded_reason: resp.headers.get('X-Degraded-Reason') || '',
      request_id: resp.headers.get('X-Request-Id') || '',
      ttft_ms: null,
      total_ms: null,
      tokens: 0,
    }
    if (!resp.ok || !resp.body) {
      let msg = `HTTP ${resp.status}`
      try { const j = await resp.json(); msg = j.detail || (j.error && j.error.message) || msg } catch { /* ignore */ }
      asst.error = msg
      meta.total_ms = performance.now() - t0
      asst.meta = meta
      return
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        handleFrame(frame, asst,
          () => { if (ttft == null) ttft = performance.now() - t0 },
          (p, c) => { usage.prompt = p; usage.completion = c })
      }
    }
    meta.ttft_ms = ttft
    meta.total_ms = performance.now() - t0
    meta.tokens = usage.prompt + usage.completion
    asst.meta = meta
  } catch (e) {
    if (e && e.name === 'AbortError') asst.error = '已停止'
    else asst.error = String((e && e.message) || e)
    if (!asst.meta) {
      asst.meta = { status: 0, ok: false, ttft_ms: ttft, total_ms: performance.now() - t0, tokens: 0 }
    }
  } finally {
    running.value = false
    abortCtrl = null
    scrollBottom()
  }
}

// 下钻链路：交给 ClusterView 打开瀑布图抽屉（旁路观测）
function openTrace(m) {
  const meta = m.meta
  if (!meta || !meta.request_id) return
  emit('trace', {
    id: meta.request_id,
    partial: {
      id: meta.request_id,
      req_model: model.value,
      target_host: meta.served_by || '',
      served_by: meta.served_by || '',
      status: meta.status || 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      cost: 0,
      degraded: meta.degraded ? 1 : 0,
      error: m.error || (meta.degraded ? (meta.degraded_reason || '降级') : ''),
    },
  })
}

onBeforeUnmount(() => {
  if (abortCtrl) abortCtrl.abort()
  if (stressAbort) stressAbort.abort()
})

// ---- 推理压测（走网关路由，SSE 回报；不写审计） ----
const stressVisible = ref(false)
const stressRunning = ref(false)
const stressCfg = reactive({ concurrency: 4, count: 8, max_tokens: 64, prompt: '' })
const stressDone = ref(0)
const stressTotal = ref(0)
const stressOk = ref(0)
const stressFail = ref(0)
const stressLiveTps = ref(null)
const stressResult = ref(null)
let stressAbort = null
let stTokens = 0
let stT0 = 0

function openStress() {
  if (stressRunning.value) return
  if (!model.value) { ElMessage.warning('请先选择模型'); return }
  stressResult.value = null
  stressDone.value = 0
  stressTotal.value = 0
  stressOk.value = 0
  stressFail.value = 0
  stressLiveTps.value = null
  stressVisible.value = true
}

const stressPct = computed(() => (stressTotal.value ? Math.round((stressDone.value / stressTotal.value) * 100) : 0))

function onStressBeforeClose(done) {
  if (stressRunning.value) { ElMessage.warning('压测进行中，请先停止'); return }
  done()
}

function stopStress() {
  if (stressAbort) stressAbort.abort()
}

async function runStress() {
  if (stressRunning.value) return
  stressRunning.value = true
  stressResult.value = null
  stressDone.value = 0
  stressTotal.value = stressCfg.count
  stressOk.value = 0
  stressFail.value = 0
  stressLiveTps.value = null
  stTokens = 0
  stT0 = Date.now()
  stressAbort = new AbortController()
  try {
    const resp = await fetch('/api/gateway/stress', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('llama_token')}` },
      signal: stressAbort.signal,
      body: JSON.stringify({ model: model.value, ...stressCfg }),
    })
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        handleStressFrame(frame)
      }
    }
  } catch (e) {
    if (e && e.name !== 'AbortError') ElMessage.error('压测失败：' + String((e && e.message) || e))
  } finally {
    stressRunning.value = false
    stressAbort = null
  }
}

function handleStressFrame(frame) {
  let event = ''
  const dataLines = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice(7).trim()
    else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
  }
  if (!dataLines.length) return
  let data
  try { data = JSON.parse(dataLines.join('\n')) } catch { return }
  if (event === 'progress') {
    stressDone.value = data.done
    stressTotal.value = data.total
    stressOk.value = data.ok
    stressFail.value = data.fail
    if (data.tokens) {
      stTokens += data.tokens
      const secs = (Date.now() - stT0) / 1000
      stressLiveTps.value = secs > 0 ? Math.round(stTokens / secs) : null
    }
  } else if (event === 'summary') {
    stressResult.value = data
  } else if (event === 'error') {
    ElMessage.error(data.msg || '压测出错')
  }
}
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
.mono { font-family: var(--mono, ui-monospace, monospace); }
code.mono { background: rgba(0, 229, 255, 0.08); padding: 2px 6px; border-radius: 4px; color: var(--cyan); }
.ok { color: var(--green); }
.err { color: var(--red); }
.warn { color: var(--amber); }
.err-text { color: var(--red); font-size: 12px; margin-top: 6px; }
.hint { font-size: 12px; margin: 10px 0 0; }
.mini {
  background: transparent; border: 1px solid var(--card-border); color: var(--text-dim);
  border-radius: 4px; font-size: 11px; padding: 2px 8px; cursor: pointer; margin-left: 6px; font-family: inherit;
}
.mini:hover { color: var(--cyan); border-color: var(--card-border-hover); }
.mini:disabled { opacity: 0.4; cursor: not-allowed; }
.btn {
  background: rgba(0, 229, 255, 0.12); color: var(--cyan); border: 1px solid var(--card-border);
  border-radius: 6px; font-size: 12px; padding: 8px 18px; cursor: pointer; font-family: inherit;
}
.btn:hover { background: rgba(0, 229, 255, 0.2); }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
.btn.danger { background: rgba(255, 107, 107, 0.12); color: var(--red); }
.btn.danger:hover { background: rgba(255, 107, 107, 0.2); }
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
/* 配置行 */
.cfg { display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap; }
.cfg-item { display: flex; flex-direction: column; gap: 5px; }
.cfg-item.grow { flex: 1; min-width: 260px; }
.cfg-k { font-size: 11px; color: var(--text-faint); }
.model-sel { width: 100%; }
.num { width: 90px; }
/* 内置用例 */
.cases { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
.case {
  position: relative; overflow: hidden;
  border: 1px solid var(--card-border); border-radius: 8px; padding: 10px 12px;
  cursor: pointer; display: flex; flex-direction: column; gap: 4px;
  transition: border-color .15s, background .15s, transform .15s;
}
.case::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 2px;
  background: var(--cyan); opacity: 0; transition: opacity .15s;
}
.case:hover { border-color: var(--card-border-hover); background: rgba(0, 229, 255, 0.04); transform: translateY(-1px); }
.case:hover::before { opacity: 0.6; }
.case.busy { opacity: 0.5; cursor: not-allowed; }
.case-name { font-size: 13px; font-weight: 600; color: var(--text); }
.case-desc { font-size: 11px; color: var(--text-faint); }
/* 对话 */
.chat {
  max-height: 420px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px;
  padding: 4px 2px; margin-bottom: 12px;
}
.chat .empty { text-align: center; color: var(--text-faint); padding: 28px 0; font-size: 13px; }
.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.msg.asst { flex-direction: column; align-items: flex-start; }
.bubble {
  max-width: 82%; padding: 9px 13px; border-radius: 10px; font-size: 13px;
  line-height: 1.65; white-space: pre-wrap; word-break: break-word;
}
.msg.user .bubble { background: rgba(0, 229, 255, 0.1); border: 1px solid var(--card-border); color: var(--text); }
.msg.asst .bubble { background: var(--card-inset); border: 1px solid var(--card-border); color: var(--text); align-self: flex-start; }
.msg.asst .bubble.err { border-color: rgba(255, 107, 107, 0.4); }
.cursor { color: var(--cyan); animation: gt-blink 1s steps(1) infinite; }
.warn-text { color: var(--amber); }
/* 思考过程（可折叠，与主机下钻测试页一致） */
.reasoning {
  width: 100%; max-width: 82%; margin-bottom: 8px;
  border: 1px solid var(--card-border);
  border-left: 3px solid color-mix(in srgb, var(--amber) 60%, transparent);
  border-radius: 6px;
  background: color-mix(in srgb, var(--text) 3%, transparent);
  overflow: hidden;
}
.r-head {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; cursor: pointer; font-size: 12px;
  color: var(--text-dim); user-select: none;
}
.r-head:hover { color: var(--text); }
.r-caret { transition: transform .15s; font-size: 12px; }
.r-caret.open { transform: rotate(90deg); }
.r-label { font-size: 12px; font-weight: 600; }
.r-toggle { margin-left: auto; color: var(--text-faint); font-size: 11px; }
.r-body {
  padding: 4px 12px 10px;
  font-size: 12px; color: var(--text-faint); line-height: 1.6;
  white-space: pre-wrap; word-break: break-word;
  border-top: 1px dashed var(--card-border);
  max-height: 220px; overflow-y: auto;
}
@keyframes gt-blink { 50% { opacity: 0; } }
/* 网关调用详情条 */
.gw-meta {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 6px;
  font-size: 11px; color: var(--text-faint);
}
.gm {
  border: 1px solid var(--card-border); border-radius: 10px; padding: 1px 8px;
  background: var(--chip-inset);
}
.gm.ok { color: var(--green); }
.gm.err { color: var(--red); }
.gm.warn { color: var(--amber); }
.gm b { font-weight: 600; }
.gm.rid { cursor: pointer; color: var(--text-dim); }
.gm.rid:hover { color: var(--cyan); border-color: var(--card-border-hover); }
/* 输入行 */
.input-row { display: flex; gap: 10px; align-items: flex-end; }
.prompt {
  flex: 1; background: var(--inset-bg); border: 1px solid var(--card-border);
  border-radius: 8px; padding: 9px 12px; font-size: 13px; color: var(--text);
  font-family: inherit; resize: vertical; line-height: 1.5;
}
.prompt:focus { outline: none; border-color: var(--card-border-hover); }
.send-col { display: flex; flex-direction: column; gap: 6px; }
/* 推理压测 */
.st-config { display: flex; flex-direction: column; gap: 12px; }
.st-row { display: flex; align-items: center; gap: 10px; }
.st-row-col { flex-direction: column; align-items: stretch; gap: 6px; }
.st-label { width: 96px; flex: none; font-size: 13px; color: var(--text-dim); }
.st-model {
  background: rgba(0, 229, 255, 0.08); padding: 2px 8px; border-radius: 4px;
  color: var(--cyan); font-size: 12px; word-break: break-all;
}
.st-hint { font-size: 12px; color: var(--text-faint); }
.st-tip { font-size: 12px; color: var(--text-faint); line-height: 1.6; padding: 8px 10px; background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 6px; }
.st-progress { display: flex; flex-direction: column; gap: 14px; }
.st-live { display: flex; gap: 18px; font-size: 13px; color: var(--text-dim); flex-wrap: wrap; }
.st-live .st-ok { color: var(--green); }
.st-live .st-fail { color: var(--red); }
.st-live .st-tps { color: var(--cyan); font-weight: 600; }
.st-result { display: flex; flex-direction: column; gap: 14px; }
.st-hero { display: flex; align-items: baseline; gap: 10px; padding: 10px 14px; background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 8px; }
.st-hero-num { font-size: 34px; font-weight: 700; color: var(--cyan); font-family: var(--mono, ui-monospace, monospace); }
.st-hero-unit { font-size: 12px; color: var(--text-faint); }
.st-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.st-cell { display: flex; flex-direction: column; gap: 3px; padding: 8px 10px; background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 6px; }
.st-cell span { font-size: 11px; color: var(--text-faint); }
.st-cell b { font-size: 15px; color: var(--text); font-family: var(--mono, ui-monospace, monospace); }
.st-deg { color: var(--amber); font-size: 12px; margin: 0; }
</style>
