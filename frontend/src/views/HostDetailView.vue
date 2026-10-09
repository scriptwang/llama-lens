<template>
  <div class="detail">
    <TopBar
      :host-id="props.id"
      :host-name="hostName"
      :model-name="modelName"
      :model-title="modelTitle"
      :llama-online="llamaOnline"
      :engine-type="engineType"
      :ssh-ok="sshOk"
      :stats="topStats"
      :mode="mode"
      :degraded="degraded"
      :connected="connected"
      @update:mode="setMode"
    />

    <div v-if="!llamaOnline && !sshOk" class="banner-danger">主机不可达（{{ engineLabel }} 离线 + SSH 断开）</div>

    <!-- 平级 Tab：监控 / 服务（?tab=service 可深链） -->
    <nav ref="tabsRef" class="tabs">
      <button v-for="t in visibleTabs" :key="t.key" :class="{ on: currentTab === t.key }" @click="switchTab(t.key)">{{ t.label }}</button>
    </nav>

    <main class="content">
      <!-- ============ 监控 Tab ============ -->
      <div v-show="currentTab === 'monitor'" class="tab-pane monitor-pane">
        <AutoBrowseBar ref="browseBarRef" :active="currentTab === 'monitor' && !!snap" :chrome="56 + tabsH" :default-enabled="uiCfg.auto_browse && uiCfg.auto_browse.enabled" />
        <!-- 左侧滚轮导航（iOS wheel picker）：极小、透明、贴屏幕最左侧；分区名排成 3D 圆柱，中间行选中高亮，上下弯向圆柱背面渐隐 -->
        <nav v-if="snap" class="wheel" aria-label="监控分区导航">
          <div class="wheel-mask">
            <div class="wheel-inner" :style="{ transform: 'rotateX(' + wheelAngle + 'deg)' }">
              <button v-for="(s, i) in secDefs" :key="s.id"
                      :class="['wheel-item', { on: secActive === s.id }]"
                      :style="{ transform: 'rotateX(' + (-(i * WHEEL_STEP)) + 'deg) translateZ(' + WHEEL_R + 'px)' }"
                      @click="scrollToSec(s.id)">{{ s.short }}</button>
            </div>
          </div>
        </nav>
        <!-- 首次加载骨架 -->
        <template v-if="!snap">
        <div class="skeleton" style="height: 120px; margin-bottom: 16px"></div>
        <div class="skeleton" style="height: 200px; margin-bottom: 16px"></div>
        <div class="skeleton" style="height: 300px"></div>
        </template>

        <template v-else>
        <!-- ============ 实时总览区 ============ -->
        <section id="sec-overview">
          <div class="section-title">实时总览</div>
          <div class="gauge-row">
            <BarCard
              title="Token 生成速度"
              :value="llamaOnline ? snap.llama.gen_speed_tps : null"
              unit="tok/s"
              :spark="sparkGen"
              :sub="speedSub"
              :foot="genFoot"
            />
            <BarCard
              title="预填充速度"
              :value="promptVal"
              unit="tok/s"
              :progress="prefillProgress"
              :spark="sparkPrompt"
              :sub="promptSub"
              :foot="promptFoot"
            />
            <BarCard
              :title="isSglang ? 'KV / 上下文占用' : '上下文占用'"
              :value="ctxUsedVal"
              :level="ctxLevel"
              :bar-max="ctxTotal"
              :spark="sparkCtx"
              :sub="ctxBarSub"
              :badge="ctxBadge"
              :fmt="fmtCtxNum"
              :fmt-compact="fmtTokens"
            />
            <GaugeCard
              v-if="!isSglang"
              title="MTP 接受率"
              :value="mtpPct"
              unit="%"
              :level="mtpLevel"
              :spark="sparkMtp"
              :sub="mtpGaugeSub"
              :zones="mtpZones"
              :zone-colors="mtpZoneColors"
            />
            <BarCard
              v-else
              title="投机接受长度"
              :value="specLen"
              unit="tok/step"
              :spark="sparkSpec"
              :sub="specSub"
              :digits="2"
            />
          </div>
        </section>

        <!-- ============ GPU 区 ============ -->
        <section id="sec-gpu">
          <div class="section-title">GPU（按卡聚合）</div>
          <div v-if="sshOk" class="gpu-grid" :class="{ single: gpus.length <= 1 }">
            <GpuPanel v-for="g in gpus" :key="g.index" :gpu="g" :alerts="alerts" />
          </div>
          <div v-else class="glass placeholder"><span class="icon">▣</span>数据不可用（SSH 断开）</div>
        </section>

        <!-- ============ 实时生成任务区 ============ -->
        <section id="sec-task">
          <div class="section-title">实时生成任务</div>
          <div class="task-row">
            <LlamaStateCard v-if="!isSglang" :log="snap.llama.log" :online="llamaOnline" :slots="slots" :flags="flags" :now="snap.ts" />
            <SglangStateCard v-else :engine="eng" :online="llamaOnline" />
            <EventFeed :events="events" fill />
          </div>
        </section>

        <!-- ============ 系统区 ============ -->
        <section id="sec-sys">
          <div class="section-title">系统资源</div>
          <template v-if="sshOk">
            <div class="sys-grid">
              <CpuPanel :cpu="cpu" :alerts="alerts" />
              <MemPanel :mem="mem" :alerts="alerts" />
            </div>
            <div class="sys-grid">
              <DiskPanel :disk="disk" :alerts="alerts" />
              <NetPanel :net="net" />
            </div>
          </template>
          <div v-else class="glass placeholder"><span class="icon">▣</span>数据不可用（SSH 断开）</div>
        </section>

        <!-- ============ 模型与 Slot 区 ============ -->
        <section id="sec-model">
          <div class="section-title">{{ isSglang ? "模型" : "模型与 Slot" }}</div>
          <div class="model-grid">
            <ModelInfoCard :model="model" />
            <SlotTable v-if="!isSglang" :slots="slots" />
          </div>
        </section>

        <!-- ============ 进程区 ============ -->
        <section id="sec-proc">
          <div class="section-title">进程</div>
          <template v-if="sshOk">
            <div class="proc-grid">
              <LlamaProcessCard
                :process="process"
                :service="service"
                :title="isSglang ? 'sglang serve 进程' : 'llama-server 进程'"
                :not-found-text="isSglang ? '未找到 sglang 进程（按 cmdline 固定串匹配）' : ''"
              />
              <div class="proc-side">
                <TopProcessTable :rows="topCpu" mode="cpu" />
                <TopProcessTable :rows="topMem" mode="mem" />
              </div>
            </div>
          </template>
          <div v-else class="glass placeholder"><span class="icon">▣</span>数据不可用（SSH 断开）</div>
        </section>

        <!-- ============ 趋势区 ============ -->
        <section id="sec-trend">
          <div class="section-title trend-title-row">
            历史趋势
            <span class="win-switch mono">
              <button v-for="w in windows" :key="w.s" :class="{ on: winS === w.s && !anchor && !isCustom }" @click="pickWindow(w.s)">{{ w.label }}</button>
              <button :class="{ on: anchor === 'today' }" @click="pickAnchor('today')">今日</button>
              <button :class="{ on: anchor === 'week' }" @click="pickAnchor('week')">本周</button>
              <button :class="{ on: anchor === 'month' }" @click="pickAnchor('month')">本月</button>
              <button :class="{ on: isCustom }" @click="toggleCustom">自定义</button>
            </span>
            <el-date-picker
              v-if="isCustom"
              v-model="customRange"
              type="datetimerange"
              size="small"
              class="trend-range"
              range-separator="~"
              start-placeholder="开始时间"
              end-placeholder="结束时间"
              value-format="x"
              :disabled-date="disabledDate"
            />
          </div>
          <div class="trend-grid">
            <div class="trend-group">{{ isSglang ? 'SGLang' : 'llama' }}</div>
            <TrendChart title="Token 生成速度" unit="tok/s" :series="chartGen" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" />
            <TrendChart title="预填充速度" unit="tok/s" :series="chartPrompt" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" />
            <TrendChart title="上下文占用" unit="tokens" :series="chartCtx" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" />
            <TrendChart v-if="!isSglang" title="MTP 接受率" unit="%" :series="chartMtp" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" :y-max="100" :y-min="0" />
            <TrendChart v-else title="投机接受长度" unit="tok/step" :series="chartSpec" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" />
            <div class="trend-group">GPU</div>
            <TrendChart title="GPU 利用率" unit="%" :series="chartGpuUtil" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" :y-max="100" />
            <TrendChart title="GPU 显存" unit="MB" :series="chartGpuMem" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" />
            <TrendChart title="GPU 温度" unit="°C" :series="chartGpuTemp" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" />
            <TrendChart title="GPU 功耗" unit="W" :series="chartGpuPower" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" />
            <div class="trend-group">系统</div>
            <TrendChart title="CPU" unit="%" :series="chartCpu" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" :y-max="100" />
            <TrendChart title="内存" unit="MB" :series="chartMem" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" />
            <TrendChart title="网络" unit="MB/s" :series="chartNet" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" />
            <TrendChart title="负载均值" unit="load" :series="chartLoad" :span="isCustom ? customSpan : liveSpan" :x-min="xMin" :x-max="xMax" :height="170" />
          </div>
        </section>

        <!-- ============ 效率统计区（P1-2：token 产出 + 耗电 + tokens/瓦；时间范围与历史趋势共用） ============ -->
        <section id="sec-eff">
          <div class="section-title">效率统计</div>
          <EfficiencyCard :host-id="props.id" :sel="timeSel" />
        </section>
        </template>
      </div>

      <!-- ============ 服务 Tab（LlamaCtl 能力，独立组件） ============ -->
      <ServiceTab
        v-show="currentTab === 'service'"
        :host-id="props.id"
        :active="currentTab === 'service'"
        :host-label="hostName"
      />

      <!-- ============ 终端 Tab（xterm.js + WebSocket 交互 shell + 文件管理） ============ -->
      <TerminalTab
        v-show="currentTab === 'terminal'"
        :host-id="props.id"
        :active="currentTab === 'terminal'"
        :host-label="hostName"
        :max-sessions="uiCfg.terminal_max_sessions || 20"
      />

      <!-- ============ 模型 Tab（P0-2：清单 + 适配预估 + 一键切换） ============ -->
      <ModelTab
        v-show="currentTab === 'model'"
        :host-id="props.id"
        :active="currentTab === 'model'"
        :host-label="hostName"
      />

      <!-- ============ Playground Tab（P1-1：聊天 + 性能指标） ============ -->
      <PlaygroundTab
        v-show="currentTab === 'playground'"
        :host-id="props.id"
        :active="currentTab === 'playground'"
        :host-label="hostName"
      />

      <!-- ============ 网关 Tab（该主机在统一网关里的状态 + 流量切片） ============ -->
      <GatewayTab
        v-show="currentTab === 'gateway'"
        :host-id="props.id"
        :active="currentTab === 'gateway'"
        :host-label="hostName"
        :snap="snap"
      />
    </main>

    <div v-if="mode === 'paused' && currentTab === 'monitor'" class="paused-watermark"><span>已暂停</span></div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useHostStream } from '../stream'
import { totalSpeed as globalSpeed } from '../speed'
import { fmtNum, fmtClock, fmtTokens, fmtDuration, alertLevel, anchorStartMs } from '../utils'
import { chartTheme } from '../theme'
import TopBar from '../components/TopBar.vue'
import BarCard from '../components/BarCard.vue'
import GaugeCard from '../components/GaugeCard.vue'
import LlamaStateCard from '../components/LlamaStateCard.vue'
import SglangStateCard from '../components/SglangStateCard.vue'
import GpuPanel from '../components/GpuPanel.vue'
import CpuPanel from '../components/CpuPanel.vue'
import MemPanel from '../components/MemPanel.vue'
import DiskPanel from '../components/DiskPanel.vue'
import NetPanel from '../components/NetPanel.vue'
import LlamaProcessCard from '../components/LlamaProcessCard.vue'
import TopProcessTable from '../components/TopProcessTable.vue'
import ModelInfoCard from '../components/ModelInfoCard.vue'
import SlotTable from '../components/SlotTable.vue'
import TrendChart from '../components/TrendChart.vue'
import EventFeed from '../components/EventFeed.vue'
import ServiceTab from '../components/ServiceTab.vue'
import TerminalTab from '../components/TerminalTab.vue'
import ModelTab from '../components/ModelTab.vue'
import PlaygroundTab from '../components/PlaygroundTab.vue'
import GatewayTab from '../components/GatewayTab.vue'
import EfficiencyCard from '../components/EfficiencyCard.vue'
import AutoBrowseBar from '../components/AutoBrowseBar.vue'

const props = defineProps({ id: { type: String, required: true } })

const { snapshot, connected, degraded, mode, setMode } = useHostStream(props.id)
const snap = computed(() => snapshot.value)

// ---------------- 平级 Tab：监控 / 服务 ----------------
const route = useRoute()
const router = useRouter()
const TAB_DEFS = [
  { key: 'monitor', label: '监控' },
  { key: 'service', label: '服务' },
  { key: 'terminal', label: '终端' },
  { key: 'model', label: '模型' },
  { key: 'playground', label: '测试' },
  { key: 'gateway', label: '网关' },
]
// UI 功能开关（config.yaml ui 段）：TAB 显隐 + 自动浏览默认开关
const uiCfg = ref({ tabs: { monitor: true, service: true, terminal: true, model: true, playground: true, gateway: true }, auto_browse: { enabled: false } })
async function loadUiCfg() {
  try { uiCfg.value = await api.uiConfig() } catch (e) { /* 保持默认全显 */ }
}
const visibleTabs = computed(() => TAB_DEFS.filter((t) => uiCfg.value.tabs && uiCfg.value.tabs[t.key] !== false))
const tab = computed(() => {
  const t = route.query.tab
  if (t === 'service' || t === 'terminal' || t === 'model' || t === 'playground' || t === 'gateway') return t
  return 'monitor'
})
// 当前 Tab：路由指定的 Tab 被配置隐藏时，回退到第一个可见 Tab
const currentTab = computed(() =>
  visibleTabs.value.some((t) => t.key === tab.value)
    ? tab.value
    : (visibleTabs.value[0] ? visibleTabs.value[0].key : 'monitor'))
function switchTab(t) {
  if (t === tab.value) return
  router.replace({ query: t === 'monitor' ? {} : { tab: t } })
}

// ---------------- 自动浏览条 sticky 定位（顶栏 56px + tabs 实测高度） ----------------
const tabsRef = ref(null)
const tabsH = ref(48)
function measureTabs() {
  if (tabsRef.value) tabsH.value = tabsRef.value.offsetHeight
}

// ---------------- 监控左侧分区导航（滚动定位，若隐若现） ----------------
const browseBarRef = ref(null)
const secDefs = [
  { id: 'sec-overview', label: '实时总览', short: '总览' },
  { id: 'sec-gpu', label: 'GPU（按卡聚合）', short: 'GPU' },
  { id: 'sec-task', label: '实时生成任务', short: '任务' },
  { id: 'sec-sys', label: '系统资源', short: '系统' },
  { id: 'sec-model', label: '模型与 Slot', short: '模型' },
  { id: 'sec-proc', label: '进程', short: '进程' },
  { id: 'sec-trend', label: '历史趋势', short: '趋势' },
  { id: 'sec-eff', label: '效率统计', short: '效率' },
]
const secActive = ref('sec-overview')
const activeIndex = computed(() => secDefs.findIndex((s) => s.id === secActive.value))
// 滚轮（iOS wheel picker）：每分区 30°，圆柱半径 40px；当前分区转到正面中心
const WHEEL_STEP = 30
const WHEEL_R = 34
const wheelAngle = computed(() => activeIndex.value * WHEEL_STEP)
let secRaf = 0
// 顶部固定区高度：终端主题有 38px 窗口标题栏（--chrome-top），其余主题 0
function chromeTop() {
  return parseInt(getComputedStyle(document.documentElement).getPropertyValue('--chrome-top'), 10) || 0
}
function updateSecActive() {
  secRaf = 0
  if (currentTab.value !== 'monitor') return
  const threshold = chromeTop() + 56 + tabsH.value + 40
  let current = secDefs[0].id
  for (const s of secDefs) {
    const el = document.getElementById(s.id)
    if (el && el.getBoundingClientRect().top <= threshold) current = s.id
  }
  if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 4) current = secDefs[secDefs.length - 1].id
  secActive.value = current
}
function onSecScroll() {
  if (!secRaf) secRaf = requestAnimationFrame(updateSecActive)
}
function scrollToSec(id) {
  const el = document.getElementById(id)
  if (!el) return
  browseBarRef.value?.pause() // 导航跳转视为用户操作：暂停自动浏览 5 秒，避免平滑滚动被自动滚动顶掉
  const top = el.getBoundingClientRect().top + window.scrollY - (chromeTop() + 56 + tabsH.value + 14)
  window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
}

// ---------------- 基础字段 ----------------
const hostName = computed(() => (snap.value ? snap.value.host.name : props.id))
const llama = computed(() => (snap.value ? snap.value.llama : {}))
const hm = computed(() => (snap.value ? snap.value.host_metrics : {}))
const alerts = computed(() => (snap.value ? snap.value.alerts : []))
const events = computed(() => (snap.value ? snap.value.events : []))

const llamaOnline = computed(() => !!(snap.value && snap.value.llama.online))
const sshOk = computed(() => !!(snap.value && snap.value.host_metrics.reachable))

// 引擎类型（llama_cpp / sglang）：决定各区域展示形态
const eng = computed(() => (snap.value ? snap.value.engine || {} : {}))
const engineType = computed(() => eng.value.type || 'llama_cpp')
const isSglang = computed(() => engineType.value === 'sglang')
const engineLabel = computed(() => (isSglang.value ? 'SGLang' : 'llama'))

// 当前主机生成速度同步到全局：浏览器标签页标题（App.vue）与 Terminal 窗口标题栏
// （TerminalFrame.vue）据此展示，使详情页也能看到速度（BrandBar 只覆盖门户页）。
// snap 未就绪时为 null，不写入，避免切换视图瞬间标题闪回 0。
const hostSpeed = computed(() => (snap.value ? (llama.value.gen_speed_tps || 0) : null))
watch(hostSpeed, (v) => { if (v !== null) globalSpeed.value = v }, { immediate: true })
const model = computed(() => llama.value.model || {})
const modelName = computed(() => (model.value.name || model.value.path || '').split('/').pop())
const modelTitle = computed(() => model.value.name || model.value.path || '')
const offlineNote = computed(() =>
  snap.value && !llamaOnline.value ? `数据截至 ${fmtClock(snap.value.ts)}` : ''
)

// ---------------- AI 核心 ----------------
const ctx = computed(() => eng.value.ctx || (llama.value.log && llama.value.log.context) || {})
const mtp = computed(() => (llama.value.log && llama.value.log.mtp) || {})
const flags = computed(() => (hm.value.process && hm.value.process.flags) || {})

const ctxUsedVal = computed(() => {
  const u = ctx.value.used
  return u === null || u === undefined ? null : u
})
const ctxLevel = computed(() => alertLevel(alerts.value, 'ctx'))
const ctxBarSub = computed(() => {
  const c = ctx.value
  if (c.used === null || c.used === undefined) return '等待任务结束'
  if (c.pct === null || c.pct === undefined) return ''
  const parts = [`${c.pct.toFixed(1)}%`]
  if (c.remaining !== null && c.remaining !== undefined) parts.push(`剩 ${fmtTokens(c.remaining)}`)
  return parts.join(' · ')
})
const fmtCtxNum = (v) => fmtNum(v, 0)
const ctxBadge = computed(() => (ctx.value.truncated ? '已截断' : ''))
// 原始值依赖：避免 chartCtx 随每秒 WS 快照（ctx 新对象）重算
const ctxTotal = computed(() => {
  const t = ctx.value.total
  return t === null || t === undefined ? null : t
})

const mtpPct = computed(() => {
  const a = mtp.value.acceptance
  return a === null || a === undefined ? null : a * 100
})
const mtpLevel = computed(() => alertLevel(alerts.value, 'mtp'))
const speedSub = computed(() => {
  const src = snap.value && snap.value.llama.speed_source ? `来源 ${snap.value.llama.speed_source}` : ''
  return [src, offlineNote.value].filter(Boolean).join(' · ')
})
const genFoot = computed(() => {
  const st = llama.value.log && llama.value.log.state
  if (st && st.tg_tps !== null && st.tg_tps !== undefined) return `任务均速 ${st.tg_tps.toFixed(1)}`
  return ''
})
// 预填充速度：预填充中显示实时速度，停止后归 0（最近一次预填充信息保留在 sub/foot）
const lastPrefill = computed(() => {
  const log = llama.value.log
  return (log && log.last_prefill) || null
})
const promptVal = computed(() => {
  if (!llamaOnline.value) return null
  if (isSglang.value) {
    const v = snap.value && snap.value.llama ? snap.value.llama.prompt_speed_tps : null
    return v === null || v === undefined ? 0 : v
  }
  const st = llama.value.log && llama.value.log.state
  if (st && st.phase === 'prompt_processing') {
    const v = snap.value && snap.value.llama ? snap.value.llama.prompt_speed_tps : null
    return v === null || v === undefined ? null : v
  }
  return 0
})
const promptSub = computed(() => {
  if (isSglang.value) return speedSub.value
  const st = llama.value.log && llama.value.log.state
  if (llamaOnline.value && st && st.phase === 'prompt_processing') return speedSub.value
  const lp = lastPrefill.value
  if (lp && lp.ts) return `上次 ${fmtClock(lp.ts)}`
  return speedSub.value
})
const prefillEta = computed(() => {
  const st = llama.value.log && llama.value.log.state
  if (!st || st.phase !== 'prompt_processing') return null
  const p = st.prompt_progress
  const total = st.prompt_total_tokens
  const speed = st.prompt_speed_tps
  if (p === null || p === undefined || !total || !speed || speed <= 0) return null
  return Math.max(0, (1 - p) * total / speed)
})
const prefillProgress = computed(() => {
  const st = llama.value.log && llama.value.log.state
  if (!st || st.phase !== 'prompt_processing') return null
  const p = st.prompt_progress
  return p === null || p === undefined ? null : p
})
const promptFoot = computed(() => {
  if (isSglang.value) return ''
  const st = llama.value.log && llama.value.log.state
  if (st && st.phase === 'prompt_processing') {
    const parts = []
    if (st.prompt_progress !== null && st.prompt_progress !== undefined) {
      parts.push(`进度 ${(st.prompt_progress * 100).toFixed(0)}%`)
    }
    if (prefillEta.value !== null) parts.push(`预计剩余 ${fmtDuration(prefillEta.value)}`)
    return parts.join(' · ')
  }
  const lp = lastPrefill.value
  if (lp) {
    const parts = []
    if (lp.n_tokens) parts.push(`${fmtTokens(lp.n_tokens)} tokens`)
    if (lp.progress !== null && lp.progress !== undefined) parts.push(`进度 ${(lp.progress * 100).toFixed(0)}%`)
    return parts.join(' · ')
  }
  return ''
})

// ---------------- 系统数据 ----------------
const gpus = computed(() => hm.value.gpus || [])
const cpu = computed(() => hm.value.cpu || {})
const mem = computed(() => hm.value.mem || {})
const disk = computed(() => hm.value.disk || {})
const net = computed(() => hm.value.net || {})
const process = computed(() => hm.value.process || {})
const service = computed(() => hm.value.service || {})
const topCpu = computed(() => (hm.value.top && hm.value.top.cpu) || [])
const topMem = computed(() => (hm.value.top && hm.value.top.mem) || [])
const slots = computed(() => llama.value.slots || [])

const topStats = computed(() => {
  const s = snap.value
  if (!s) return null
  const ll = s.llama || {}
  const en = s.engine || ll || {}
  const hm = s.host_metrics || {}
  const log = ll.log || {}
  const ctx = en.ctx || log.context || {}
  const mtp = log.mtp || {}
  const mem = hm.mem || {}
  const cpu = hm.cpu || {}
  const spec = en.speculative || {}
  return {
    online: !!ll.online,
    gen: ll.gen_speed_tps,
    prompt: ll.prompt_speed_tps,
    speedSource: ll.speed_source || '',
    engineType: en.type || 'llama_cpp',
    mtp: mtp.acceptance === null || mtp.acceptance === undefined ? null : mtp.acceptance * 100,
    spec: spec.accept_length === null || spec.accept_length === undefined ? null : spec.accept_length,
    ctxUsed: ctx.used,
    ctxRemain: ctx.remaining,
    ctxTotal: ctx.total,
    ctxPct: ctx.pct,
    gpus: (hm.gpus || []).map((g) => ({
      idx: g.index,
      util: g.util_pct,
      temp: g.temp_c,
      power: g.power_w,
      memUsed: g.mem_used_mb,
      memTotal: g.mem_total_mb
    })),
    memUsed: mem.used_mb,
    memTotal: mem.total_mb,
    cpu: cpu.usage_pct,
    alerts: s.alerts || []
  }
})

// ---------------- 实时总览仪表 ----------------
// MTP 接受率阈值反向：低为差（<65 红 / 65-80 黄 / ≥80 绿）
const mtpZones = computed(() => chartTheme().mtpZones)
const mtpZoneColors = computed(() => chartTheme().mtpZoneColors)
const mtpGaugeSub = computed(() => {
  const a = mtp.value.accepted
  const g = mtp.value.generated
  const ml = mtp.value.mean_len
  if (a === null || a === undefined || g === null || g === undefined) return ''
  const spec = flags.value.spec_type ? `${flags.value.spec_type}×${flags.value.spec_draft_n_max ?? '?'} ` : ''
  return `${spec}${fmtNum(a)} / ${fmtNum(g)} · mean ${ml === null || ml === undefined ? '—' : ml.toFixed(2)}`
})

// ---------------- 总览 60s spark ----------------
function mapTail(name, fn) {
  const s = history.value && history.value.series ? history.value.series[name] : null
  if (!s || !s.ts.length) return []
  const now = s.ts[s.ts.length - 1]
  const pts = []
  for (let i = 0; i < s.ts.length; i++) {
    if (now - s.ts[i] > 60) continue
    const v = s.values[i]
    pts.push([s.ts[i], v === null || v === undefined ? null : fn(v)])
  }
  return pts
}

const sparkCtx = computed(() => mapTail('ctx_used', (v) => v))
const sparkMtp = computed(() => mapTail('mtp_acceptance', (v) => v * 100))
const sparkSpec = computed(() => mapTail('accept_length', (v) => v))
const specLen = computed(() => {
  const a = eng.value.speculative && eng.value.speculative.accept_length
  return a === null || a === undefined ? null : a
})
const specSub = computed(() => {
  const spec = eng.value.speculative || {}
  const parts = []
  if (spec.algorithm) parts.push(spec.algorithm)
  if (spec.num_draft_tokens) parts.push(`draft ${spec.num_draft_tokens}`)
  return parts.join(' · ')
})

// ---------------- 趋势图 ----------------
const windows = [
  { s: 300, label: '5m' },
  { s: 900, label: '15m' },
  { s: 3600, label: '1h' },
  { s: 14400, label: '4h' },
  { s: 86400, label: '24h' },
  { s: 604800, label: '7d' },
  { s: 7776000, label: '90d' }
]
const winS = ref(300)
const anchor = ref(null)      // 'today' | 'week' | 'month'（锚点实时窗口：起点固定、终点=现在）
const customRange = ref(null) // [startMs, endMs]（静态自定义范围）
const history = ref(null)
let histTimer = null

const isCustom = computed(() => customRange.value != null)
// 时间选择描述符（趋势 + 效率统计共用）：window / anchor / custom
const timeSel = computed(() => {
  if (isCustom.value) {
    return { type: 'custom', start: Math.floor(customRange.value[0] / 1000), end: Math.floor(customRange.value[1] / 1000) }
  }
  if (anchor.value) return { type: 'anchor', a: anchor.value }
  return { type: 'window', s: winS.value }
})
// 实时窗口长度（秒），每次轮询更新；供 x 轴范围与刻度格式使用
const liveSpan = ref(300)
const xMin = computed(() => (isCustom.value ? customRange.value[0] : null))
const xMax = computed(() => (isCustom.value ? customRange.value[1] : null))
const customSpan = computed(() => (isCustom.value ? (customRange.value[1] - customRange.value[0]) / 1000 : null))

function pickWindow(s) {
  anchor.value = null
  customRange.value = null
  winS.value = s
}

function pickAnchor(a) {
  customRange.value = null
  anchor.value = (anchor.value === a) ? null : a  // 再点同一锚点退回窗口模式
}

function toggleCustom() {
  if (isCustom.value) {
    customRange.value = null  // 退回之前的选择（窗口/锚点）
    return
  }
  anchor.value = null
  customRange.value = [Date.now() - 3600 * 1000, Date.now()]  // 默认最近 1 小时
}

// 当前时间范围 [startSec, endSec]（实时窗口终点=现在，调用时计算，避免 computed 缓存导致 end 过期）
function currentRangeSec() {
  const nowS = Math.floor(Date.now() / 1000)
  if (isCustom.value) return [Math.floor(customRange.value[0] / 1000), Math.floor(customRange.value[1] / 1000)]
  if (anchor.value) return [Math.floor(anchorStartMs(anchor.value) / 1000), nowS]
  return [nowS - winS.value, nowS]
}

// 历史保留期：原始 7 天 / 1 分钟聚合 90 天 → 选择器最多允许 90 天，且不允许未来
function disabledDate(d) {
  return d.getTime() < Date.now() - 90 * 86400 * 1000 || d.getTime() > Date.now()
}

async function loadHistory() {
  // 标签页隐藏时跳过轮询（省 CPU/带宽）；回到前台由 visibilitychange 立即补刷
  if (typeof document !== 'undefined' && document.hidden) return
  const [s, e] = currentRangeSec()
  liveSpan.value = e - s
  try {
    if (!isCustom.value && !anchor.value && winS.value <= 3600) {
      history.value = await api.history(props.id, winS.value)  // 短窗口走内存环形缓冲（不依赖历史存储）
    } else {
      history.value = await api.history(props.id, null, s, e)
    }
  } catch (err) { /* ignore */ }
}

function onVisibilityChange() {
  if (typeof document !== 'undefined' && !document.hidden) loadHistory()
}

function seriesOf(name, opts = {}) {
  const s = history.value && history.value.series ? history.value.series[name] : null
  if (!s) return null
  return { name: opts.name || name, ts: s.ts, values: s.values, color: opts.color, step: opts.step, area: opts.area, stack: opts.stack, markLine: opts.markLine, connectNulls: opts.connectNulls }
}

const chartGen = computed(() => {
  const s = seriesOf('gen_speed', { name: 'gen', color: chartTheme().cyan, area: true })
  return s ? [s] : []
})
const chartPrompt = computed(() => {
  const s = seriesOf('prompt_speed', { name: 'prompt', color: chartTheme().green, area: true })
  return s ? [s] : []
})
const chartGpuUtil = computed(() => {
  const colors = [chartTheme().cyan, chartTheme().green, chartTheme().amber, chartTheme().red]
  const out = []
  for (let i = 0; i < 8; i++) {
    const s = seriesOf(`gpu_util_${i}`, { name: `GPU${i}`, color: colors[i % colors.length] })
    if (s) out.push(s)
  }
  return out
})
const chartGpuMem = computed(() => {
  const colors = [chartTheme().cyan, chartTheme().green, chartTheme().amber, chartTheme().red]
  const out = []
  for (let i = 0; i < 8; i++) {
    const s = seriesOf(`gpu_mem_${i}`, { name: `GPU${i}`, color: colors[i % colors.length] })
    if (s) out.push(s)
  }
  return out
})
const chartGpuTemp = computed(() => {
  const colors = [chartTheme().cyan, chartTheme().green, chartTheme().amber, chartTheme().red]
  const out = []
  for (let i = 0; i < 8; i++) {
    const s = seriesOf(`gpu_temp_${i}`, { name: `GPU${i}`, color: colors[i % colors.length] })
    if (s) out.push(s)
  }
  return out
})
const chartGpuPower = computed(() => {
  const colors = [chartTheme().cyan, chartTheme().green, chartTheme().amber, chartTheme().red]
  const out = []
  for (let i = 0; i < 8; i++) {
    const s = seriesOf(`gpu_power_${i}`, { name: `GPU${i}`, color: colors[i % colors.length] })
    if (s) out.push(s)
  }
  return out
})
const chartCpu = computed(() => {
  const s = seriesOf('cpu', { name: 'CPU', color: chartTheme().cyan, area: true })
  return s ? [s] : []
})
const chartMem = computed(() => {
  const out = []
  const u = seriesOf('mem_used', { name: '已用', color: chartTheme().cyan, area: true, stack: 'mem' })
  const c = seriesOf('mem_buff_cache', { name: 'buff/cache', color: chartTheme().green, area: true, stack: 'mem' })
  if (u) out.push(u)
  if (c) out.push(c)
  return out
})
const chartNet = computed(() => {
  const out = []
  const r = seriesOf('net_rx', { name: '下行', color: chartTheme().cyan })
  const t = seriesOf('net_tx', { name: '上行', color: chartTheme().green })
  if (r) out.push(r)
  if (t) out.push(t)
  return out
})
const chartLoad = computed(() => {
  const out = []
  const l1 = seriesOf('load_1', { name: '1m', color: chartTheme().cyan })
  const l5 = seriesOf('load_5', { name: '5m', color: chartTheme().green })
  const l15 = seriesOf('load_15', { name: '15m', color: chartTheme().amber })
  if (l1) out.push(l1)
  if (l5) out.push(l5)
  if (l15) out.push(l15)
  return out
})
const chartCtx = computed(() => {
  const s = seriesOf('ctx_used', { name: 'n_tokens', color: chartTheme().cyan })
  if (!s) return []
  const total = ctxTotal.value
  if (total) {
    s.markLine = {
      silent: true,
      symbol: 'none',
      lineStyle: { color: chartTheme().red, type: 'dashed', width: 1 },
      label: { color: chartTheme().red, fontSize: 10, formatter: `n_ctx ${fmtNum(total)}` },
      data: [{ yAxis: total }]
    }
  }
  return [s]
})
const chartSpec = computed(() => {
  const s = seriesOf('accept_length', { name: '接受长度', color: chartTheme().green, step: true, connectNulls: true })
  return s ? [s] : []
})
const chartMtp = computed(() => {
  const s = seriesOf('mtp_acceptance', { name: '接受率', color: chartTheme().green, step: true, connectNulls: true })
  if (!s) return []
  s.values = s.values.map((v) => (v === null ? null : v * 100))
  return [s]
})

// 速度卡 60s spark（取自历史序列尾部）
const sparkGen = computed(() => mapTail('gen_speed', (v) => v))
const sparkPrompt = computed(() => mapTail('prompt_speed', (v) => v))

onMounted(() => {
  loadHistory()
  loadUiCfg()
  histTimer = setInterval(() => { if (!isCustom.value) loadHistory() }, 5000)  // 自定义范围为静态历史时段，不轮询
  document.addEventListener('visibilitychange', onVisibilityChange)
  measureTabs()
  window.addEventListener('resize', measureTabs)
  window.addEventListener('scroll', onSecScroll, { passive: true })
  updateSecActive()
})

watch([winS, anchor, customRange], loadHistory)
onBeforeUnmount(() => {
  if (histTimer) clearInterval(histTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  window.removeEventListener('resize', measureTabs)
  window.removeEventListener('scroll', onSecScroll)
  if (secRaf) cancelAnimationFrame(secRaf)
})
</script>

<style scoped>
.content {
  padding: 18px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 22px;
}
.tabs {
  position: sticky;
  top: calc(var(--chrome-top, 0px) + 56px);
  z-index: 19;
  display: flex;
  gap: 6px;
  padding: 14px 24px 0;
  background: color-mix(in srgb, var(--bg) 85%, transparent);
  backdrop-filter: blur(12px);
}
.tabs button {
  padding: 8px 20px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-dim);
  background: transparent;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  cursor: pointer;
  transition: color .15s, background .15s;
}
.tabs button:hover { color: var(--text); }
.tabs button.on {
  color: var(--cyan);
  background: var(--card-bg);
  border-color: var(--card-border);
}
.tab-pane { display: flex; flex-direction: column; gap: 22px; }
/* 监控页左侧滚轮导航（iOS wheel picker）：极小、透明、贴屏幕最左侧；分区名排成 3D 圆柱，
   中间行选中高亮，上下弯向圆柱背面渐隐，滚动时整列像滚轮一样转动 */
.wheel {
  position: fixed;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 20px;
  height: 64px;
  z-index: 16;
}
/* 可视窗口：3D 透视 + 上下渐隐（无背景，透明） */
.wheel-mask {
  position: absolute;
  inset: 0;
  overflow: hidden;
  perspective: 150px;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, #000 30%, #000 70%, transparent 100%);
  mask-image: linear-gradient(to bottom, transparent 0%, #000 30%, #000 70%, transparent 100%);
}
/* 圆柱（随滚动旋转） */
.wheel-inner {
  position: absolute;
  inset: 0;
  transform-style: preserve-3d;
  transition: transform .45s cubic-bezier(.22, .61, .36, 1);
}
/* 单个分区行：贴在圆柱面上 rotateX(角度) translateZ(半径) */
.wheel-item {
  position: absolute;
  left: 0;
  top: 50%;
  width: 100%;
  height: 20px;
  margin-top: -10px;
  line-height: 20px;
  text-align: center;
  font-family: 'JetBrains Mono', 'Roboto Mono', Consolas, monospace;
  font-size: 7px;
  font-weight: 500;
  color: rgba(150, 215, 235, 0.55);
  background: none;
  border: none;
  cursor: pointer;
  transform-origin: 50% 50%;
  backface-visibility: hidden;
  text-shadow: 0 0 3px rgba(0, 229, 255, 0.25);
  transition: color .2s;
}
.wheel-item:hover { color: rgba(200, 248, 255, 0.9); }
.wheel-item.on {
  color: #eafcff;
  font-weight: 600;
  text-shadow: 0 0 4px rgba(0, 229, 255, 0.95), 0 0 9px rgba(0, 229, 255, 0.55);
}
.banner-danger {
  margin: 14px 24px 0;
  padding: 10px 16px;
  border-radius: 8px;
  background: rgba(255, 59, 92, 0.12);
  border: 1px solid rgba(255, 59, 92, 0.5);
  color: var(--red);
  font-weight: 600;
  text-align: center;
  animation: dangerPulse 1.2s infinite;
}
.gauge-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
.task-row {
  display: grid;
  grid-template-columns: minmax(0, 640px) minmax(0, 1fr);
  gap: 12px;
}
.task-row > * { height: 320px; }
.gpu-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.gpu-grid.single { grid-template-columns: 1fr; }
.sys-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}
/* 子面板允许收缩：CPU 核心多时最小内容宽度大，不收缩会把右侧内存面板挤出视口 */
.sys-grid > * { min-width: 0; }
.sys-grid:last-child { margin-bottom: 0; }
.model-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.proc-grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 12px;
}
.proc-side { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.proc-side > .top { flex: 1; }
.trend-title-row { justify-content: flex-start; gap: 16px; }
.win-switch { display: inline-flex; gap: 4px; margin-left: auto; }
.win-switch button {
  background: transparent;
  border: 1px solid var(--card-border);
  color: var(--text-dim);
  font-size: 11px;
  padding: 2px 10px;
  border-radius: 12px;
  cursor: pointer;
}
.win-switch button.on {
  color: var(--cyan);
  border-color: rgba(0, 229, 255, 0.5);
  background: rgba(0, 229, 255, 0.08);
}
/* el-date-picker 根节点是 tooltip 触发器（fragment 渲染），父组件 scoped 属性不会落到它身上，
   直接写 .trend-range[data-v] 永远匹配不到，必须用 :deep() 从带 scoped 属性的父级穿透 */
.trend-title-row :deep(.el-date-editor--datetimerange) { flex: none; width: 300px; max-width: 300px; --el-date-editor-width: 300px; }
.trend-title-row :deep(.el-range-input) { font-size: 12px; }
.trend-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.trend-group {
  grid-column: 1 / -1;
  font-size: 10px;
  letter-spacing: 2px;
  color: var(--text-faint);
  padding: 0 4px;
  border-bottom: 1px solid rgba(143, 163, 200, 0.12);
}
.trend-group:first-child { margin-top: -4px; }
@media (max-width: 1500px) {
  .gpu-grid { grid-template-columns: 1fr; }
  .model-grid { grid-template-columns: 1fr; }
  .proc-grid { grid-template-columns: 1fr; }
  .gauge-row { grid-template-columns: repeat(2, 1fr); }
  .task-row { grid-template-columns: 1fr; }
}
@media (max-width: 1100px) {
  .sys-grid { grid-template-columns: 1fr; }
  .trend-grid { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .gauge-row { grid-template-columns: 1fr; }
}
</style>
