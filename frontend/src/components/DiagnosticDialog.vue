<template>
  <el-dialog v-model="visible" title="一键体检" width="760px" top="6vh" append-to-body :close-on-click-modal="false">
    <div v-loading="loading" class="diag">
      <div v-if="summary" class="diag-summary">
        <el-tag :type="summary.llama_online ? 'success' : 'danger'" size="small">
          llama {{ summary.llama_online ? '在线' : '离线' }}
        </el-tag>
        <el-tag :type="summary.ssh_ok ? 'success' : 'danger'" size="small">
          SSH {{ summary.ssh_ok ? '已连接' : '断开' }}
        </el-tag>
        <el-tag :type="summary.alerts ? 'warning' : 'info'" size="small">告警 {{ summary.alerts }}</el-tag>
        <el-tag type="info" size="small">服务 {{ summary.services }}</el-tag>
        <el-tag type="info" size="small">事件 {{ summary.events }}</el-tag>
        <el-tag :type="summary.error_logs ? 'danger' : 'info'" size="small">错误日志 {{ summary.error_logs }}</el-tag>
      </div>
      <pre v-if="markdown" class="diag-md">{{ markdown }}</pre>
      <el-empty v-else-if="!loading" description="生成失败" :image-size="50" />
    </div>
    <template #footer>
      <el-button size="small" @click="visible = false">关闭</el-button>
      <el-button size="small" @click="copyMd">
        <el-icon style="margin-right:4px"><CopyDocument /></el-icon>复制
      </el-button>
      <el-button size="small" type="primary" @click="downloadMd">
        <el-icon style="margin-right:4px"><Document /></el-icon>下载 Markdown
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api/client'
import { api } from '../api'

// 一键体检（P2-1）：聚合诊断报告，页面查看 + 复制/下载 markdown。
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  hostId: { type: String, required: true },  // mid
  hostLabel: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})
const loading = ref(false)
const markdown = ref('')
const summary = ref(null)

watch(() => props.modelValue, async (open) => {
  if (!open) return
  loading.value = true
  markdown.value = ''
  summary.value = null
  try {
    const hosts = await api.hosts()
    const h = hosts.find((x) => x.id === props.hostId)
    if (!h) throw new Error('未知主机')
    const data = await http.get(`/hosts/${h.db_id}/diagnostic`)
    markdown.value = data.markdown
    summary.value = data.summary
  } catch (e) { /* client 已提示 */ } finally {
    loading.value = false
  }
})

function copyMd() {
  if (!markdown.value) return
  navigator.clipboard.writeText(markdown.value)
    .then(() => ElMessage.success('已复制'))
    .catch(() => ElMessage.error('复制失败'))
}

function downloadMd() {
  if (!markdown.value) return
  const blob = new Blob([markdown.value], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `diagnostic-${props.hostLabel || props.hostId}-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}.md`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.diag { min-height: 200px; }
.diag-summary { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.diag-md {
  max-height: 55vh;
  overflow: auto;
  background: var(--bg);
  border: 1px solid var(--card-border);
  border-radius: 8px;
  padding: 14px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono, monospace);
}
</style>
