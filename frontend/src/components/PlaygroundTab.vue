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
      <div v-if="!chat.length" class="pg-empty">
        输入消息开始推理测试——流式回复，自动统计 TTFT / tokens/s / 总耗时；支持图片（需模型带 mmproj）
      </div>
      <div v-for="(m, i) in chat" :key="i" class="pg-msg" :class="m.role">
        <div class="pg-role">{{ m.role === 'user' ? '你' : '助手' }}</div>
        <div class="pg-content">
          <template v-if="m.images && m.images.length">
            <img v-for="(src, j) in m.images" :key="j" :src="src" class="pg-img" />
          </template>
          <span v-if="m.content">{{ m.content }}</span>
          <span v-else-if="streaming && i === chat.length - 1" class="pg-cursor">▍</span>
          <span v-else-if="m.error" class="pg-err">{{ m.error }}</span>
          <span v-else class="pg-muted">（无内容）</span>
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
        <el-input ref="taRef" v-model="input" type="textarea" :rows="3" resize="none"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行；可添加图片（需模型带 mmproj）"
          @keydown.enter.exact.prevent="send" />
        <div class="pg-btns">
          <el-button class="pg-attach" title="添加图片" @click="fileRef && fileRef.click()">
            <el-icon :size="18"><Picture /></el-icon>
          </el-button>
          <el-button type="primary" :loading="streaming" :disabled="!input.trim() && !pendingImages.length" @click="send">
            发送
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

// Playground Tab（P1-1）：代理 llama-server 聊天（SSE 流式）+ TTFT/tokens/s 指标。
// 增强：图片消息（mmproj）、采样参数（top_k/top_p/min_p/重复惩罚/max_tokens）、
// 会话历史（localStorage 按主机保存，可新建/切换/删除）。
// hostId=mid（主监控 API 直接用 mid，无需 db_id）。
const props = defineProps({
  hostId: { type: String, required: true },
  active: { type: Boolean, default: false },
  hostLabel: { type: String, default: '' },
})

const info = ref({ endpoint: '', model: '', online: false })
const chat = ref([])
const input = ref('')
const streaming = ref(false)
const chatBox = ref(null)
const taRef = ref(null)
const fileRef = ref(null)
const pendingImages = ref([])

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

// ---------------- 会话历史（localStorage 按主机保存） ----------------
const SKEY = `llamalens.pg.sessions.${props.hostId}`
const MAX_SESSIONS = 20
const store = reactive({ current: '', sessions: {} })

function newSessionId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}
function blankSession() {
  return { title: '新会话', created: Date.now(), messages: [] }
}
function loadStore() {
  try {
    const saved = JSON.parse(localStorage.getItem(SKEY) || 'null')
    if (saved && saved.sessions && Object.keys(saved.sessions).length) {
      store.current = saved.current && saved.sessions[saved.current] ? saved.current : Object.keys(saved.sessions)[0]
      store.sessions = saved.sessions
    }
  } catch { /* 忽略损坏的历史 */ }
  if (!store.sessions[store.current]) {
    store.current = newSessionId()
    store.sessions[store.current] = blankSession()
  }
  chat.value = (store.sessions[store.current].messages || []).map((m) => ({ ...m, images: m.images ? [...m.images] : [] }))
}
function persist() {
  // 同步当前会话到 store
  store.sessions[store.current] = {
    title: store.sessions[store.current].title,
    created: store.sessions[store.current].created,
    messages: chat.value,
  }
  // 会话数上限：丢弃最旧的非当前会话
  const ids = Object.keys(store.sessions).filter((i) => i !== store.current)
    .sort((a, b) => store.sessions[a].created - store.sessions[b].created)
  while (ids.length && Object.keys(store.sessions).length > MAX_SESSIONS) {
    delete store.sessions[ids.shift()]
  }
  try {
    localStorage.setItem(SKEY, JSON.stringify(store))
  } catch {
    // 超配额（多为图片）：逐个丢弃最旧会话后重试
    for (const id of ids) {
      delete store.sessions[id]
      try { localStorage.setItem(SKEY, JSON.stringify(store)); return } catch { /* 继续丢 */ }
    }
  }
}
const sessionList = computed(() =>
  Object.entries(store.sessions)
    .map(([id, ss]) => ({ id, title: ss.title, created: ss.created }))
    .sort((a, b) => b.created - a.created))

function newSession() {
  if (streaming.value) { ElMessage.warning('等待当前回复完成'); return }
  persist()
  store.current = newSessionId()
  store.sessions[store.current] = blankSession()
  chat.value = []
  persist()
}
function loadSession(id) {
  if (streaming.value) { ElMessage.warning('等待当前回复完成'); return }
  if (id === store.current) return
  persist()
  store.current = id
  chat.value = (store.sessions[id].messages || []).map((m) => ({ ...m, images: m.images ? [...m.images] : [] }))
  persist()
  scrollBottom()
}
function deleteSession(id) {
  if (streaming.value) { ElMessage.warning('等待当前回复完成'); return }
  delete store.sessions[id]
  if (id === store.current) {
    const rest = Object.keys(store.sessions)
    if (rest.length) {
      store.current = rest.sort((a, b) => store.sessions[b].created - store.sessions[a].created)[0]
      chat.value = (store.sessions[store.current].messages || []).map((m) => ({ ...m, images: m.images ? [...m.images] : [] }))
    } else {
      store.current = newSessionId()
      store.sessions[store.current] = blankSession()
      chat.value = []
    }
  }
  persist()
  scrollBottom()
}
function clearCurrent() {
  if (streaming.value) { ElMessage.warning('等待当前回复完成'); return }
  chat.value = []
  store.sessions[store.current] = { ...store.sessions[store.current], title: '新会话', messages: [] }
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
async function onPickImages(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''
  for (const f of files) {
    if (!f.type || !f.type.startsWith('image/')) continue
    if (pendingImages.value.length >= MAX_IMAGES) { ElMessage.warning(`最多 ${MAX_IMAGES} 张图片`); break }
    try {
      pendingImages.value.push(await downscale(f))
    } catch {
      ElMessage.error(`图片读取失败：${f.name}`)
    }
  }
}

// ---------------- 输入框自适应高度 ----------------
function autoGrow() {
  const ta = taRef.value && taRef.value.textarea
  if (!ta) return
  ta.style.height = 'auto'
  ta.style.height = Math.min(Math.max(ta.scrollHeight, 84), 240) + 'px'
}
watch(input, autoGrow)

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
  } else if (data.choices && data.choices[0] && data.choices[0].delta && data.choices[0].delta.content) {
    asstMsg.content += data.choices[0].delta.content
    scrollBottom()
  }
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

async function send() {
  const text = input.value.trim()
  if ((!text && !pendingImages.value.length) || streaming.value) return
  const images = [...pendingImages.value]
  input.value = ''
  pendingImages.value = []
  const asstMsg = { role: 'assistant', content: '', metrics: null, error: '' }
  chat.value.push({ role: 'user', content: text, images }, asstMsg)
  // 会话标题：取首条用户消息
  const sess = store.sessions[store.current]
  if (sess && sess.title === '新会话') {
    sess.title = (text || '（图片）').slice(0, 24)
  }
  streaming.value = true
  scrollBottom()
  try {
    const token = localStorage.getItem('llama_token')
    const resp = await fetch(`/api/hosts/${encodeURIComponent(props.hostId)}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
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
        handleFrame(frame, asstMsg)
      }
    }
  } catch (e) {
    asstMsg.error = String((e && e.message) || e)
  } finally {
    streaming.value = false
    scrollBottom()
    persist()
  }
}

onMounted(() => {
  loadInfo()
  loadStore()
  autoGrow()
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
  background: var(--bg);
}
.pg-msg.user .pg-content { background: color-mix(in srgb, var(--cyan) 12%, var(--bg)); }
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
.pg-row :deep(.el-textarea__inner) { min-height: 84px; }
.pg-btns { display: flex; flex-direction: column; gap: 8px; flex: none; }
.pg-attach { width: 42px; }
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
