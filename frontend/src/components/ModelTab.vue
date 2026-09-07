<template>
  <div class="tab-pane ctl-scope">
    <div class="ctl-toolbar">
      <span class="mt-title">模型清单</span>
      <span v-if="memory" class="mt-mem">
        <template v-for="g in memory.gpus" :key="g.index">
          <el-tag size="small" effect="plain" class="mt-mem-tag">
            {{ g.name || `GPU${g.index}` }} 空闲 {{ fmtGib(g.free_mb) }} / {{ fmtGib(g.total_mb) }}
          </el-tag>
        </template>
        <el-tag v-if="memory.ram && memory.ram.total_mb" size="small" effect="plain" class="mt-mem-tag">
          内存可用 {{ fmtGib(memory.ram.available_mb) }} / {{ fmtGib(memory.ram.total_mb) }}
        </el-tag>
      </span>
      <span v-else class="mt-mem mt-mem-none">无内存数据（监控未启用且探测失败）</span>
      <span class="toolbar-spacer" />
      <el-input v-model="keyword" size="small" class="mt-search" placeholder="搜索名称 / 路径" clearable>
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select v-model="minMb" size="small" class="mt-size-filter" @change="loadModels">
        <el-option label="全部大小" :value="0" />
        <el-option label="≥ 50 MB" :value="50" />
        <el-option label="≥ 1 GB" :value="1024" />
        <el-option label="≥ 5 GB" :value="5120" />
      </el-select>
      <el-button type="primary" size="small" :loading="loading" @click="loadModels">
        <el-icon style="margin-right:4px"><Refresh /></el-icon>刷新
      </el-button>
    </div>

    <el-empty v-if="!loading && !models.length" description="浏览路径下未扫描到 .gguf 模型文件（可在【主机管理】调整浏览路径）" :image-size="60" />
    <el-empty v-else-if="!filteredModels.length" description="没有符合搜索条件的模型" :image-size="60">
      <el-button type="primary" plain size="small" @click="keyword = ''">清空搜索</el-button>
    </el-empty>
    <el-table v-else :data="filteredModels" v-loading="loading" size="small" stripe>
      <el-table-column label="模型" min-width="260">
        <template #default="{ row }">
          <div class="mt-name">{{ row.name }}</div>
          <div class="mt-path">{{ row.path }}</div>
        </template>
      </el-table-column>
      <el-table-column label="大小" width="90" align="right">
        <template #default="{ row }">{{ fmtBytes(row.size) }}</template>
      </el-table-column>
      <el-table-column label="量化" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.quant" size="small" effect="plain">{{ row.quant }}</el-tag>
          <span v-else class="mt-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="mmproj" min-width="180">
        <template #default="{ row }">
          <template v-if="row.mmproj">
            <div class="mt-name">{{ row.mmproj_name }}</div>
            <div class="mt-path">{{ row.mmproj }}</div>
          </template>
          <span v-else class="mt-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="占用服务" min-width="150">
        <template #default="{ row }">
          <el-tag v-for="s in row.used_by" :key="s" size="small" type="success" effect="plain" class="mt-svc-tag">{{ s }}</el-tag>
          <span v-if="!row.used_by.length" class="mt-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="适配预估" min-width="220">
        <template #default="{ row }">
          <el-tag :type="fitType(row.fit.level)" size="small">{{ fitLabel(row.fit.level) }}</el-tag>
          <div class="mt-fit-note">{{ row.fit.note }}</div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="openSwitch(row)">
            <el-icon style="margin-right:4px"><SwitchButton /></el-icon>切换
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 切换模型对话框 -->
    <el-dialog v-model="switchVisible" title="切换模型" width="560px" :close-on-click-modal="false">
      <el-form label-width="90px" size="small">
        <el-form-item label="目标服务" required>
          <el-select v-model="switchForm.service" placeholder="选择要切换的服务" style="width:100%">
            <el-option v-for="s in services" :key="s.name" :label="s.name" :value="s.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="当前模型">
          <span class="mt-path">{{ currentModel || '（未解析到 -m 参数）' }}</span>
        </el-form-item>
        <el-form-item label="新模型" required>
          <el-input v-model="switchForm.model" readonly />
        </el-form-item>
        <el-form-item label="mmproj">
          <el-input v-model="switchForm.mmproj" placeholder="留空则移除 --mmproj 参数" clearable />
        </el-form-item>
        <el-alert type="warning" :closable="false" show-icon
          title="切换将备份原配置、修改 ExecStart 的 -m/--mmproj 并重启服务，期间推理中断。" />
      </el-form>
      <template #footer>
        <el-button size="small" @click="switchVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="switchLoading" @click="doSwitch">确认切换</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api/client'
import { api } from '../api'

// 模型 Tab（P0-2）：模型清单 + 显存适配预估 + 一键切换。
// 与 ServiceTab 同构：hostId=mid（路由用），内部解析 db_id 调管理 API。
const props = defineProps({
  hostId: { type: String, required: true },
  active: { type: Boolean, default: false },
  hostLabel: { type: String, default: '' },
})

const ctlDbId = ref(0)
const minMb = ref(50)  // 默认隐藏 vocab 等小文件
const keyword = ref('')
const models = ref([])
const filteredModels = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return models.value
  return models.value.filter((m) => m.name.toLowerCase().includes(kw) || m.path.toLowerCase().includes(kw))
})
const services = ref([])
const memory = ref(null)
const loading = ref(false)

const switchVisible = ref(false)
const switchLoading = ref(false)
const switchForm = ref({ service: '', model: '', mmproj: '' })

function fmtGib(mb) {
  if (mb == null) return '—'
  return (mb / 1024).toFixed(2) + ' GiB'
}
function fmtBytes(bytes) {
  if (bytes == null) return '—'
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GiB'
}
function fitType(level) {
  return { ok: 'success', tight: 'warning', no: 'danger' }[level] || 'info'
}
function fitLabel(level) {
  return { ok: '装得下', tight: '紧张', no: '装不下', unknown: '未知' }[level] || level
}

// 从服务 ExecStart 提取当前 -m 值（简单解析，供对话框展示）
function currentModelOf(service) {
  const es = service.execstart || ''
  const m = es.match(/(?:^|\s)(?:-m|--model)\s+('([^']+)'|"([^"]+)"|(\S+))/)
  if (!m) return ''
  return m[2] ?? m[3] ?? m[4] ?? ''
}
const currentModel = computed(() => {
  const s = services.value.find((x) => x.name === switchForm.value.service)
  return s ? currentModelOf(s) : ''
})

async function loadModels() {
  if (!ctlDbId.value) return
  loading.value = true
  try {
    const data = await http.get(`/hosts/${ctlDbId.value}/models`, { params: { min_mb: minMb.value } })
    models.value = data.models || []
    services.value = data.services || []
    memory.value = data.memory
  } catch (e) { /* client 已提示 */ } finally {
    loading.value = false
  }
}

function openSwitch(row) {
  switchForm.value = {
    service: (row.used_by && row.used_by[0]) || (services.value[0] ? services.value[0].name : ''),
    model: row.path,
    mmproj: row.mmproj || '',
  }
  switchVisible.value = true
}

async function doSwitch() {
  if (!switchForm.value.service) {
    ElMessage.warning('请选择目标服务')
    return
  }
  switchLoading.value = true
  try {
    const res = await http.post(
      `/services/${encodeURIComponent(switchForm.value.service)}/switch-model`,
      { model_path: switchForm.value.model, mmproj_path: switchForm.value.mmproj },
      { params: { host_id: ctlDbId.value } },
    )
    switchVisible.value = false
    if (res.restart_error) ElMessage.warning(`配置已切换，但重启失败：${res.restart_error}`)
    else ElMessage.success(`已切换到 ${switchForm.value.model} 并重启服务`)
    loadModels()
  } catch (e) { /* client 已提示 */ } finally {
    switchLoading.value = false
  }
}

onMounted(async () => {
  try {
    const hosts = await api.hosts()
    const h = hosts.find((x) => x.id === props.hostId)
    if (h) ctlDbId.value = h.db_id
  } catch (e) { /* 忽略，loadModels 会静默失败 */ }
  loadModels()
})
</script>

<style scoped>
.mt-title { font-weight: 600; }
.mt-size-filter { width: 110px; }
.mt-search { width: 200px; }
.mt-mem { display: inline-flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.mt-mem-tag { font-family: var(--font-mono, monospace); }
.mt-mem-none { color: var(--text-dim, #888); font-size: 12px; }
.mt-name { font-weight: 500; }
.mt-path {
  font-size: 11px;
  color: var(--text-dim, #888);
  font-family: var(--font-mono, monospace);
  word-break: break-all;
}
.mt-muted { color: var(--text-dim, #888); }
.mt-svc-tag { margin-right: 4px; }
.mt-fit-note { font-size: 11px; color: var(--text-dim, #888); margin-top: 3px; }
</style>
