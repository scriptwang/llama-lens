<template>
  <el-dialog
    :model-value="true"
    @update:model-value="$emit('close')"
    title="选择服务器文件"
    width="760px"
    append-to-body
    destroy-on-close
   :teleported="false">
    <div v-if="roots.length" class="fb-roots">
      <span class="fb-roots-label">快捷目录：</span>
      <el-tag v-for="r in roots" :key="r" class="fb-root" effect="plain" @click="navigate(r)">{{ r }}</el-tag>
    </div>
    <div class="fb-pathbar">
      <el-input v-model="pathInput" size="small" @keyup.enter="navigate(pathInput)">
        <template #prepend>
          <el-button :disabled="!parent || parent === '/'" @click="navigate(parent)" title="上一级">
            <el-icon><ArrowUp /></el-icon>
          </el-button>
        </template>
      </el-input>
      <el-input v-model="pattern" size="small" clearable placeholder="过滤，如 *.gguf" style="width: 160px" @keyup.enter="load" />
      <el-button size="small" :loading="loading" @click="load"><el-icon><Refresh /></el-icon></el-button>
    </div>
    <el-table
      :data="entries"
      size="small"
      height="360"
      highlight-current-row
      v-loading="loading"
      @current-change="onSelect"
      @row-dblclick="onRowDblClick"
    >
      <el-table-column label="名称" min-width="260">
        <template #default="{ row }">
          <el-icon style="vertical-align: -2px; margin-right: 4px" :class="row.is_dir ? 'fb-dir' : 'fb-file'">
            <Folder v-if="row.is_dir" /><Document v-else />
          </el-icon>
          {{ row.name }}
        </template>
      </el-table-column>
      <el-table-column label="大小" width="110">
        <template #default="{ row }">{{ row.is_dir ? '—' : fmtSize(row.size) }}</template>
      </el-table-column>
    </el-table>
    <div class="fb-selected" v-if="selected && !selected.is_dir">已选：{{ selected.path }}</div>
    <div v-else-if="!loading && !entries.length && pattern" class="fb-selected fb-hint">
      当前目录没有匹配「{{ pattern }}」的文件：可清除过滤，或进入子目录继续找
    </div>
    <div v-else class="fb-selected fb-hint">提示：单击选中文件，双击进入目录</div>
    <template #footer>
      <el-button @click="$emit('close')">取消</el-button>
      <el-button type="primary" :disabled="!selected || selected.is_dir" @click="confirm">选择</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import http from '../api/client'

const props = defineProps({
  hostId: { type: Number, required: true },
  roots: { type: Array, default: () => [] },
  initialPath: { type: String, default: '' },
  initialPattern: { type: String, default: '' },
})
const emit = defineEmits(['select', 'close'])

const pathInput = ref('/')
const pattern = ref(props.initialPattern || '')
const entries = ref([])
const parent = ref('/')
const selected = ref(null)
const loading = ref(false)

function startPath() {
  const p = (props.initialPath || '').replace(/\/+$/, '')
  if (p && p !== '/') {
    const last = p.split('/').pop() || ''
    if (last.includes('.')) return p.slice(0, p.lastIndexOf('/')) || '/'
    return p
  }
  return props.roots[0] || '/'
}

async function load() {
  if (!props.hostId) return
  loading.value = true
  selected.value = null
  try {
    const data = await http.get(`/hosts/${props.hostId}/fs`, {
      params: { path: pathInput.value || '/', pattern: pattern.value || '' },
    })
    entries.value = data.entries
    parent.value = data.parent
    pathInput.value = data.path
  } catch (e) {
    entries.value = []
    // 路径可能是文件（或不存在）：自动回退到上级目录重试
    const p = (pathInput.value || '').replace(/\/+$/, '')
    const par = p === '/' ? '' : (p.split('/').slice(0, -1).join('/') || '/')
    if (par && par !== p) {
      pathInput.value = par
      load()
    }
  } finally {
    loading.value = false
  }
}

function navigate(path) {
  if (!path) return
  pathInput.value = path
  load()
}

function onSelect(row) {
  selected.value = row
}

function onRowDblClick(row) {
  if (row && row.is_dir) navigate(row.path)
}

function confirm() {
  if (selected.value && !selected.value.is_dir) emit('select', selected.value.path)
}

function fmtSize(n) {
  if (n === 0) return '0 B'
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB'
}

onMounted(() => {
  pathInput.value = startPath()
  load()
})
</script>

<style scoped>
.fb-roots { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.fb-roots-label { font-size: 12px; color: var(--lc-text-muted); }
.fb-root { cursor: pointer; }
.fb-root:hover { opacity: 0.8; }
.fb-pathbar { display: flex; gap: 8px; margin-bottom: 10px; }
.fb-pathbar .el-input:first-child { flex: 1; }
.fb-selected {
  margin-top: 10px;
  font-size: 12px;
  color: var(--lc-text-secondary);
  font-family: ui-monospace, Menlo, Consolas, monospace;
  word-break: break-all;
}
.fb-hint { color: var(--lc-text-muted); font-family: inherit; }
.fb-dir { color: var(--lc-primary); }
.fb-file { color: var(--lc-text-muted); }
:deep(.el-table__row) { cursor: pointer; }
</style>
