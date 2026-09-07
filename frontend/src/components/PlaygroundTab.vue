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
        输入消息开始推理测试——流式回复，自动统计 TTFT / tokens/s / 总耗时
      </div>
      <div v-for="(m, i) in chat" :key="i" class="pg-msg" :class="m.role">
        <div class="pg-role">{{ m.role === 'user' ? '你' : '助手' }}</div>
        <div class="pg-content">
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
      <div class="pg-params">
        <el-slider v-model="temperature" :min="0" :max="2" :step="0.1" style="width:120px"
          :format-tooltip="v => '温度 ' + v" />
        <el-input-number v-model="maxTokens" :min="0" :max="32768" :step="256" size="small"
          controls-position="right" style="width:110px" placeholder="max_tokens" />
        <span class="pg-hint">0 = 不限制</span>
        <span class="toolbar-spacer" />
        <el-button v-if="chat.length" size="small" text @click="chat = []">清空</el-button>
      </div>
      <div class="pg-row">
        <el-input v-model="input" type="textarea" :rows="2" resize="none"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行" @keydown.enter.exact.prevent="send" />
        <el-button type="primary" :loading="streaming" :disabled="!input.trim()" @click="send">
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'

// Playground Tab（P1-1）：代理 llama-server 聊天（SSE 流式）+ TTFT/tokens/s 指标。
// hostId=mid（主监控 API 直接用 mid，无需 db_id）。
const props = defineProps({
  hostId: { type: String, required: true },
  active: { type: Boolean, default: false },
  hostLabel: { type: String, default: '' },
})

const info = ref({ endpoint: '', model: '', online: false })
const chat = ref([])
const input = ref('')
const temperature = ref(0.7)
const maxTokens = ref(0)
const streaming = ref(false)
const chatBox = ref(null)

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

async function send() {
  const text = input.value.trim()
  if (!text || streaming.value) return
  input.value = ''
  const asstMsg = { role: 'assistant', content: '', metrics: null, error: '' }
  chat.value.push({ role: 'user', content: text }, asstMsg)
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
        messages: chat.value
          .filter((m) => m.content)
          .map((m) => ({ role: m.role, content: m.content })),
        temperature: temperature.value,
        max_tokens: maxTokens.value,
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
  }
}

onMounted(loadInfo)
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
.pg-cursor { animation: pg-blink 1s infinite; }
@keyframes pg-blink { 50% { opacity: 0; } }
.pg-err { color: var(--red); }
.pg-muted { color: var(--text-faint); }
.pg-metrics { font-size: 11px; color: var(--text-dim); font-family: var(--font-mono, monospace); }
.pg-input { display: flex; flex-direction: column; gap: 8px; }
.pg-params { display: flex; align-items: center; gap: 12px; }
.pg-hint { font-size: 11px; color: var(--text-faint); }
.pg-row { display: flex; gap: 10px; align-items: flex-end; }
.pg-row .el-button { height: 55px; }
</style>
