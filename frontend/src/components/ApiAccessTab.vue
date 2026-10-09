<template>
  <div class="tab-pane api-scope">
    <div class="api-toolbar">
      <span class="api-title">API 接入</span>
      <span class="api-sub">推理服务兼容 OpenAI 接口，其他应用可直接调用</span>
      <span class="api-spacer" />
      <el-button size="small" @click="loadHost">刷新</el-button>
    </div>
    <div v-if="!baseUrl" class="api-empty">
      暂无法获取服务监听地址（未配置日志采集或服务未启动），请先到「模型」页确认服务状态
    </div>
    <div v-else class="api-grid">
      <div class="api-card">
        <div class="api-card-title">连接信息</div>
        <div class="api-row"><span class="api-k">Base URL</span><code class="api-v">{{ baseUrl }}</code><el-button link size="small" @click="copy(baseUrl)">复制</el-button></div>
        <div class="api-row"><span class="api-k">模型名</span><code class="api-v">{{ modelName || '（未知，可用 /v1/models 查询）' }}</code><el-button v-if="modelName" link size="small" @click="copy(modelName)">复制</el-button></div>
        <div class="api-row"><span class="api-k">补全地址</span><code class="api-v">{{ fullUrl }}</code><el-button link size="small" @click="copy(fullUrl)">复制</el-button></div>
      </div>
      <div class="api-card">
        <div class="api-card-title">curl 示例<el-button link size="small" @click="copy(curlExample)">复制</el-button></div>
        <pre class="api-code">{{ curlExample }}</pre>
      </div>
      <div class="api-card">
        <div class="api-card-title">Python（openai）<el-button link size="small" @click="copy(pyExample)">复制</el-button></div>
        <pre class="api-code">{{ pyExample }}</pre>
      </div>
      <div class="api-card">
        <div class="api-card-title">JavaScript（fetch）<el-button link size="small" @click="copy(jsExample)">复制</el-button></div>
        <pre class="api-code">{{ jsExample }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { api } from '../api'

const props = defineProps({
  hostId: String,
  active: Boolean,
  hostLabel: { type: String, default: '' },
  snap: { type: Object, default: null },
})

const hostInfo = ref(null)
async function loadHost() {
  try {
    const hosts = await api.hosts()
    hostInfo.value = hosts.find((x) => x.id === props.hostId) || null
  } catch { hostInfo.value = null }
}

// 监听地址来自日志启动行 "listening on http://0.0.0.0:8080"
const listen = computed(() => (props.snap?.llama?.log?.boot || {}).listening || '')
const port = computed(() => {
  const m = listen.value.match(/:(\d+)/)
  return m ? m[1] : ''
})
const baseUrl = computed(() => {
  const ip = hostInfo.value?.host || ''
  return (ip && port.value) ? `http://${ip}:${port.value}/v1` : ''
})
const modelName = computed(() => {
  const p = props.snap?.llama?.model?.path || ''
  return p ? p.split('/').pop() : ''
})
const fullUrl = computed(() => `${baseUrl.value}/chat/completions`)
const curlExample = computed(() =>
  `curl ${fullUrl.value} \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "model": "${modelName.value || '模型名'}",\n    "messages": [{"role": "user", "content": "你好"}],\n    "stream": false\n  }'`)
const pyExample = computed(() =>
  `from openai import OpenAI\n\nclient = OpenAI(base_url="${baseUrl.value}", api_key="not-needed")\nresp = client.chat.completions.create(\n    model="${modelName.value || '模型名'}",\n    messages=[{"role": "user", "content": "你好"}],\n)\nprint(resp.choices[0].message.content)`)
const jsExample = computed(() =>
  `const resp = await fetch("${fullUrl.value}", {\n  method: "POST",\n  headers: { "Content-Type": "application/json" },\n  body: JSON.stringify({\n    model: "${modelName.value || '模型名'}",\n    messages: [{ role: "user", content: "你好" }],\n  }),\n})\nconst data = await resp.json()\nconsole.log(data.choices[0].message.content)`)

async function copy(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      ElMessage.success('已复制')
      return
    }
  } catch { /* 继续回退 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const okc = document.execCommand('copy')
    ta.remove()
    if (okc) { ElMessage.success('已复制'); return }
  } catch { /* ignore */ }
  ElMessage.warning('复制失败，请手动选择复制')
}

onMounted(loadHost)
</script>

<style scoped>
.api-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.api-title { font-size: 14px; font-weight: 600; color: var(--text, #eee); }
.api-sub { font-size: 12px; color: var(--lc-text-muted, #999); }
.api-spacer { flex: 1; }
.api-empty { padding: 40px 0; text-align: center; color: var(--lc-text-muted, #999); font-size: 13px; border: 1px dashed var(--lc-border, #444); border-radius: 10px; }
.api-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 12px; }
.api-card { border: 1px solid var(--lc-border, #333); border-radius: 10px; padding: 12px 14px; background: var(--lc-bg-2, #1a1e2a); }
.api-card-title { font-size: 13px; font-weight: 600; color: var(--text, #ddd); margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
.api-row { display: flex; align-items: center; gap: 8px; padding: 5px 0; font-size: 12px; }
.api-k { width: 64px; flex-shrink: 0; color: var(--lc-text-muted, #999); }
.api-v { flex: 1; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #7aa2f7; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.api-code { margin: 0; padding: 10px; background: #12141c; border-radius: 8px; font: 12px/1.6 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #c8d0e0; overflow-x: auto; white-space: pre; }
</style>
