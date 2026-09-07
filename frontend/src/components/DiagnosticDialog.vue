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

      <div v-if="blocks.length" class="diag-body">
        <template v-for="(b, i) in blocks" :key="i">
          <h2 v-if="b.type === 'title'" class="diag-title">{{ b.text }}</h2>
          <h3 v-else-if="b.type === 'h2'" class="diag-h2">{{ b.text }}</h3>
          <p v-else-if="b.type === 'p'" class="diag-p" v-html="inline(b.text)"></p>
          <ul v-else-if="b.type === 'list'" class="diag-list">
            <li v-for="(it, j) in b.items" :key="j" :class="{ sub: it.indent }" v-html="inline(it.text)"></li>
          </ul>
          <table v-else-if="b.type === 'table'" class="diag-table">
            <thead><tr><th v-for="(c, j) in b.head" :key="j">{{ c }}</th></tr></thead>
            <tbody>
              <tr v-for="(r, j) in b.body" :key="j"><td v-for="(c, k) in r" :key="k">{{ c }}</td></tr>
            </tbody>
          </table>
          <pre v-else-if="b.type === 'code'" class="diag-code">{{ b.lines.join('\n') }}</pre>
        </template>
      </div>
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

// 一键体检（P2-1）：聚合诊断报告，结构化展示（分节/表格/列表/代码块）+ 复制/下载 markdown。
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

// ---------------- 轻量 markdown 解析（仅覆盖体检报告用到的语法） ----------------
function parseMd(md) {
  const lines = (md || '').split('\n')
  const blocks = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }
    if (line.startsWith('# ')) { blocks.push({ type: 'title', text: line.slice(2) }); i++; continue }
    if (line.startsWith('## ')) { blocks.push({ type: 'h2', text: line.slice(3) }); i++; continue }
    if (line.trim() === '```') {
      const code = []
      i++
      while (i < lines.length && lines[i].trim() !== '```') { code.push(lines[i]); i++ }
      i++
      blocks.push({ type: 'code', lines: code })
      continue
    }
    if (line.trim().startsWith('|')) {
      const rows = []
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(lines[i]); i++ }
      const parse = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
      blocks.push({ type: 'table', head: parse(rows[0]), body: rows.slice(2).map(parse) })
      continue
    }
    if (/^\s*- /.test(line)) {
      const items = []
      while (i < lines.length && /^\s*- /.test(lines[i])) {
        items.push({ indent: lines[i].startsWith('  ') ? 1 : 0, text: lines[i].replace(/^\s*- /, '') })
        i++
      }
      blocks.push({ type: 'list', items })
      continue
    }
    blocks.push({ type: 'p', text: line })
    i++
  }
  return blocks
}
const blocks = computed(() => parseMd(markdown.value))

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
// 行内：**加粗** + [danger]/[warn] 等级着色
function inline(text) {
  let h = esc(text)
  h = h.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
  h = h.replace(/\[(danger|warn|ok|info)\]/g, (m, lv) => `<i class="lv-${lv}">[${lv}]</i>`)
  return h
}

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
.diag-summary { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.diag-body {
  max-height: 58vh;
  overflow: auto;
  background: var(--bg);
  border: 1px solid var(--card-border);
  border-radius: 8px;
  padding: 16px 18px;
}
.diag-title {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
}
.diag-h2 {
  margin: 18px 0 8px;
  padding-left: 9px;
  border-left: 3px solid var(--cyan);
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
}
.diag-h2:first-child { margin-top: 0; }
.diag-p { margin: 4px 0; font-size: 12.5px; color: var(--text-dim); }
.diag-list { margin: 4px 0; padding-left: 18px; }
.diag-list li { font-size: 12.5px; line-height: 1.7; color: var(--text); }
.diag-list li.sub { padding-left: 14px; color: var(--text-dim); }
.diag-list li::marker { color: var(--text-faint); }
.diag-table {
  width: 100%;
  border-collapse: collapse;
  margin: 6px 0;
  font-size: 12px;
}
.diag-table th, .diag-table td {
  border: 1px solid var(--card-border);
  padding: 5px 10px;
  text-align: left;
}
.diag-table th {
  background: color-mix(in srgb, var(--cyan) 8%, transparent);
  color: var(--text);
  font-weight: 600;
  white-space: nowrap;
}
.diag-table td { color: var(--text-dim); font-variant-numeric: tabular-nums; }
.diag-table tbody tr:nth-child(even) { background: color-mix(in srgb, var(--text) 3%, transparent); }
.diag-code {
  margin: 6px 0;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  font-size: 11.5px;
  line-height: 1.6;
  font-family: var(--font-mono, monospace);
  color: var(--text-dim);
  white-space: pre-wrap;
  word-break: break-all;
}
:deep(.lv-danger) { color: var(--red); font-style: normal; font-weight: 600; }
:deep(.lv-warn) { color: var(--amber); font-style: normal; font-weight: 600; }
:deep(.lv-ok) { color: var(--green); font-style: normal; }
:deep(.lv-info) { color: var(--text-dim); font-style: normal; }
</style>
