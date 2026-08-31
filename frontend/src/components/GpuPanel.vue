<template>
  <div class="gpu glass" :class="levelClass">
    <div class="gpu-head">
      <span class="gpu-name">
        <svg class="gpu-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
          <rect x="6" y="6" width="12" height="12" rx="1.5" />
          <rect x="9.5" y="9.5" width="5" height="5" rx="0.5" />
          <path d="M9 6V3M12 6V3M15 6V3M9 21v-3M12 21v-3M15 21v-3M6 9H3M6 12H3M6 15H3M21 9h-3M21 12h-3M21 15h-3" />
        </svg>
        GPU{{ gpu.index }} · {{ gpu.name || 'NVIDIA GPU' }}
      </span>
    </div>

    <div class="gpu-body">
      <div class="gauge-wrap">
        <svg class="gauge" viewBox="0 0 100 100">
          <circle cx="50" cy="50" r="42" fill="none" stroke="rgba(143,163,200,0.15)" stroke-width="8" />
          <circle
            cx="50" cy="50" r="42" fill="none"
            :stroke="gaugeColor" stroke-width="8" stroke-linecap="round"
            :stroke-dasharray="dash"
            transform="rotate(-90 50 50)"
            style="transition: stroke-dasharray 0.3s ease; filter: drop-shadow(0 0 4px currentColor)"
          />
        </svg>
        <div class="gauge-center">
          <span class="mono val" :class="levelClass">{{ utilText }}</span>
          <span class="unit">利用率</span>
        </div>
      </div>

      <div class="gpu-metrics">
        <div class="mem-block">
          <div class="mem-label mono small dim">
            显存 {{ fmtNum(gpu.mem_used_mb, 0) }} / {{ fmtNum(gpu.mem_total_mb, 0) }} MB
            <span :class="memLevelClass">{{ memPctText }}</span>
          </div>
          <div class="bar"><i :class="memBarClass" :style="{ width: memPctNum + '%' }"></i></div>
        </div>

        <div class="grid3">
          <div class="kv"><span class="k">温度</span><span class="v mono" :class="tempLevelClass">{{ tempText }}°C</span></div>
          <div class="kv"><span class="k">显存温度</span><span class="v mono">{{ tempMemText }}<template v-if="tempMemText !== '—'">°C</template></span></div>
          <div class="kv"><span class="k">功耗</span><span class="v mono">{{ powerText }} W</span></div>
          <div class="kv"><span class="k">显存利用率</span><span class="v mono">{{ memUtilText }}</span></div>
          <div class="kv"><span class="k">风扇</span><span class="v mono">{{ fanText }}</span></div>
          <div class="kv"><span class="k">频率</span><span class="v mono">{{ clockText }}</span></div>
          <div class="kv"><span class="k">PCIe</span><span class="v mono">gen{{ gpu.pcie_gen ?? '—' }} x{{ gpu.pcie_width ?? '—' }}</span></div>
          <div class="kv"><span class="k">P-State</span><span class="v mono">{{ gpu.pstate || '—' }}</span></div>
          <div class="kv"><span class="k">降频状态</span><span class="v mono" :class="throttleClass">{{ throttleText }}</span></div>
          <div class="kv"><span class="k">ECC 纠错/不可纠</span><span class="v mono" :class="eccClass">{{ eccText }}</span></div>
          <div class="kv"><span class="k">CUDA 版本</span><span class="v mono">{{ cudaText }}</span></div>
          <div class="kv"><span class="k">驱动版本</span><span class="v mono">{{ driverText }}</span></div>
        </div>

        <div v-if="apps.length" class="apps">
          <div class="apps-title small dim">占用进程</div>
          <div v-for="a in apps" :key="a.pid" class="kv small mono">
            <span class="k">{{ a.name }}</span>
            <span class="v">pid {{ a.pid }} · {{ fmtNum(a.mem_mb, 0) }} MB</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtNum, alertOf } from '../utils'

const props = defineProps({
  gpu: { type: Object, required: true },
  alerts: { type: Array, default: () => [] }
})

const idx = computed(() => props.gpu.index ?? 0)
const util = computed(() => props.gpu.util_pct ?? null)
const utilText = computed(() => (util.value === null ? '—' : Math.round(util.value) + '%'))

const CIRC = 2 * Math.PI * 42
const dash = computed(() => {
  const p = util.value === null ? 0 : Math.min(100, util.value)
  return `${(p / 100) * CIRC} ${CIRC}`
})
const gaugeColor = computed(() => {
  const p = util.value
  if (p === null) return 'var(--text-faint)'
  if (p >= 90) return 'var(--red)'
  if (p >= 80) return 'var(--amber)'
  return 'var(--cyan)'
})

const memTotal = computed(() => props.gpu.mem_total_mb || 0)
const memUsed = computed(() => props.gpu.mem_used_mb ?? 0)
const memPctNum = computed(() => (memTotal.value ? Math.min(100, (memUsed.value / memTotal.value) * 100) : 0))
const memPctText = computed(() => (memTotal.value ? (memPctNum.value).toFixed(1) + '%' : '—'))
const memLevel = computed(() => {
  const p = memPctNum.value
  if (p >= 95) return 'danger'
  if (p >= 85) return 'warn'
  return 'normal'
})
const memLevelClass = computed(() => (memLevel.value === 'danger' ? 'lv-danger' : memLevel.value === 'warn' ? 'lv-warn' : 'lv-green'))
const memBarClass = computed(() => (memLevel.value === 'danger' ? 'danger' : memLevel.value === 'warn' ? 'warn' : 'green'))

const temp = computed(() => props.gpu.temp_c ?? null)
const tempText = computed(() => (temp.value === null ? '—' : Math.round(temp.value)))
const tempLevelClass = computed(() => {
  const t = temp.value
  if (t === null) return ''
  if (t >= 85) return 'lv-danger'
  if (t >= 75) return 'lv-warn'
  return ''
})

const powerText = computed(() => {
  const p = props.gpu.power_w
  const lim = props.gpu.power_limit_w
  if (p === null || p === undefined) return '—'
  return `${p.toFixed(1)}${lim ? ' / ' + lim.toFixed(0) : ''}`
})
const fanText = computed(() => (props.gpu.fan_pct === null || props.gpu.fan_pct === undefined ? '—' : Math.round(props.gpu.fan_pct) + '%'))
const clockText = computed(() => {
  const c = props.gpu.clock_mhz
  const mc = props.gpu.mem_clock_mhz
  if (!c && !mc) return '—'
  return `${c ?? '—'} / ${mc ?? '—'} MHz`
})

const memUtilText = computed(() => {
  const u = props.gpu.mem_util_pct
  return u === null || u === undefined ? '—' : Math.round(u) + '%'
})
const tempMem = computed(() => props.gpu.temp_mem_c ?? null)
const tempMemText = computed(() => (tempMem.value === null ? '—' : Math.round(tempMem.value)))
const eccText = computed(() => {
  const c = props.gpu.ecc_corrected
  const u = props.gpu.ecc_uncorrected
  if (c === null || c === undefined || u === null || u === undefined) return '—'
  return `${fmtNum(c)} / ${fmtNum(u)}`
})
const eccClass = computed(() => ((props.gpu.ecc_uncorrected || 0) > 0 ? 'lv-danger' : ''))

// clocks_throttle_reasons.active 位掩码 → 中文短名（常见位）
const THROTTLE_BITS = [
  [0x1, '硬件降频'], [0x2, '热降频'], [0x4, '功耗墙'], [0x8, '热降频'],
  [0x10, '热降频'], [0x20, '功率刹车'], [0x40, '热降频'], [0x80, 'SW Fast Switch'],
  [0x100, '热功率刹车'], [0x200, 'SW FLIP'], [0x400, 'HW FLIP'], [0x800, '热功率刹车'],
  [0x1000, '功率刹车']
]
// 0x1 (HW Throttle) 在 GPU 空闲+低温时被驱动错误设置（时钟回到低频节能状态），
// 非真正的硬件保护降频。忽略条件：利用率<5% 且 温度<70°C
const HW_THROTTLE_FAKE_THRESHOLD = { util: 5, temp: 70 }
const throttleNames = computed(() => {
  const t = props.gpu.throttle
  if (t === null || t === undefined) return null
  if (t === 0) return []
  const util = props.gpu.util_pct ?? -1
  const temp = props.gpu.temp_c ?? 100
  const isIdle = util < HW_THROTTLE_FAKE_THRESHOLD.util && temp < HW_THROTTLE_FAKE_THRESHOLD.temp
  const names = []
  for (const [bit, name] of THROTTLE_BITS) {
    if (t & bit) {
      if (bit === 0x1 && isIdle) continue  // 假阳性：空闲低温时的 0x1 位
      names.push(name)
    }
  }
  return names
})
const throttleText = computed(() => {
  const names = throttleNames.value
  if (names === null) return '—'
  if (!names.length) return '正常'
  return [...new Set(names)].join('、')
})
const throttleClass = computed(() => {
  const names = throttleNames.value
  if (names === null) return ''
  return names.length ? 'lv-warn' : ''
})

const apps = computed(() => props.gpu.apps || [])

const cudaText = computed(() => props.gpu.cuda || '—')
const driverText = computed(() => props.gpu.driver || '—')

// 卡片级别：取该卡所有告警的最高级别
const cardLevel = computed(() => {
  let level = 'normal'
  for (const a of props.alerts || []) {
    if (!a.metric.startsWith(`gpu${idx.value}.`)) continue
    if (a.level === 'danger') return 'danger'
    if (a.level === 'warn') level = 'warn'
  }
  return level
})
const levelClass = computed(() => (cardLevel.value === 'danger' ? 'card-danger' : cardLevel.value === 'warn' ? 'card-warn' : ''))
</script>

<style scoped>
.gpu { padding: 12px 14px; }
.gpu-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
.gpu-name { font-size: 13px; font-weight: 600; display: inline-flex; align-items: center; gap: 8px; }
.gpu-icon { width: 18px; height: 18px; color: var(--cyan); filter: drop-shadow(0 0 4px rgba(0, 229, 255, 0.5)); flex: none; }
.gpu-body { display: flex; gap: 14px; align-items: center; }
.gauge-wrap { position: relative; width: 88px; height: 88px; flex: none; }
.gauge { width: 100%; height: 100%; }
.gauge-center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.gauge-center .val { font-size: 18px; font-weight: 700; color: var(--text); }
.gauge-center .unit { font-size: 10px; color: var(--text-faint); }
.gpu-metrics { flex: 1; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.mem-label { display: flex; gap: 8px; margin-bottom: 4px; }
.mem-label span:last-child { margin-left: auto; font-weight: 700; }
.grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0 14px; }
.grid3 .kv { padding: 1px 0; font-size: 11px; }
.lv-green { color: var(--green); }
.apps { border-top: 1px solid rgba(143, 163, 200, 0.1); padding-top: 6px; }
.apps-title { margin-bottom: 2px; }
</style>
