import { reactive } from 'vue'

// ---------------- 主题定义 ----------------
// aurora   = 现有深色玻璃拟态风格（默认）
// terminal = 现代终端风格（macOS 窗口 + 等宽字体 + ANSI 配色 + tmux 状态栏）
export const THEMES = [
  { id: 'aurora', label: 'Aurora 极光' },
  { id: 'terminal', label: 'Terminal 终端' },
  { id: 'light', label: 'Light 浅色' },
  { id: 'monokai', label: 'Monokai' },
  { id: 'nord', label: 'Nord' },
  { id: 'dracula', label: 'Dracula' },
  { id: 'synthwave', label: "Synthwave '84" },
  { id: 'tokyonight', label: 'Tokyo Night' },
  { id: 'matrix', label: 'Matrix' }
]

const STORAGE_KEY = 'llamalens.theme'

// 深色主题集合：同步 html.dark 类（Element Plus 暗色变量与管理区暗色样式依赖它）
const DARK_THEMES = new Set(['aurora', 'terminal', 'monokai', 'nord', 'dracula', 'synthwave', 'tokyonight', 'matrix'])

export function isDarkTheme(id) {
  return DARK_THEMES.has(id)
}

function applyThemeAttrs(id) {
  document.documentElement.setAttribute('data-theme', id)
  document.documentElement.classList.toggle('dark', isDarkTheme(id))
}

function storedTheme() {
  try {
    const t = localStorage.getItem(STORAGE_KEY)
    return THEMES.some((x) => x.id === t) ? t : null
  } catch (e) {
    return null
  }
}

export const themeState = reactive({
  id: storedTheme() || 'aurora',
  version: 0
})

export function isTerminal() {
  return themeState.id === 'terminal'
}

export function setTheme(id) {
  if (!THEMES.some((t) => t.id === id) || id === themeState.id) return
  themeState.id = id
  themeState.version++
  applyThemeAttrs(id)
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch (e) { /* 隐私模式下忽略 */ }
}

// 首帧前应用持久化主题（index.html 内联脚本已做同样处理，这里兜底）
export function initTheme() {
  applyThemeAttrs(themeState.id)
}

// ---------------- 图表主题（ECharts / sparkline 颜色） ----------------
// aurora 的取值与改造前硬编码值完全一致，保证默认主题零视觉回归
const CHART_THEMES = {
  aurora: {
    palette: ['#00e5ff', '#00ff9d', '#ffc53d', '#ff3b5c', '#7c8cff', '#ff8f5c', '#c792ea'],
    cyan: '#00e5ff',
    green: '#00ff9d',
    amber: '#ffc53d',
    red: '#ff3b5c',
    text: '#e6f1ff',
    dim: '#8fa3c8',
    faint: '#5a6b8c',
    axisLine: 'rgba(143, 163, 200, 0.3)',
    splitLine: 'rgba(143, 163, 200, 0.12)',
    tooltipBg: 'rgba(10, 14, 23, 0.94)',
    tooltipBorder: 'rgba(0, 229, 255, 0.3)',
    tooltipText: '#e6f1ff',
    axisPointer: 'rgba(0, 229, 255, 0.4)',
    glow: true,
    gaugeZones: [
      [0.8, 'rgba(0,229,255,0.10)'],
      [0.9, 'rgba(255,197,61,0.16)'],
      [1, 'rgba(255,59,92,0.18)']
    ],
    gaugeAnchorBg: '#0d1526',
    gaugeTick: 'rgba(143,163,200,0.28)',
    gaugeSplit: 'rgba(143,163,200,0.5)',
    mtpZones: [
      [0.65, 'rgba(255,59,92,0.16)'],
      [0.8, 'rgba(255,197,61,0.16)'],
      [1, 'rgba(0,255,157,0.12)']
    ],
    mtpZoneColors: [
      [0.65, '#ff3b5c'],
      [0.8, '#ffc53d'],
      [1, '#00ff9d']
    ]
  },
  terminal: {
    palette: ['#56b6c2', '#98c379', '#e5c07b', '#e06c75', '#c678dd', '#d19a66', '#787880'],
    cyan: '#56b6c2',
    green: '#98c379',
    amber: '#e5c07b',
    red: '#e06c75',
    text: '#c8c8cc',
    dim: '#787880',
    faint: '#48484e',
    axisLine: '#36363c',
    splitLine: '#242428',
    tooltipBg: '#18181c',
    tooltipBorder: '#36363c',
    tooltipText: '#c8c8cc',
    axisPointer: '#48484e',
    glow: false,
    gaugeZones: [
      [0.8, 'rgba(86,182,194,0.10)'],
      [0.9, 'rgba(229,192,123,0.16)'],
      [1, 'rgba(224,108,117,0.18)']
    ],
    gaugeAnchorBg: '#141416',
    gaugeTick: 'rgba(120,120,128,0.28)',
    gaugeSplit: 'rgba(120,120,128,0.5)',
    mtpZones: [
      [0.65, 'rgba(224,108,117,0.16)'],
      [0.8, 'rgba(229,192,123,0.16)'],
      [1, 'rgba(152,195,121,0.12)']
    ],
    mtpZoneColors: [
      [0.65, '#e06c75'],
      [0.8, '#e5c07b'],
      [1, '#98c379']
    ]
  },
  light: {
    palette: ['#0969da', '#1a7f37', '#9a6700', '#cf222e', '#8250df', '#bc4c00', '#57606a'],
    cyan: '#0969da',
    green: '#1a7f37',
    amber: '#9a6700',
    red: '#cf222e',
    text: '#24292f',
    dim: '#57606a',
    faint: '#8c959f',
    axisLine: 'rgba(87, 96, 106, 0.3)',
    splitLine: 'rgba(87, 96, 106, 0.15)',
    tooltipBg: 'rgba(255, 255, 255, 0.96)',
    tooltipBorder: '#d0d7de',
    tooltipText: '#24292f',
    axisPointer: 'rgba(9, 105, 218, 0.4)',
    glow: false,
    gaugeZones: [
      [0.8, 'rgba(9,105,218,0.10)'],
      [0.9, 'rgba(154,103,0,0.16)'],
      [1, 'rgba(207,34,46,0.18)']
    ],
    gaugeAnchorBg: '#f6f8fa',
    gaugeTick: 'rgba(87,96,106,0.28)',
    gaugeSplit: 'rgba(87,96,106,0.5)',
    mtpZones: [
      [0.65, 'rgba(207,34,46,0.16)'],
      [0.8, 'rgba(154,103,0,0.16)'],
      [1, 'rgba(26,127,55,0.12)']
    ],
    mtpZoneColors: [
      [0.65, '#cf222e'],
      [0.8, '#9a6700'],
      [1, '#1a7f37']
    ]
  },
  monokai: {
    palette: ['#66d9ef', '#a6e22e', '#e6db74', '#f92672', '#ae81ff', '#fd971f', '#939293'],
    cyan: '#66d9ef',
    green: '#a6e22e',
    amber: '#e6db74',
    red: '#f92672',
    text: '#f8f8f2',
    dim: '#939293',
    faint: '#75715e',
    axisLine: 'rgba(147, 146, 147, 0.35)',
    splitLine: 'rgba(147, 146, 147, 0.14)',
    tooltipBg: 'rgba(30, 31, 26, 0.95)',
    tooltipBorder: 'rgba(230, 219, 116, 0.3)',
    tooltipText: '#f8f8f2',
    axisPointer: 'rgba(102, 217, 239, 0.4)',
    glow: true,
    gaugeZones: [
      [0.8, 'rgba(102,217,239,0.10)'],
      [0.9, 'rgba(230,219,116,0.16)'],
      [1, 'rgba(249,38,114,0.18)']
    ],
    gaugeAnchorBg: '#1e1f1a',
    gaugeTick: 'rgba(147,146,147,0.28)',
    gaugeSplit: 'rgba(147,146,147,0.5)',
    mtpZones: [
      [0.65, 'rgba(249,38,114,0.16)'],
      [0.8, 'rgba(230,219,116,0.16)'],
      [1, 'rgba(166,226,46,0.12)']
    ],
    mtpZoneColors: [
      [0.65, '#f92672'],
      [0.8, '#e6db74'],
      [1, '#a6e22e']
    ]
  },
  nord: {
    palette: ['#88c0d0', '#a3be8c', '#ebcb8b', '#bf616a', '#b48ead', '#d08770', '#7b88a1'],
    cyan: '#88c0d0',
    green: '#a3be8c',
    amber: '#ebcb8b',
    red: '#bf616a',
    text: '#eceff4',
    dim: '#d8dee9',
    faint: '#7b88a1',
    axisLine: 'rgba(216, 222, 233, 0.3)',
    splitLine: 'rgba(216, 222, 233, 0.12)',
    tooltipBg: 'rgba(43, 48, 59, 0.95)',
    tooltipBorder: 'rgba(136, 192, 208, 0.3)',
    tooltipText: '#eceff4',
    axisPointer: 'rgba(136, 192, 208, 0.4)',
    glow: false,
    gaugeZones: [
      [0.8, 'rgba(136,192,208,0.10)'],
      [0.9, 'rgba(235,203,139,0.16)'],
      [1, 'rgba(191,97,106,0.18)']
    ],
    gaugeAnchorBg: '#2b303b',
    gaugeTick: 'rgba(216,222,233,0.28)',
    gaugeSplit: 'rgba(216,222,233,0.5)',
    mtpZones: [
      [0.65, 'rgba(191,97,106,0.16)'],
      [0.8, 'rgba(235,203,139,0.16)'],
      [1, 'rgba(163,190,140,0.12)']
    ],
    mtpZoneColors: [
      [0.65, '#bf616a'],
      [0.8, '#ebcb8b'],
      [1, '#a3be8c']
    ]
  },
  dracula: {
    palette: ['#8be9fd', '#50fa7b', '#f1fa8c', '#ff5555', '#bd93f9', '#ffb86c', '#9ca0c0'],
    cyan: '#8be9fd',
    green: '#50fa7b',
    amber: '#f1fa8c',
    red: '#ff5555',
    text: '#f8f8f2',
    dim: '#9ca0c0',
    faint: '#6272a4',
    axisLine: 'rgba(156, 160, 192, 0.35)',
    splitLine: 'rgba(156, 160, 192, 0.14)',
    tooltipBg: 'rgba(33, 34, 44, 0.95)',
    tooltipBorder: 'rgba(189, 147, 249, 0.3)',
    tooltipText: '#f8f8f2',
    axisPointer: 'rgba(139, 233, 253, 0.4)',
    glow: true,
    gaugeZones: [
      [0.8, 'rgba(139,233,253,0.10)'],
      [0.9, 'rgba(241,250,140,0.16)'],
      [1, 'rgba(255,85,85,0.18)']
    ],
    gaugeAnchorBg: '#21222c',
    gaugeTick: 'rgba(156,160,192,0.28)',
    gaugeSplit: 'rgba(156,160,192,0.5)',
    mtpZones: [
      [0.65, 'rgba(255,85,85,0.16)'],
      [0.8, 'rgba(241,250,140,0.16)'],
      [1, 'rgba(80,250,123,0.12)']
    ],
    mtpZoneColors: [
      [0.65, '#ff5555'],
      [0.8, '#f1fa8c'],
      [1, '#50fa7b']
    ]
  },
  synthwave: {
    palette: ['#05f4ea', '#72f1b1', '#fde74c', '#ff5555', '#ff7edb', '#b388ff', '#a9a1d1'],
    cyan: '#05f4ea',
    green: '#72f1b1',
    amber: '#fde74c',
    red: '#ff5555',
    text: '#f0efff',
    dim: '#a9a1d1',
    faint: '#6e6a8f',
    axisLine: 'rgba(169, 161, 209, 0.35)',
    splitLine: 'rgba(169, 161, 209, 0.14)',
    tooltipBg: 'rgba(30, 27, 46, 0.95)',
    tooltipBorder: 'rgba(255, 126, 219, 0.3)',
    tooltipText: '#f0efff',
    axisPointer: 'rgba(5, 244, 234, 0.4)',
    glow: true,
    gaugeZones: [
      [0.8, 'rgba(5,244,234,0.10)'],
      [0.9, 'rgba(253,231,76,0.16)'],
      [1, 'rgba(255,85,85,0.18)']
    ],
    gaugeAnchorBg: '#1e1b2e',
    gaugeTick: 'rgba(169,161,209,0.28)',
    gaugeSplit: 'rgba(169,161,209,0.5)',
    mtpZones: [
      [0.65, 'rgba(255,85,85,0.16)'],
      [0.8, 'rgba(253,231,76,0.16)'],
      [1, 'rgba(114,241,177,0.12)']
    ],
    mtpZoneColors: [
      [0.65, '#ff5555'],
      [0.8, '#fde74c'],
      [1, '#72f1b1']
    ]
  },
  tokyonight: {
    palette: ['#7dcfff', '#9ece6a', '#e0af68', '#f7768e', '#bb9af7', '#7aa2f7', '#9aa5ce'],
    cyan: '#7dcfff',
    green: '#9ece6a',
    amber: '#e0af68',
    red: '#f7768e',
    text: '#c0caf5',
    dim: '#9aa5ce',
    faint: '#565f89',
    axisLine: 'rgba(154, 165, 206, 0.35)',
    splitLine: 'rgba(154, 165, 206, 0.14)',
    tooltipBg: 'rgba(22, 22, 30, 0.95)',
    tooltipBorder: 'rgba(122, 162, 247, 0.3)',
    tooltipText: '#c0caf5',
    axisPointer: 'rgba(125, 207, 255, 0.4)',
    glow: true,
    gaugeZones: [
      [0.8, 'rgba(125,207,255,0.10)'],
      [0.9, 'rgba(224,175,104,0.16)'],
      [1, 'rgba(247,118,142,0.18)']
    ],
    gaugeAnchorBg: '#16161e',
    gaugeTick: 'rgba(154,165,206,0.28)',
    gaugeSplit: 'rgba(154,165,206,0.5)',
    mtpZones: [
      [0.65, 'rgba(247,118,142,0.16)'],
      [0.8, 'rgba(224,175,104,0.16)'],
      [1, 'rgba(158,206,106,0.12)']
    ],
    mtpZoneColors: [
      [0.65, '#f7768e'],
      [0.8, '#e0af68'],
      [1, '#9ece6a']
    ]
  },
  matrix: {
    palette: ['#00ff41', '#aaff00', '#33ff77', '#ff003c', '#00cc33', '#66ff99', '#008822'],
    cyan: '#00ff41',
    green: '#00ff41',
    amber: '#aaff00',
    red: '#ff003c',
    text: '#00ff41',
    dim: '#00cc33',
    faint: '#008822',
    axisLine: 'rgba(0, 255, 65, 0.35)',
    splitLine: 'rgba(0, 255, 65, 0.12)',
    tooltipBg: 'rgba(5, 8, 5, 0.95)',
    tooltipBorder: 'rgba(0, 255, 65, 0.3)',
    tooltipText: '#00ff41',
    axisPointer: 'rgba(0, 255, 65, 0.4)',
    glow: true,
    gaugeZones: [
      [0.8, 'rgba(0,255,65,0.10)'],
      [0.9, 'rgba(170,255,0,0.16)'],
      [1, 'rgba(255,0,60,0.18)']
    ],
    gaugeAnchorBg: '#0a0e0a',
    gaugeTick: 'rgba(0,255,65,0.28)',
    gaugeSplit: 'rgba(0,255,65,0.5)',
    mtpZones: [
      [0.65, 'rgba(255,0,60,0.16)'],
      [0.8, 'rgba(170,255,0,0.16)'],
      [1, 'rgba(0,255,65,0.12)']
    ],
    mtpZoneColors: [
      [0.65, '#ff003c'],
      [0.8, '#aaff00'],
      [1, '#00ff41']
    ]
  }
}

// 在 computed 内调用可自动跟踪 themeState.id，主题切换时触发重算
export function chartTheme() {
  return CHART_THEMES[themeState.id] || CHART_THEMES.aurora
}

export function palette() {
  return chartTheme().palette
}
