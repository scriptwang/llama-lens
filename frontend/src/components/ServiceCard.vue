<template>
  <div class="svc-card" :class="cardClass">
    <div class="svc-head">
      <div class="svc-name-wrap">
        <span class="status-dot" :class="dotClass" />
        <span class="svc-name" :title="service.name">{{ service.name }}</span>
        <el-tooltip content="重命名服务" placement="top">
          <el-icon class="svc-rename" @click="$emit('rename', service)"><EditPen /></el-icon>
        </el-tooltip>
      </div>
      <span class="svc-status">{{ statusText }}</span>
    </div>

    <div class="svc-meta">
      <el-tag size="small" :type="service.unit_file_state === 'enabled' ? 'success' : 'info'" effect="plain">
        {{ service.unit_file_state === 'enabled' ? '开机自启' : '未自启' }}
      </el-tag>
      <el-tag size="small" :type="service.match_type === 'content' ? 'warning' : 'primary'" effect="plain">
        {{ service.match_type === 'content' ? '内容识别' : '名称匹配' }}
      </el-tag>
    </div>

    <div class="svc-path" :title="service.fragment_path">{{ service.fragment_path || '（无单元文件）' }}</div>
    <div class="svc-cmd" :title="service.execstart">{{ service.execstart || '（未找到 ExecStart）' }}</div>

    <div v-if="isActive && svcMetrics" class="svc-metrics">
      <span class="sm-item" title="进程启动以来 CPU 均值"><el-icon><Cpu /></el-icon>CPU {{ fmtPct(svcMetrics.cpu_percent) }}</span>
      <span class="sm-item" title="进程内存占用"><el-icon><Odometer /></el-icon>{{ fmtMem(svcMetrics.mem_mb) }}</span>
      <span class="sm-item" title="已运行时长"><el-icon><Timer /></el-icon>{{ fmtUptime(svcMetrics.uptime_sec) }}</span>
    </div>

    <div class="svc-actions">
      <el-button size="small" type="success" :disabled="isActive" :loading="actionLoading === 'start'" @click="$emit('action', service, 'start')">
        <el-icon style="margin-right:3px"><VideoPlay /></el-icon>启动
      </el-button>
      <el-button size="small" type="danger" :disabled="isInactive" :loading="actionLoading === 'stop'" @click="$emit('action', service, 'stop')">
        <el-icon style="margin-right:3px"><VideoPause /></el-icon>停止
      </el-button>
      <el-button size="small" type="warning" :loading="actionLoading === 'restart'" @click="$emit('action', service, 'restart')">
        <el-icon style="margin-right:3px"><RefreshRight /></el-icon>重启
      </el-button>
      <el-button size="small" type="danger" plain :loading="actionLoading === 'delete'" @click="$emit('delete', service)">
        <el-icon style="margin-right:3px"><Delete /></el-icon>删除
      </el-button>
    </div>
    <div class="svc-actions">
      <el-button size="small" type="primary" plain @click="$emit('edit', service)">
        <el-icon style="margin-right:3px"><EditPen /></el-icon>编辑
      </el-button>
      <el-button size="small" plain @click="$emit('restore', service)">
        <el-icon style="margin-right:3px"><Back /></el-icon>恢复
      </el-button>
      <el-button size="small" plain @click="$emit('logs', service)">
        <el-icon style="margin-right:3px"><Document /></el-icon>日志
      </el-button>
      <el-button size="small" plain @click="$emit('duplicate', service)">
        <el-icon style="margin-right:3px"><CopyDocument /></el-icon>复制
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  service: Object,
  metrics: { type: Object, default: null },
  actionLoading: { type: String, default: '' },  // 当前进行中的动作（start/stop/restart/delete）
})
defineEmits(['action', 'edit', 'restore', 'logs', 'duplicate', 'rename', 'delete'])

const isActive = computed(() => props.service.active_state === 'active')
// 停止仅在「已停止」时禁用：启动中/停止中/失败等非停止态都允许停止（如 auto-restart 循环中服务永远不是 active，仍需可停）
const isInactive = computed(() => props.service.active_state === 'inactive')
const svcMetrics = computed(() => (props.metrics ? props.metrics[props.service.name] : null))

function fmtPct(v) {
  if (v == null) return '—'
  return (Math.round(v * 10) / 10).toFixed(1) + '%'
}

function fmtMem(mb) {
  if (mb == null) return '—'
  return mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : Math.round(mb) + ' MB'
}

function fmtUptime(sec) {
  if (sec == null) return '—'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  if (m) return `${m}m`
  return `${sec}s`
}
const dotClass = computed(() => ({
  active: props.service.active_state === 'active',
  failed: props.service.active_state === 'failed',
  inactive: props.service.active_state === 'inactive',
  other: !['active', 'failed', 'inactive'].includes(props.service.active_state),
}))
const cardClass = computed(() => ({
  'is-active': props.service.active_state === 'active',
  'is-failed': props.service.active_state === 'failed',
}))
const statusText = computed(
  () =>
    ({
      active: '运行中',
      failed: '失败',
      inactive: '已停止',
      activating: '启动中',
      deactivating: '停止中',
    }[props.service.active_state] || props.service.active_state),
)
</script>
