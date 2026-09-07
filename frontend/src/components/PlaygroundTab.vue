<template>
  <div class="tab-pane pg">
    <!-- Endpoint 信息卡 -->
    <div class="pg-endpoint">
      <span class="pg-dot" :class="info.online ? 'on' : 'off'"></span>
      <span class="pg-label">OpenAI 兼容地址</span>
      <code class="pg-url">{{ info.endpoint || '—' }}</code>
      <el-button size="small" text @click="copyEndpoint">
        <el-icon style="margin-right:3px"><CopyDocument /></el-icon>复制
      </el-button>
      <span v-if="info.model" class="pg-model">模型：{{ info.model }}</span>
      <span v-if="!info.online" class="pg-offline">llama 离线，聊天不可用</span>
    </div>

    <!-- 聊天区 -->
    <div ref="chatBox" class="pg-chat">
      <div v-if="loadingSessions && !chat.length" class="pg-empty">加载会话中…</div>
      <div v-else-if="!chat.length" class="pg-empty">
        输入消息开始推理测试——流式回复，自动统计 TTFT / tokens/s / 总耗时；支持图片（需模型带 mmproj）
      </div>
      <div v-for="(m, i) in chat" :key="i" class="pg-msg" :class="m.role">
        <div class="pg-role">{{ m.role === 'user' ? '你' : modelFileName }}</div>
        <div class="pg-content" :class="{ 'is-md': m.role === 'assistant' }">
          <template v-if="m.images && m.images.length">
            <img v-for="(src, j) in m.images" :key="j" :src="src" class="pg-img" />
          </template>
          <!-- 思考过程（可折叠，默认展开；流式时末尾光标） -->
          <div v-if="m.role === 'assistant' && m.reasoning" class="pg-reasoning">
            <div class="pg-reasoning-head" @click="toggleReasoning(i)">
              <el-icon class="pg-reasoning-caret" :class="{ open: isReasoningOpen(i) }"><CaretRight /></el-icon>
              <span class="pg-reasoning-title">思考过程</span>
              <span class="pg-reasoning-toggle">{{ isReasoningOpen(i) ? '收起' : '展开' }}</span>
            </div>
            <div v-show="isReasoningOpen(i)" class="pg-reasoning-body">
              <span class="pg-md" v-html="reasoningHtml(m, i)"></span>
            </div>
          </div>
          <span v-if="m.role === 'assistant' && m.content" class="pg-md" v-html="asstHtml(m, i)"></span>
          <span v-else-if="m.content">{{ m.content }}</span>
          <span v-else-if="streaming && i === chat.length - 1" class="pg-cursor">▍</span>
          <span v-else-if="m.error" class="pg-err">{{ m.error }}</span>
          <span v-else-if="!m.reasoning" class="pg-muted">（无内容）</span>
          <span v-if="m.stopped" class="pg-stopped">（已手动停止）</span>
        </div>
        <div v-if="m.metrics" class="pg-metrics">
          <span v-if="m.metrics.ttft_ms != null">TTFT {{ m.metrics.ttft_ms }} ms</span>
          <span>· {{ m.metrics.tps }} tokens/s</span>
          <span>· {{ (m.metrics.total_ms / 1000).toFixed(1) }} s</span>
          <span>· {{ m.metrics.tokens }} tokens</span>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="pg-input">
      <!-- 采样参数 -->
      <div class="pg-params">
        <span class="pg-p-label">参数</span>
        <div class="pg-p-item"><span>温度</span>
          <el-slider v-model="params.temperature" :min="0" :max="2" :step="0.1" style="width:96px"
            :format-tooltip="v => String(v)" />
        </div>
        <div class="pg-p-item"><span>top_p</span>
          <el-input-number v-model="params.top_p" :min="0" :max="1" :step="0.05" :precision="2" size="small" controls-position="right" class="pg-p-num" />
        </div>
        <div class="pg-p-item"><span>top_k</span>
          <el-input-number v-model="params.top_k" :min="0" :max="400" :step="1" size="small" controls-position="right" class="pg-p-num" />
        </div>
        <div class="pg-p-item"><span>min_p</span>
          <el-input-number v-model="params.min_p" :min="0" :max="1" :step="0.05" :precision="2" size="small" controls-position="right" class="pg-p-num" />
        </div>
        <div class="pg-p-item"><span>重复惩罚</span>
          <el-input-number v-model="params.repeat_penalty" :min="0.5" :max="2" :step="0.05" :precision="2" size="small" controls-position="right" class="pg-p-num" />
        </div>
        <div class="pg-p-item"><span>max_tokens</span>
          <el-input-number v-model="params.max_tokens" :min="0" :max="32768" :step="256" size="small" controls-position="right" class="pg-p-num-lg" />
        </div>
        <el-button size="small" text @click="resetParams">重置</el-button>
      </div>

      <!-- 待发送图片 -->
      <div v-if="pendingImages.length" class="pg-imgs">
        <div v-for="(src, i) in pendingImages" :key="i" class="pg-img-chip">
          <img :src="src" />
          <span class="pg-img-x" title="移除" @click="pendingImages.splice(i, 1)"><el-icon><Close /></el-icon></span>
        </div>
      </div>

      <!-- 输入行 -->
      <div class="pg-row">
        <el-input ref="taRef" v-model="input" type="textarea" :rows="3" resize="vertical"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行；可添加/粘贴图片（需模型带 mmproj）"
          @keydown.enter.exact.prevent="send" @paste="onPaste" />
        <div class="pg-btns">
          <el-button class="pg-attach" title="添加图片" @click="fileRef && fileRef.click()">
            <el-icon :size="18"><Picture /></el-icon>
          </el-button>
          <el-button v-if="!streaming" type="primary" :disabled="!input.trim() && !pendingImages.length" @click="send">
            发送
          </el-button>
          <el-button v-else type="danger" @click="stop">
            停止
          </el-button>
        </div>
        <input ref="fileRef" type="file" accept="image/*" multiple class="pg-file" @change="onPickImages" />
      </div>

      <!-- 会话行 -->
      <div class="pg-sessions">
        <el-button size="small" @click="newSession">
          <el-icon style="margin-right:4px"><Plus /></el-icon>新会话
        </el-button>
        <el-dropdown v-if="sessionList.length > 1" trigger="click" @command="loadSession">
          <el-button size="small" plain>
            <el-icon style="margin-right:4px"><Clock /></el-icon>历史会话（{{ sessionList.length }}）
          </el-button>
          <template #dropdown>
            <el-dropdown-menu class="pg-sess-menu">
              <el-dropdown-item v-for="ss in sessionList" :key="ss.id" :command="ss.id" class="pg-sess-item">
                <span class="pg-sess-title">{{ ss.title }}</span>
                <span class="pg-sess-time">{{ fmtTime(ss.created) }}</span>
                <el-icon class="pg-sess-del" title="删除" @click.stop="deleteSession(ss.id)"><Delete /></el-icon>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <span class="toolbar-spacer" />
        <el-button v-if="chat.length" size="small" text @click="clearCurrent">清空当前</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMd } from '../utils/md'

// Playground Tab（P1-1）：代理 llama-server 聊天（SSE 流式）+ TTFT/tokens/s 指标。
// 增强：图片消息（mmproj）、采样参数（top_k/top_p/min_p/重复惩罚/max_tokens）、
// 会话历史（存业务库 llama_ctl.db，按 host+user，可新建/切换/删除）。
// hostId=mid（主监控 API 直接用 mid，无需 db_id）。
const props = defineProps({
  hostId: { type: String, required: true },
  active: { type: Boolean, default: false },
  hostLabel: { type: String, default: '' },
})

const info = ref({ endpoint: '', model: '', online: false })
// 助手标签：显示模型文件名（路径取最后一段），无模型时回退「助手」
const modelFileName = computed(() => {
  const m = info.value.model || ''
  if (!m) return '助手'
  return m.split('/').pop()
})
const chat = ref([])
const input = ref('')
const streaming = ref(false)
const chatBox = ref(null)
const taRef = ref(null)
const fileRef = ref(null)
const pendingImages = ref([])
// 思考过程折叠状态（按消息索引，默认展开；仅 UI 状态，不持久化）
const reasoningCollapsed = ref({})
function isReasoningOpen(i) { return !reasoningCollapsed.value[i] }
function toggleReasoning(i) { reasoningCollapsed.value[i] = !reasoningCollapsed.value[i] }

// ---------------- 采样参数（按主机持久化） ----------------
const PKEY = `llamalens.pg.params.${props.hostId}`
const DEFAULT_PARAMS = { temperature: 0.7, top_p: 0.95, top_k: 40, min_p: 0.05, repeat_penalty: 1.1, max_tokens: 0 }
const params = reactive({ ...DEFAULT_PARAMS })
try {
  const saved = JSON.parse(localStorage.getItem(PKEY) || 'null')
  if (saved) Object.assign(params, DEFAULT_PARAMS, saved)
} catch { /* 忽略损坏的参数 */ }
watch(params, () => {
  try { localStorage.setItem(PKEY, JSON.stringify(params)) } catch { /* 忽略 */ }
}, { deep: true })
function resetParams() {
  Object.assign(params, DEFAULT_PARAMS)
}

// ---------------- 会话历史（存业务库 llama_ctl.db，按 host+user 维度） ----------------
const store = reactive({ current: '', sessions: {} })
const loadingSessions = ref(false)

function newSessionId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}
function blankSession() {
  return { title: '新会话', created: Date.now(), updated: Date.now() }
}
function authHeaders() {
  return { Authorization: `Bearer ${localStorage.getItem('llama_token')}` }
}
async function apiGet(path) {
  const resp = await fetch(path, { headers: { Accept: 'application/json', ...authHeaders() } })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  const j = await resp.json()
  if (j.code !== 0) throw new Error(j.msg || '请求失败')
  return j.data
}
async function apiSend(path, method, body) {
  const resp = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  const j = await resp.json()
  if (j.code !== 0) throw new Error(j.msg || '请求失败')
  return j.data
}
function hydrateMessages(msgs) {
  return (msgs || []).map((m) => ({ ...m, images: m.images ? [...m.images] : [] }))
}
function sessPath(id) {
  return `/api/hosts/${encodeURIComponent(props.hostId)}/chat/sessions/${encodeURIComponent(id)}`
}

// 加载会话列表 + 最近会话的消息
async function loadStore() {
  loadingSessions.value = true
  try {
    const list = await apiGet(`/api/hosts/${encodeURIComponent(props.hostId)}/chat/sessions`)
    store.sessions = {}
    for (const s of list) store.sessions[s.id] = { title: s.title, created: s.created_at, updated: s.updated_at }
    if (list.length) {
      store.current = list[0].id
      chat.value = hydrateMessages(await getSessionMessages(store.current))
    } else {
      store.current = newSessionId()
      store.sessions[store.current] = blankSession()
      chat.value = []
    }
  } catch (e) {
    if (!store.current) {
      store.current = newSessionId()
      store.sessions[store.current] = blankSession()
      chat.value = []
    }
  } finally {
    loadingSessions.value = false
    scrollBottom()
  }
}
async function getSessionMessages(id) {
  const d = await apiGet(sessPath(id))
  return d.messages || []
}
// 保存当前会话到库。用 promise 链串行化，避免快速连续保存时 API 乱序覆盖（丢消息）。
let persistChain = Promise.resolve()
function persist() {
  const id = store.current
  if (!id) return persistChain
  const sess = store.sessions[id]
  const title = sess ? sess.title : '新会话'
  const messages = chat.value  // 捕获当前消息数组引用（切换会话后仍指向本会话）
  persistChain = persistChain.then(() =>
    apiSend(sessPath(id), 'PUT', { title, messages })
      .then(() => { if (sess) sess.updated = Date.now() })
      .catch((e) => console.warn('保存会话失败', e))
  )
  return persistChain
}
const sessionList = computed(() =>
  Object.entries(store.sessions)
    .map(([id, ss]) => ({ id, title: ss.title, created: ss.created, updated: ss.updated }))
    .sort((a, b) => (b.updated || b.created) - (a.updated || a.created)))

function newSession() {
  if (streaming.value) { ElMessage.warning('等待当前回复完成'); return }
  store.current = newSessionId()
  store.sessions[store.current] = blankSession()
  chat.value = []
  scrollBottom()
}
async function loadSession(id) {
  if (streaming.value) { ElMessage.warning('等待当前回复完成'); return }
  if (id === store.current) return
  store.current = id
  chat.value = []
  try {
    chat.value = hydrateMessages(await getSessionMessages(id))
  } catch (e) {
    ElMessage.error('加载会话失败')
  }
  scrollBottom()
}
async function deleteSession(id) {
  if (streaming.value) { ElMessage.warning('等待当前回复完成'); return }
  try { await apiSend(sessPath(id), 'DELETE') } catch (e) { /* 忽略删除失败 */ }
  delete store.sessions[id]
  if (id === store.current) {
    const rest = Object.keys(store.sessions)
    if (rest.length) {
      store.current = rest.sort((a, b) => (store.sessions[b].updated || store.sessions[b].created) - (store.sessions[a].updated || store.sessions[a].created))[0]
      loadSession(store.current)
    } else {
      store.current = newSessionId()
      store.sessions[store.current] = blankSession()
      chat.value = []
    }
  }
  scrollBottom()
}
function clearCurrent() {
  if (streaming.value) { ElMessage.warning('等待当前回复完成'); return }
  chat.value = []
  const sess = store.sessions[store.current]
  if (sess) { sess.title = '新会话'; sess.updated = Date.now() }
  persist()
}
function fmtTime(ts) {
  const d = new Date(ts)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// ---------------- 图片（客户端压缩后以 base64 发送） ----------------
const MAX_IMAGES = 4
function downscale(file, maxSide = 1024, quality = 0.85) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      const scale = Math.min(1, maxSide / Math.max(img.width, img.height))
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.round(img.width * scale))
      canvas.height = Math.max(1, Math.round(img.height * scale))
      canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height)
      URL.revokeObjectURL(url)
      resolve(canvas.toDataURL('image/jpeg', quality))
    }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('decode')) }
    img.src = url
  })
}
async function addImageFiles(files) {
  for (const f of files) {
    if (!f.type || !f.type.startsWith('image/')) continue
    if (pendingImages.value.length >= MAX_IMAGES) { ElMessage.warning(`最多 ${MAX_IMAGES} 张图片`); break }
    try {
      pendingImages.value.push(await downscale(f))
    } catch {
      ElMessage.error(`图片读取失败：${f.name || '粘贴图片'}`)
    }
  }
}
async function onPickImages(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''
  await addImageFiles(files)
}
// Ctrl+V 粘贴图片
async function onPaste(e) {
  const items = Array.from((e.clipboardData && e.clipboardData.items) || [])
  const files = items
    .filter((i) => i.type && i.type.startsWith('image/'))
    .map((i) => i.getAsFile())
    .filter(Boolean)
  if (!files.length) return
  e.preventDefault()
  await addImageFiles(files)
}

// ---------------- 输入框高度：默认随内容自适应，手动拉伸后以用户为准 ----------------
let userResized = false
function autoGrow() {
  if (userResized) return
  const ta = taRef.value && taRef.value.textarea
  if (!ta) return
  ta.style.height = 'auto'
  ta.style.height = Math.min(Math.max(ta.scrollHeight, 84), 240) + 'px'
}
watch(input, autoGrow)
function bindTaResize() {
  const ta = taRef.value && taRef.value.textarea
  if (ta) ta.addEventListener('resize', () => { userResized = true })
}

// ---------------- 聊天 ----------------
async function loadInfo() {
  try {
    const token = localStorage.getItem('llama_token')
    const resp = await fetch(`/api/hosts/${encodeURIComponent(props.hostId)}/playground`, {
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
    })
    if (resp.ok) info.value = await resp.json()
  } catch (e) { /* 忽略 */ }
}

function scrollBottom() {
  nextTick(() => {
    if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
  })
}

function copyEndpoint() {
  if (!info.value.endpoint) return
  navigator.clipboard.writeText(info.value.endpoint)
    .then(() => ElMessage.success('已复制'))
    .catch(() => ElMessage.error('复制失败'))
}

function handleFrame(frame, asstMsg) {
  let event = 'message'
  const dataLines = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return
  let data
  try { data = JSON.parse(dataLines.join('\n')) } catch (e) { return }
  if (event === 'error') {
    asstMsg.error = data.msg || '未知错误'
  } else if (event === 'metrics') {
    asstMsg.metrics = data
  } else if (typeof data.reasoning === 'string' && data.reasoning) {
    asstMsg.reasoning += data.reasoning
    scrollBottom()
  } else if (data.choices && data.choices[0] && data.choices[0].delta && data.choices[0].delta.content) {
    asstMsg.content += data.choices[0].delta.content
    scrollBottom()
  }
}

// 助手消息：Markdown 渲染；流式时在末尾追加快闪光标
function asstHtml(m, i) {
  let html = renderMd(m.content)
  if (streaming.value && i === chat.value.length - 1) html += '<span class="pg-cursor">▍</span>'
  return html
}
// 思考过程：Markdown 渲染；流式且尚未出正文时末尾光标
function reasoningHtml(m, i) {
  let html = renderMd(m.reasoning)
  if (streaming.value && i === chat.value.length - 1 && !m.content) html += '<span class="pg-cursor">▍</span>'
  return html
}

// 消息 → OpenAI 格式（带图片时 content 为多模态数组）
function toApiMessages() {
  return chat.value
    .filter((m) => m.content || (m.images && m.images.length))
    .map((m) => {
      if (!m.images || !m.images.length) return { role: m.role, content: m.content }
      const parts = []
      if (m.content) parts.push({ type: 'text', text: m.content })
      for (const src of m.images) parts.push({ type: 'image_url', image_url: { url: src } })
      return { role: m.role, content: parts }
    })
}

let abortCtrl = null
function stop() {
  if (abortCtrl) abortCtrl.abort()
}

async function send() {
  const text = input.value.trim()
  if ((!text && !pendingImages.value.length) || streaming.value) return
  if (!store.current) { store.current = newSessionId(); store.sessions[store.current] = blankSession() }
  const images = [...pendingImages.value]
  input.value = ''
  pendingImages.value = []
  const asstMsg = { role: 'assistant', content: '', reasoning: '', metrics: null, error: '' }
  chat.value.push({ role: 'user', content: text, images }, asstMsg)
  // 必须通过响应式数组取 proxy 再修改，直接改原对象不触发渲染（会整段一次性显示）
  const asst = chat.value[chat.value.length - 1]
  // 会话标题：取首条用户消息
  const sess = store.sessions[store.current]
  if (sess && sess.title === '新会话') {
    sess.title = (text || '（图片）').slice(0, 24)
  }
  streaming.value = true
  abortCtrl = new AbortController()
  scrollBottom()
  try {
    const token = localStorage.getItem('llama_token')
    const resp = await fetch(`/api/hosts/${encodeURIComponent(props.hostId)}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      signal: abortCtrl.signal,
      body: JSON.stringify({
        messages: toApiMessages(),
        temperature: params.temperature,
        top_p: params.top_p,
        top_k: params.top_k,
        min_p: params.min_p,
        repeat_penalty: params.repeat_penalty,
        max_tokens: params.max_tokens,
      }),
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
        handleFrame(frame, asst)
      }
    }
  } catch (e) {
    if (e && e.name === 'AbortError') {
      asst.stopped = true
    } else {
      asst.error = String((e && e.message) || e)
    }
  } finally {
    streaming.value = false
    abortCtrl = null
    scrollBottom()
    persist()
  }
}

onMounted(() => {
  loadInfo()
  loadStore()
  autoGrow()
  bindTaResize()
})
</script>

<style scoped>
.pg { gap: 14px; }
.pg-endpoint {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 8px;
  flex-wrap: wrap;
}
.pg-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.pg-dot.on { background: var(--green); }
.pg-dot.off { background: var(--red); }
.pg-label { color: var(--text-dim); font-size: 12px; }
.pg-url {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  background: var(--bg);
  padding: 3px 8px;
  border-radius: 4px;
}
.pg-model { color: var(--text-dim); font-size: 12px; }
.pg-offline { color: var(--red); font-size: 12px; }
.pg-chat {
  flex: 1;
  min-height: 320px;
  max-height: 52vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 8px;
}
.pg-empty { color: var(--text-faint); font-size: 13px; text-align: center; margin: auto; }
.pg-msg { display: flex; flex-direction: column; gap: 4px; max-width: 88%; }
.pg-msg.user { align-self: flex-end; align-items: flex-end; }
.pg-msg.assistant { align-self: flex-start; }
.pg-role { font-size: 11px; color: var(--text-faint); }
.pg-content {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.6;
  padding: 8px 12px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--text) 5%, transparent);
  border: 1px solid var(--card-border);
}
.pg-content.is-md { white-space: normal; }
.pg-md :deep(p) { margin: 0 0 8px; }
.pg-md :deep(p:last-child) { margin-bottom: 0; }
.pg-md :deep(pre) {
  background: var(--bg);
  border: 1px solid var(--card-border);
  border-radius: 6px;
  padding: 10px 12px;
  overflow-x: auto;
  margin: 8px 0;
}
.pg-md :deep(code) { font-family: var(--font-mono, monospace); font-size: 12px; }
.pg-md :deep(:not(pre) > code) {
  background: color-mix(in srgb, var(--text) 10%, transparent);
  padding: 1px 5px;
  border-radius: 4px;
}
.pg-md :deep(h1), .pg-md :deep(h2), .pg-md :deep(h3), .pg-md :deep(h4) {
  margin: 10px 0 6px;
  font-size: 14px;
  font-weight: 700;
}
.pg-md :deep(h1):first-child, .pg-md :deep(h2):first-child, .pg-md :deep(h3):first-child { margin-top: 0; }
.pg-md :deep(ul), .pg-md :deep(ol) { margin: 4px 0; padding-left: 20px; }
.pg-md :deep(li) { margin: 2px 0; }
.pg-md :deep(table) { border-collapse: collapse; margin: 8px 0; font-size: 12px; display: block; overflow-x: auto; }
.pg-md :deep(th), .pg-md :deep(td) { border: 1px solid var(--card-border); padding: 4px 8px; }
.pg-md :deep(th) { background: color-mix(in srgb, var(--text) 6%, transparent); }
.pg-md :deep(blockquote) {
  border-left: 3px solid var(--card-border);
  padding-left: 10px;
  margin: 8px 0;
  color: var(--text-dim);
}
.pg-md :deep(a) { color: var(--cyan); }
/* 思考过程块（可折叠） */
.pg-reasoning {
  margin-bottom: 8px;
  border: 1px solid var(--card-border);
  border-left: 3px solid color-mix(in srgb, var(--amber) 60%, transparent);
  border-radius: 6px;
  background: color-mix(in srgb, var(--text) 3%, transparent);
  overflow: hidden;
}
.pg-reasoning-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-dim);
  user-select: none;
}
.pg-reasoning-head:hover { color: var(--text); }
.pg-reasoning-caret { transition: transform .15s; font-size: 12px; }
.pg-reasoning-caret.open { transform: rotate(90deg); }
.pg-reasoning-title { font-weight: 600; }
.pg-reasoning-toggle { margin-left: auto; color: var(--text-faint); font-size: 11px; }
.pg-reasoning-body {
  padding: 4px 12px 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-dim);
  word-break: break-word;
  border-top: 1px dashed var(--card-border);
}
.pg-msg.user .pg-content {
  background: color-mix(in srgb, var(--cyan) 12%, transparent);
  border-color: color-mix(in srgb, var(--cyan) 25%, transparent);
}
.pg-img {
  display: block;
  max-width: 240px;
  max-height: 240px;
  border-radius: 6px;
  margin-bottom: 6px;
}
.pg-cursor { animation: pg-blink 1s infinite; }
@keyframes pg-blink { 50% { opacity: 0; } }
.pg-err { color: var(--red); }
.pg-muted { color: var(--text-faint); }
.pg-stopped { color: var(--amber); font-size: 12px; }
.pg-metrics { font-size: 11px; color: var(--text-dim); font-family: var(--font-mono, monospace); }
.pg-input { display: flex; flex-direction: column; gap: 10px; }
/* 参数行 */
.pg-params {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 8px 12px;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 8px;
}
.pg-p-label { font-size: 12px; color: var(--text-dim); font-weight: 600; }
.pg-p-item { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-dim); }
.pg-p-num { width: 86px; }
.pg-p-num-lg { width: 100px; }
/* 待发送图片 */
.pg-imgs { display: flex; gap: 8px; flex-wrap: wrap; }
.pg-img-chip {
  position: relative;
  width: 64px; height: 64px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--card-border);
}
.pg-img-chip img { width: 100%; height: 100%; object-fit: cover; }
.pg-img-x {
  position: absolute;
  top: 2px; right: 2px;
  width: 18px; height: 18px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}
/* 输入行 */
.pg-row { display: flex; gap: 10px; align-items: flex-end; }
.pg-row :deep(.el-textarea__inner) { min-height: 84px; max-height: 320px; }
.pg-btns { display: flex; flex-direction: column; gap: 8px; flex: none; }
.pg-btns .el-button { width: 60px; }
.pg-row .pg-btns .el-button:last-child { height: 42px; }
.pg-file { display: none; }
/* 会话行 */
.pg-sessions { display: flex; align-items: center; gap: 8px; }
.pg-sess-item { display: flex; align-items: center; gap: 10px; min-width: 260px; }
.pg-sess-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pg-sess-time { color: var(--text-faint, #888); font-size: 12px; flex: none; }
.pg-sess-del { color: var(--text-faint, #888); cursor: pointer; flex: none; }
.pg-sess-del:hover { color: var(--red); }
</style>
