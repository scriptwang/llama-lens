<template>
  <div class="tab-pane ctl-scope">
    <div class="ctl-toolbar">
      <el-input v-model="ctlFilterKeyword" class="fb-keyword" placeholder="搜索服务名" clearable size="small">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select v-model="ctlFilterStatus" class="fb-status" size="small">
        <el-option label="全部状态" value="all" />
        <el-option label="运行中" value="active" />
        <el-option label="非已停止" value="not_inactive" />
        <el-option label="已停止" value="inactive" />
        <el-option label="异常" value="failed" />
      </el-select>
      <el-select v-model="ctlFilterEnabled" class="fb-enabled" size="small">
        <el-option label="全部自启" value="all" />
        <el-option label="开机自启" value="enabled" />
        <el-option label="未自启" value="disabled" />
      </el-select>
      <span v-if="ctlHasFilter" class="fb-count">{{ ctlFilteredServices.length }}/{{ ctlServices.length }}</span>
      <el-button v-if="ctlHasFilter" text size="small" class="fb-clear" @click="clearCtlFilters">清空</el-button>
      <span class="toolbar-spacer" />
      <el-button type="primary" size="small" @click="newServiceVisible = true">
        <el-icon style="margin-right:4px"><Plus /></el-icon>新建服务
      </el-button>
      <el-button size="small" :loading="ctlScanning" @click="ctlRefresh">
        <el-icon style="margin-right:4px"><Refresh /></el-icon>刷新
      </el-button>
      <el-button size="small" :loading="ctlScanning" @click="ctlScan">
        <el-icon style="margin-right:4px"><Refresh /></el-icon>扫描服务
      </el-button>
      <el-button size="small" @click="ctlRuleDialog = true">扫描规则</el-button>
      <el-button size="small" @click="ctlLogsDrawer = true">操作日志</el-button>
    </div>
    <MetricsPanel :data="ctlMetrics" :error="ctlMetricsError" :services="ctlServices" :host-label="hostLabel" />
    <el-empty v-if="!ctlServices.length" description="未扫描到匹配的服务，可检查【扫描规则】或【新建服务】" :image-size="60" />
    <el-empty v-else-if="!ctlFilteredServices.length" description="没有符合当前筛选条件的服务" :image-size="60">
      <el-button type="primary" plain size="small" @click="clearCtlFilters">清空筛选</el-button>
    </el-empty>
    <el-row :gutter="16" v-else>
      <el-col :xs="24" :sm="12" :lg="8" :xl="6" v-for="s in ctlFilteredServices" :key="s.name">
        <ServiceCard
          :service="s"
          :metrics="ctlMetrics ? ctlMetrics.services : null"
          :action-loading="ctlActionLoading[s.name] || ''"
          @action="onCtlAction"
          @edit="openCtlConfig"
          @restore="onCtlRestore"
          @logs="openCtlLogs"
          @duplicate="openCtlDup"
          @rename="onCtlRename"
          @delete="onCtlDelete"
        />
      </el-col>
    </el-row>
    <ConfigDialog v-model="ctlConfigVisible" :service="ctlActiveService" @saved="ctlScan" />
    <NewServiceDialog v-model="newServiceVisible" @created="ctlScan" />
    <DuplicateServiceDialog v-model="ctlDupVisible" :service="ctlDupService" @created="ctlScan" />
    <ServiceLogsDrawer v-model="ctlSvcLogsVisible" :service="ctlActiveService" :host-id="ctlDbId" />
    <RuleEditor v-model="ctlRuleDialog" />
    <LogsDrawer v-model="ctlLogsDrawer" />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import http from '../api/client'
import { useAuthStore } from '../stores/auth'
import MetricsPanel from './MetricsPanel.vue'
import ServiceCard from './ServiceCard.vue'
import ConfigDialog from './ConfigDialog.vue'
import NewServiceDialog from './NewServiceDialog.vue'
import DuplicateServiceDialog from './DuplicateServiceDialog.vue'
import ServiceLogsDrawer from './ServiceLogsDrawer.vue'
import RuleEditor from './RuleEditor.vue'
import LogsDrawer from './LogsDrawer.vue'

// 服务 Tab（LlamaCtl 能力）：systemd 服务扫描/管理 + 主机指标面板。
// 从 HostDetailView 抽出，独立持有管理侧状态与 1s 状态轮询 / 5s 指标轮询生命周期。
const props = defineProps({
  hostId: { type: String, required: true },   // mid（监控/路由用）
  active: { type: Boolean, default: false },  // 当前是否显示本 Tab（驱动轮询启停）
  hostLabel: { type: String, default: '' },
})

const authStore = useAuthStore()
const ctlDbId = ref(0)
const ctlServices = ref([])
// 服务筛选（移植自 LlamaCtl-Web Dashboard：关键词 + 状态 + 自启）
const ctlFilterKeyword = ref('')
const ctlFilterStatus = ref('all')
const ctlFilterEnabled = ref('all')
const ctlFilteredServices = computed(() => {
  const kw = ctlFilterKeyword.value.trim().toLowerCase()
  const list = ctlServices.value.filter((s) => {
    if (kw && !s.name.toLowerCase().includes(kw)) return false
    if (ctlFilterStatus.value === 'not_inactive') {
      if (s.active_state === 'inactive') return false
    } else if (ctlFilterStatus.value !== 'all' && s.active_state !== ctlFilterStatus.value) {
      return false
    }
    if (ctlFilterEnabled.value === 'enabled' && s.unit_file_state !== 'enabled') return false
    if (ctlFilterEnabled.value === 'disabled' && s.unit_file_state === 'enabled') return false
    return true
  })
  // 运行中的永远排在最前，其次异常，其余按名称
  const rank = (s) => (s.active_state === 'active' ? 0 : s.active_state === 'failed' ? 1 : 2)
  return list.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name))
})
const ctlHasFilter = computed(
  () => !!(ctlFilterKeyword.value.trim() || ctlFilterStatus.value !== 'all' || ctlFilterEnabled.value !== 'all'),
)
function clearCtlFilters() {
  ctlFilterKeyword.value = ''
  ctlFilterStatus.value = 'all'
  ctlFilterEnabled.value = 'all'
}
const ctlMetrics = ref(null)
const ctlMetricsError = ref('')
const ctlScanning = ref(false)
const ctlActionLoading = ref({})  // { [service.name]: 'start'|'stop'|'restart'|'refresh' }
const ctlConfigVisible = ref(false)
const newServiceVisible = ref(false)
const ctlDupVisible = ref(false)
const ctlDupService = ref(null)
const ctlSvcLogsVisible = ref(false)
const ctlActiveService = ref(null)
const ctlRuleDialog = ref(false)
const ctlLogsDrawer = ref(false)
let ctlMetricsTimer = null
let ctlStateTimer = null

async function ctlScan() {
  if (!ctlDbId.value) return
  ctlScanning.value = true
  try {
    ctlServices.value = await http.get('/services', { params: { host_id: ctlDbId.value } })
  } catch (e) {
    ctlServices.value = []
  } finally {
    ctlScanning.value = false
  }
}

// 强制刷新：重扫服务状态 + 立即拉取指标（服务自行崩溃等状态变化即时同步，无需等 5s 轮询）
async function ctlRefresh() {
  await Promise.all([ctlScan(), ctlPollMetrics()])
}

// 准实时状态轮询：每秒查询一次（后端复用监控快照，零额外 SSH；服务自行崩溃等变化 ~2s 内同步到卡片/统计）
async function ctlPollState() {
  if (!ctlDbId.value || !ctlServices.value.length) return
  try {
    const names = ctlServices.value.map((s) => s.name).join(',')
    const data = await http.get('/services/state', { params: { host_id: ctlDbId.value, names }, silent: true })
    if (!data) return
    for (const s of ctlServices.value) {
      const st = data[s.name]
      if (st && st.active_state) {
        s.active_state = st.active_state
        if (st.sub_state != null) s.sub_state = st.sub_state
      }
    }
  } catch (e) { /* SSH 断开等：保留最后已知状态，不打扰用户 */ }
}

async function ctlPollMetrics() {
  if (!ctlDbId.value) return
  try {
    const params = { host_id: ctlDbId.value }
    // 传入运行中服务：后端主机级指标复用监控快照（零额外 SSH），仅对差额服务补采
    const active = ctlServices.value
      .filter((s) => s.active_state === 'active')
      .map((s) => s.name)
    if (active.length) params.services = active.join(',')
    ctlMetrics.value = await http.get('/metrics', { params })
    ctlMetricsError.value = ''
  } catch (e) {
    ctlMetricsError.value = String((e && e.message) || e)
  }
}

async function onCtlAction(service, action) {
  ctlActionLoading.value[service.name] = action
  try {
    const data = await http.post(`/services/${encodeURIComponent(service.name)}/${action}`, null,
      { params: { host_id: ctlDbId.value } })
    // 后端等待 systemctl 完成并返回最新状态：直接合并进列表项，按钮/状态立即切换
    const item = ctlServices.value.find((s) => s.name === service.name)
    if (item && data) Object.assign(item, data)
    else ctlScan()
    ctlPollMetrics()
  } catch (e) {
    // 动作失败：主机侧状态可能已变化（如启动失败 → failed），重扫同步，卡片显示「失败」
    ctlScan()
  }
  finally {
    delete ctlActionLoading.value[service.name]
  }
}

function openCtlConfig(service) {
  ctlActiveService.value = service
  ctlConfigVisible.value = true
}

async function onCtlRestore(service) {
  try {
    await http.post(`/services/${encodeURIComponent(service.name)}/restore`, null,
      { params: { host_id: ctlDbId.value } })
    ctlScan()
  } catch (e) { /* client 已提示 */ }
}

function openCtlLogs(service) {
  ctlActiveService.value = service
  ctlSvcLogsVisible.value = true
}

function openCtlDup(service) {
  ctlDupService.value = service
  ctlDupVisible.value = true
}

// 删除：停止（如运行中）+ 删除单元文件（仅 /etc/systemd/system；后端失败自动回滚）
async function onCtlDelete(service) {
  try {
    await ElMessageBox.confirm(
      `将停止服务（如运行中）并永久删除单元文件 ${service.fragment_path || service.name}，此操作不可撤销。`,
      '删除服务',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return } // 取消
  ctlActionLoading.value[service.name] = 'delete'
  try {
    await http.post(`/services/${encodeURIComponent(service.name)}/delete`, null,
      { params: { host_id: ctlDbId.value } })
    ElMessage.success(`已删除 ${service.name}`)
    ctlScan()
  } catch (e) { /* client 已提示 */ }
  finally {
    delete ctlActionLoading.value[service.name]
  }
}

// 重命名：单元文件改名（后端运行中会先停后启，失败自动回滚）
async function onCtlRename(service) {
  let newName
  try {
    const { value } = await ElMessageBox.prompt(`重命名「${service.name}」`, '重命名服务', {
      confirmButtonText: '重命名',
      cancelButtonText: '取消',
      inputValue: service.name.replace(/\.service$/i, ''),
      inputPlaceholder: '新服务名（可省略 .service 后缀）',
      inputValidator: (v) => (!v || !v.trim() ? '请输入新服务名' : true),
    })
    newName = value.trim()
  } catch { return } // 取消
  if (newName === service.name) { ElMessage.warning('新服务名与原服务名相同'); return }
  ctlActionLoading.value[service.name] = 'rename'
  try {
    const data = await http.post(`/services/${encodeURIComponent(service.name)}/rename`, { new_name: newName },
      { params: { host_id: ctlDbId.value } })
    ElMessage.success(`已重命名为 ${data.renamed}`)
    ctlScan()
  } catch (e) { /* client 已提示 */ }
  finally {
    delete ctlActionLoading.value[service.name]
  }
}

onMounted(async () => {
  // 解析管理侧 db_id（统一列表 id=mid，db_id=整型主键）
  try {
    const hosts = await api.hosts()
    const h = hosts.find((x) => x.id === props.hostId)
    if (h && h.db_id) {
      ctlDbId.value = h.db_id
      authStore.setCurrentHost(h.db_id)
    }
  } catch (e) { /* 无管理数据 */ }
})

// 首次进入懒加载 + 1s 状态轮询 + 5s 指标轮询；离开 Tab 停止轮询
let ctlLoaded = false
watch([() => props.active, ctlDbId], ([a, dbId]) => {
  if (a && dbId) {
    if (!ctlLoaded) {
      ctlLoaded = true
      ctlScan()
      ctlPollMetrics()
    }
    if (!ctlMetricsTimer) ctlMetricsTimer = setInterval(ctlPollMetrics, 5000)
    if (!ctlStateTimer) {
      ctlPollState()
      ctlStateTimer = setInterval(ctlPollState, 1000)
    }
  } else {
    if (ctlMetricsTimer) {
      clearInterval(ctlMetricsTimer)
      ctlMetricsTimer = null
    }
    if (ctlStateTimer) {
      clearInterval(ctlStateTimer)
      ctlStateTimer = null
    }
  }
})
onBeforeUnmount(() => {
  if (ctlMetricsTimer) clearInterval(ctlMetricsTimer)
  if (ctlStateTimer) clearInterval(ctlStateTimer)
})
</script>

<style scoped>
.ctl-toolbar { display: flex; flex-wrap: wrap; gap: 8px; }
.toolbar-spacer { flex: 1; }
.fb-keyword { width: 180px; }
.fb-status { width: 104px; }
.fb-enabled { width: 104px; }
.fb-count { font-size: 12px; color: var(--lc-text-muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
.fb-clear { font-size: 12px; padding: 0 6px; }
</style>
