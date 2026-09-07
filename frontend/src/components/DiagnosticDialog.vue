<template>
  <el-dialog v-model="visible" title="一键体检" width="880px" top="5vh" append-to-body :close-on-click-modal="false">
    <div v-loading="loading" class="diag">
      <template v-if="data">
        <!-- 头部：主机 + 时间 + 状态标签 -->
        <div class="dg-head">
          <div class="dg-host">
            <span class="dg-host-name">{{ data.host.name }}</span>
            <span class="dg-host-addr">{{ data.host.host }}:{{ data.host.port }}</span>
            <span class="dg-host-time">生成于 {{ data.host.generated_at }}</span>
          </div>
          <div class="dg-chips">
            <el-tag :type="data.overall.llama_online ? 'success' : 'danger'" size="small" effect="dark" round>
              llama {{ data.overall.llama_online ? '在线' : '离线' }}
            </el-tag>
            <el-tag :type="data.overall.ssh_ok ? 'success' : 'danger'" size="small" effect="dark" round>
              SSH {{ data.overall.ssh_ok ? '已连接' : '断开' }}
            </el-tag>
            <el-tag :type="data.overall.alerts_total ? 'warning' : 'info'" size="small" effect="dark" round>
              告警 {{ data.overall.alerts_total }}
            </el-tag>
            <el-tag :type="data.error_logs.length ? 'danger' : 'info'" size="small" effect="dark" round>
              错误日志 {{ data.error_logs.length }}
            </el-tag>
          </div>
        </div>

        <!-- 卡片网格 -->
        <div class="dg-grid">
          <!-- 总体状态 -->
          <div class="dg-card">
            <div class="dg-card-title"><el-icon><FirstAidKit /></el-icon>总体状态</div>
            <div class="dg-kv">
              <span class="k">llama</span><b :class="data.overall.llama_online ? 'v-ok' : 'v-bad'">{{ data.overall.llama_online ? '在线' : '离线' }}</b>
              <span class="k">SSH</span><b :class="data.overall.ssh_ok ? 'v-ok' : 'v-bad'">{{ data.overall.ssh_ok ? '已连接' : '断开' }}</b>
              <span class="k">告警</span><b>{{ data.overall.alerts_total }} 条</b>
              <span class="k">danger</span><b class="v-bad">{{ data.overall.alerts_danger }}</b>
              <span class="k">warn</span><b class="v-warn">{{ data.overall.alerts_warn }}</b>
            </div>
            <div v-if="data.overall.alert_items.length" class="dg-alerts">
              <div v-for="(a, i) in data.overall.alert_items" :key="i" class="dg-alert" :class="a.level">
                <span class="lv">[{{ a.level }}]</span> {{ a.metric }}：{{ a.value }} ≥ {{ a.threshold }}
              </div>
            </div>
            <div v-else class="dg-none">无活动告警</div>
          </div>

          <!-- llama API -->
          <div class="dg-card">
            <div class="dg-card-title"><el-icon><Lightning /></el-icon>llama API</div>
            <div class="dg-kv">
              <span class="k">/health</span><b :class="data.llama_api.online ? 'v-ok' : 'v-bad'">{{ data.llama_api.online ? 'OK（在线）' : '失败（离线）' }}</b>
              <span class="k">模型</span><b class="dg-model">{{ data.llama_api.model }}</b>
              <span class="k">生成速度</span><b>{{ data.llama_api.gen_speed_tps.toFixed(1) }} tok/s</b>
            </div>
          </div>

          <!-- 系统 -->
          <div class="dg-card">
            <div class="dg-card-title"><el-icon><Monitor /></el-icon>系统</div>
            <div class="dg-metric">
              <div class="dg-metric-head"><span>CPU</span><b>{{ pct(data.system.cpu_pct) }}</b></div>
              <div class="dg-bar"><i :class="barLevel(data.system.cpu_pct)" :style="{ width: (data.system.cpu_pct || 0) + '%' }"></i></div>
            </div>
            <div class="dg-metric">
              <div class="dg-metric-head"><span>内存</span><b>{{ data.system.mem_used_gib != null ? fmtGb(data.system.mem_used_gib) + ' / ' + fmtGb(data.system.mem_total_gib) + ' GiB' : '—' }}（{{ pct(data.system.mem_pct) }}）</b></div>
              <div class="dg-bar"><i :class="barLevel(data.system.mem_pct)" :style="{ width: (data.system.mem_pct || 0) + '%' }"></i></div>
            </div>
            <div class="dg-metric">
              <div class="dg-metric-head"><span>负载</span><b>{{ (data.system.load.length ? data.system.load.map((x) => x.toFixed(2)).join(' / ') : '— / — / —') }}</b></div>
            </div>
            <div v-for="(d, i) in data.system.disks" :key="i" class="dg-metric">
              <div class="dg-metric-head"><span>磁盘 {{ d.mount }}</span><b>{{ fmtGb(d.used_gb) }} / {{ fmtGb(d.size_gb) }} GiB（{{ pct(d.use_pct) }}）</b></div>
              <div class="dg-bar"><i :class="barLevel(d.use_pct)" :style="{ width: (d.use_pct || 0) + '%' }"></i></div>
            </div>
            <div class="dg-metric">
              <div class="dg-metric-head"><span>网络</span><b>{{ data.system.has_net ? 'rx ' + data.system.net_rx + ' / tx ' + data.system.net_tx + ' MB/s' : '—' }}</b></div>
            </div>
          </div>

          <!-- GPU -->
          <div class="dg-card">
            <div class="dg-card-title"><el-icon><Cpu /></el-icon>GPU（{{ data.gpus.length }}）</div>
            <div v-if="!data.gpus.length" class="dg-none">无 GPU 数据</div>
            <div v-for="g in data.gpus" :key="g.index" class="dg-gpu">
              <div class="dg-gpu-head">
                <b>GPU{{ g.index }}</b>
                <span class="dim">{{ g.name }}</span>
              </div>
              <div class="dg-bar"><i :class="barLevel(g.util_pct)" :style="{ width: (g.util_pct || 0) + '%' }"></i></div>
              <div class="dg-gpu-meta">
                <span>利用率 <b>{{ pct(g.util_pct) }}</b></span>
                <span>显存 <b>{{ g.mem_used_gib != null ? g.mem_used_gib + '/' + g.mem_total_gib + ' GiB' : '—' }}</b></span>
                <span>温度 <b>{{ g.temp_c != null ? g.temp_c + '°C' : '—' }}</b></span>
                <span>功耗 <b>{{ g.power_w != null ? g.power_w + ' W' : '—' }}</b></span>
              </div>
            </div>
          </div>

          <!-- 服务 -->
          <div class="dg-card">
            <div class="dg-card-title"><el-icon><Platform /></el-icon>服务（{{ data.services.length }}）</div>
            <div v-if="!data.services.length" class="dg-none">未扫描到服务</div>
            <div class="dg-svc-table">
              <div class="dg-svc-row head">
                <span class="c-dot"></span>
                <span class="c-name">服务</span>
                <span class="c-state">状态</span>
                <span class="c-boot">自启</span>
              </div>
              <div v-for="s in data.services" :key="s.name" class="dg-svc-row">
                <span class="c-dot"><span class="dot" :class="s.active_state === 'active' ? 'ok' : (s.active_state === 'failed' ? 'bad' : 'off')"></span></span>
                <span class="c-name" :title="s.name">{{ s.name }}</span>
                <span class="c-state dim">{{ s.active_state }}（{{ s.sub_state }}）</span>
                <span class="c-boot dim">{{ s.unit_file_state }}</span>
              </div>
            </div>
          </div>

          <!-- 最近事件 -->
          <div class="dg-card">
            <div class="dg-card-title"><el-icon><DataLine /></el-icon>最近事件（{{ data.events.length }}）</div>
            <div v-if="!data.events.length" class="dg-none">无</div>
            <div v-else class="dg-evs">
              <div v-for="(e, i) in data.events" :key="i" class="dg-ev">
                <span class="ev-ts">{{ e.ts }}</span>
                <span class="ev-lv" :class="e.level">[{{ e.level }}]</span>
                <span class="ev-msg">{{ e.type }}：{{ e.msg }}</span>
              </div>
            </div>
          </div>

          <!-- 错误日志 -->
          <div class="dg-card span2">
            <div class="dg-card-title"><el-icon><Document /></el-icon>最近错误日志（{{ data.error_logs.length }}）</div>
            <div v-if="!data.error_logs.length" class="dg-none">无</div>
            <pre v-else class="dg-code">{{ data.error_logs.join('\n') }}</pre>
          </div>
        </div>
      </template>

      <!-- 旧后端兜底：无结构化数据时展示 markdown -->
      <pre v-else-if="markdown && !loading" class="diag-md">{{ markdown }}</pre>
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

// 一键体检（P2-1）：卡片式仪表盘展示（总体/系统/GPU/服务/API/事件/错误日志）+ 复制/下载 markdown。
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
const data = ref(null)

function pct(v) {
  return v == null ? '—' : Math.round(v) + '%'
}
function fmtGb(v) {
  return v == null ? '—' : v.toFixed(1)
}
function barLevel(v) {
  if (v == null) return ''
  if (v >= 90) return 'danger'
  if (v >= 75) return 'warn'
  return ''
}

watch(() => props.modelValue, async (open) => {
  if (!open) return
  loading.value = true
  markdown.value = ''
  data.value = null
  try {
    const hosts = await api.hosts()
    const h = hosts.find((x) => x.id === props.hostId)
    if (!h) throw new Error('未知主机')
    const res = await http.get(`/hosts/${h.db_id}/diagnostic`)
    markdown.value = res.markdown
    data.value = res.data || null
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
/* 头部 */
.dg-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.dg-host { display: flex; align-items: baseline; gap: 10px; }
.dg-host-name { font-size: 16px; font-weight: 700; color: var(--text); }
.dg-host-addr { font-size: 12px; color: var(--text-dim); font-family: var(--font-mono, monospace); }
.dg-host-time { font-size: 11px; color: var(--text-faint); }
.dg-chips { display: flex; gap: 6px; flex-wrap: wrap; }
/* 卡片网格 */
.dg-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  max-height: 62vh;
  overflow-y: auto;
  padding-right: 4px;
}
.dg-card {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}
.dg-card.span2 { grid-column: span 2; }
.dg-card-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
}
.dg-card-title .el-icon { color: var(--cyan); font-size: 15px; }
.dg-none { font-size: 12px; color: var(--text-faint); }
/* 键值对 */
.dg-kv {
  display: grid;
  grid-template-columns: auto 1fr auto 1fr;
  gap: 6px 10px;
  font-size: 12.5px;
  align-items: baseline;
}
.dg-kv .k { color: var(--text-dim); }
.dg-kv b { color: var(--text); font-weight: 600; word-break: break-all; }
.dg-kv .v-ok { color: var(--green); }
.dg-kv .v-bad { color: var(--red); }
.dg-kv .v-warn { color: var(--amber); }
.dg-model { font-family: var(--font-mono, monospace); font-size: 11.5px; }
/* 告警 */
.dg-alerts { display: flex; flex-direction: column; gap: 4px; }
.dg-alert {
  font-size: 12px;
  padding: 5px 10px;
  border-radius: 6px;
  border: 1px solid;
}
.dg-alert.danger { color: var(--red); border-color: color-mix(in srgb, var(--red) 35%, transparent); background: color-mix(in srgb, var(--red) 8%, transparent); }
.dg-alert.warn { color: var(--amber); border-color: color-mix(in srgb, var(--amber) 35%, transparent); background: color-mix(in srgb, var(--amber) 8%, transparent); }
.dg-alert .lv { font-weight: 700; margin-right: 4px; }
/* 指标条 */
.dg-metric { display: flex; flex-direction: column; gap: 4px; }
.dg-metric-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 12px;
  color: var(--text-dim);
}
.dg-metric-head b { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }
.dg-bar {
  height: 6px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--text) 8%, transparent);
  overflow: hidden;
}
.dg-bar i { display: block; height: 100%; border-radius: 3px; background: var(--green); transition: width .3s ease; }
.dg-bar i.warn { background: var(--amber); }
.dg-bar i.danger { background: var(--red); }
/* GPU */
.dg-gpu {
  border: 1px solid var(--card-border);
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 7px;
  background: color-mix(in srgb, var(--text) 2%, transparent);
}
.dg-gpu-head { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; }
.dg-gpu-head b { color: var(--text); }
.dg-gpu-head .dim { color: var(--text-dim); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dg-gpu-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px 12px;
  font-size: 11.5px;
  color: var(--text-dim);
}
.dg-gpu-meta b { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }
/* 服务 */
.dg-svc-table { display: flex; flex-direction: column; }
.dg-svc-row {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) 132px 64px;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 5px 0;
  border-bottom: 1px dashed color-mix(in srgb, var(--text) 8%, transparent);
}
.dg-svc-row:last-child { border-bottom: none; }
.dg-svc-row.head { color: var(--text-faint); font-size: 11px; border-bottom: 1px solid var(--card-border); }
.dg-svc-row .dot { width: 8px; height: 8px; border-radius: 50%; }
.dg-svc-row .dot.ok { background: var(--green); }
.dg-svc-row .dot.bad { background: var(--red); }
.dg-svc-row .dot.off { background: var(--text-faint); }
.dg-svc-row .c-name { font-weight: 600; color: var(--text); font-family: var(--font-mono, monospace); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dg-svc-row .dim { color: var(--text-dim); font-size: 11.5px; }
/* 事件 */
.dg-evs { display: flex; flex-direction: column; gap: 4px; max-height: 220px; overflow-y: auto; }
.dg-ev { display: flex; gap: 8px; font-size: 11.5px; align-items: baseline; }
.ev-ts { color: var(--text-faint); font-family: var(--font-mono, monospace); flex: none; }
.ev-lv { font-weight: 700; flex: none; }
.ev-lv.danger { color: var(--red); }
.ev-lv.warn { color: var(--amber); }
.ev-lv.info, .ev-lv { color: var(--text-dim); }
.ev-msg { color: var(--text-dim); word-break: break-all; }
/* 错误日志 */
.dg-code {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--bg);
  border: 1px solid var(--card-border);
  font-size: 11.5px;
  line-height: 1.6;
  font-family: var(--font-mono, monospace);
  color: var(--text-dim);
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
/* 兜底 markdown */
.diag-md {
  max-height: 60vh;
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
@media (max-width: 760px) {
  .dg-grid { grid-template-columns: 1fr; }
  .dg-card.span2 { grid-column: span 1; }
}
</style>
