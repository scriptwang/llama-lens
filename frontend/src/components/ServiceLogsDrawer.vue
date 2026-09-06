<template>
  <el-drawer
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    :title="`服务日志 · ${service ? service.name : ''}`"
    size="720px"
  >
    <div class="log-toolbar">
      <el-select v-model="lineCount" size="small" style="width: 130px" @change="load">
        <el-option :value="100" label="最近 100 行" />
        <el-option :value="200" label="最近 200 行" />
        <el-option :value="500" label="最近 500 行" />
      </el-select>
      <el-switch v-model="autoRefresh" style="margin-left: 14px" />
      <span class="log-auto-label">自动刷新（5s）</span>
      <el-button size="small" :loading="loading" style="margin-left: 14px" @click="load">
        <el-icon style="margin-right: 3px"><Refresh /></el-icon>刷新
      </el-button>
      <span v-if="updatedAt" class="log-updated">{{ updatedAt }}</span>
    </div>
    <pre ref="bodyRef" class="log-body">{{ lines.length ? lines.join('\n') : loading ? '加载中…' : '（无日志输出）' }}</pre>
  </el-drawer>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import http from '../api/client'

const props = defineProps({
  modelValue: Boolean,
  service: { type: Object, default: null },
  hostId: { type: Number, default: 0 },
})
defineEmits(['update:modelValue'])

const lines = ref([])
const loading = ref(false)
const lineCount = ref(200)
const autoRefresh = ref(true)
const updatedAt = ref('')
const bodyRef = ref(null)
let timer = null

async function load() {
  if (!props.service || !props.hostId) return
  loading.value = true
  try {
    const data = await http.get(`/metrics/services/${props.service.name}/logs`, {
      params: { host_id: props.hostId, lines: lineCount.value },
    })
    lines.value = data.lines || []
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    scrollToBottom()
  } finally {
    loading.value = false
  }
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    if (bodyRef.value) bodyRef.value.scrollTop = bodyRef.value.scrollHeight
  })
}

function startTimer() {
  stopTimer()
  if (autoRefresh.value) timer = setInterval(load, 5000)
}

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(autoRefresh, (v) => (v ? startTimer() : stopTimer()))

watch(
  () => props.modelValue,
  (v) => {
    if (v) {
      lines.value = []
      updatedAt.value = ''
      load()
      startTimer()
    } else {
      stopTimer()
    }
  },
)

watch(
  () => props.service?.name,
  () => {
    if (props.modelValue) {
      lines.value = []
      load()
    }
  },
)

onBeforeUnmount(stopTimer)
</script>

<style scoped>
.log-toolbar {
  display: flex; align-items: center; margin-bottom: 10px;
}
.log-auto-label { margin-left: 8px; font-size: 12px; color: var(--lc-text-muted); }
.log-updated { margin-left: auto; font-size: 12px; color: var(--lc-text-muted); }
.log-body {
  margin: 0; padding: 12px 14px;
  height: calc(100% - 52px);
  overflow: auto;
  background: #0d1117;
  color: #c9d1d9;
  border-radius: 10px;
  border: 1px solid var(--lc-border);
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
