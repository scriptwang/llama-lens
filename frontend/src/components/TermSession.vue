<template>
  <div class="term-host-wrap">
    <div ref="termHost" class="term-host" @contextmenu.prevent="onContextMenu" />
    <div v-if="status === 'idle' || status === 'connecting'" class="term-overlay">
      <span v-if="status === 'connecting'">正在连接 {{ hostLabel || '服务器' }} …</span>
      <template v-else>
        <span>终端未连接</span>
        <el-button size="small" type="primary" @click="connect">连接</el-button>
      </template>
    </div>
    <div v-else-if="status === 'error' || status === 'closed'" class="term-overlay">
      <span>{{ status === 'error' ? (errMsg || '连接失败') : '会话已结束' }}</span>
      <el-button size="small" type="primary" @click="reconnect">重新连接</el-button>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { ClipboardAddon } from '@xterm/addon-clipboard'
import '@xterm/xterm/css/xterm.css'
import { themeState, isDarkTheme } from '../theme'

// xterm 配色随明暗主题切换（浅色主题下终端不再是黑块，与整体浅色一致）
const XTERM_DARK = {
  background: '#161822',
  foreground: '#d8dce8',
  cursor: '#7aa2f7',
  selectionBackground: '#3d4466',
}
const XTERM_LIGHT = {
  background: '#f6f8fa',
  foreground: '#24292f',
  cursor: '#0969da',
  selectionBackground: 'rgba(9, 105, 218, 0.22)',
}
function xtermTheme() {
  return isDarkTheme(themeState.id) ? XTERM_DARK : XTERM_LIGHT
}

// 单个终端会话：独立 xterm + 独立 WS（后端每个连接开独立 SSH shell，互不干扰）
const props = defineProps({
  id: Number,
  dbId: { type: Number, default: null },
  active: Boolean,
  hostLabel: { type: String, default: '' },
  tmuxName: { type: String, default: '' },
})
const emit = defineEmits(['status', 'persist'])

const termHost = ref(null)
const status = ref('idle')  // idle | connecting | connected | closed | error
const errMsg = ref('')
let term = null
let fit = null
let ws = null
let ro = null

watch(status, (v) => emit('status', { id: props.id, status: v }))

function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = encodeURIComponent(localStorage.getItem('llama_token') || '')
  const sess = props.tmuxName ? `&session=${encodeURIComponent(props.tmuxName)}` : ''
  return `${proto}://${location.host}/api/hosts/${props.dbId}/terminal/ws?token=${token}${sess}`
}

function initTerm() {
  if (term || !termHost.value) return
  term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: 'ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace',
    theme: xtermTheme(),
    scrollback: 5000,
    allowProposedApi: true,
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.loadAddon(new WebLinksAddon())
  term.loadAddon(new ClipboardAddon())
  term.open(termHost.value)
  try { fit.fit() } catch { /* 隐藏时忽略 */ }
  // Ctrl/Cmd+C：有选区=复制（放行浏览器原生 copy 事件），无选区=发 ^C（SIGINT）
  // Ctrl/Cmd+V：粘贴（放行浏览器原生 paste 事件）
  // 默认 xterm.js 会把 Ctrl+C/V 转成 ^C/^V 发给 shell 并 preventDefault，导致复制/粘贴失效
  term.attachCustomKeyEventHandler((ev) => {
    if (ev.type === 'keydown' && (ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey) {
      if (ev.code === 'KeyC') return !term.hasSelection()
      if (ev.code === 'KeyV') return false
    }
    return true
  })
  term.onData((d) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'input', data: d }))
  })
  // 选中即复制（鼠标松开时）
  termHost.value.addEventListener('mouseup', () => {
    const sel = term?.getSelection()
    if (sel) void copyText(sel)
  })
  ro = new ResizeObserver(() => {
    const el = termHost.value
    // 隐藏（切走 Tab）时尺寸为 0：忽略，避免把 shell 收缩成 2x1
    if (!el || el.clientWidth === 0 || el.clientHeight === 0) return
    try {
      fit.fit()
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
      }
    } catch { /* 隐藏时忽略 */ }
  })
  ro.observe(termHost.value)
}

function connect() {
  if (!props.dbId) return
  if (ws) { try { ws.close() } catch { /* ignore */ } ws = null }
  term?.clear()
  errMsg.value = ''
  status.value = 'connecting'
  ws = new WebSocket(wsUrl())
  ws.onopen = () => {
    if (term) ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
  }
  ws.onmessage = (ev) => {
    let m
    try { m = JSON.parse(ev.data) } catch { return }
    if (m.type === 'output') term?.write(m.data)
    else if (m.type === 'status') {
      if (m.state === 'ready') { status.value = 'connected'; emit('persist', { id: props.id, persist: !!m.persist }) }
      else if (m.state === 'closed') status.value = 'closed'
      else if (m.state === 'error') { status.value = 'error'; errMsg.value = m.reason || '' }
    }
  }
  ws.onerror = () => { if (status.value !== 'connected') { status.value = 'error'; errMsg.value = 'WebSocket 连接错误' } }
  ws.onclose = () => { if (status.value === 'connected' || status.value === 'connecting') status.value = 'closed' }
}

function reconnect() { connect() }
function clearTerm() { term?.clear() }
// 关闭标签时通知后端杀 tmux 会话（WS 断开本身不杀，刷新后要重连恢复）
function kill() {
  if (props.tmuxName && ws && ws.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify({ type: 'kill' })) } catch { /* ignore */ }
  }
}

// ---------------- 剪贴板（右键：有选区=复制，无选区=粘贴；选中即复制） ----------------
// 兼容非安全上下文（http://dev.lan）：Clipboard API 不可用时回退 execCommand
async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* 继续回退 */ }
  try {
    const prev = document.activeElement
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const okc = document.execCommand('copy')
    ta.remove()
    // 回退路径会偷焦点：不还原则终端收不到按键（vim 里表现为"卡住"）
    if (prev && prev !== document.body && prev.focus) prev.focus()
    return okc
  } catch { return false }
}
// http（非安全上下文）兜底：浮层 textarea 走浏览器原生粘贴（Chrome 下唯一可靠路径，
// 粘贴到输入框始终放行），读值后 term.paste 发给终端 —— 不依赖 Clipboard API
let pasteBox = null
let pasteBoxCleanup = null
function closePasteBox() {
  if (pasteBoxCleanup) { pasteBoxCleanup(); pasteBoxCleanup = null }
  if (pasteBox) { pasteBox.remove(); pasteBox = null }
}
function openPasteBox() {
  closePasteBox()
  const box = document.createElement('div')
  box.className = 'term-paste-box'
  const ta = document.createElement('textarea')
  ta.placeholder = '在此按 Ctrl+V 粘贴，Enter 发送到终端（Esc 取消）'
  box.appendChild(ta)
  document.body.appendChild(box)
  pasteBox = box
  ta.focus()
  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault()
      const v = ta.value
      closePasteBox()
      if (v) { term?.paste(v); term?.focus() }
    } else if (e.key === 'Escape') {
      closePasteBox()
    }
  }
  const onDocDown = (e) => { if (!box.contains(e.target)) closePasteBox() }
  pasteBoxCleanup = () => {
    document.removeEventListener('keydown', onKey, true)
    document.removeEventListener('mousedown', onDocDown)
  }
  document.addEventListener('keydown', onKey, true)
  setTimeout(() => document.addEventListener('mousedown', onDocDown), 0)
}
async function pasteText() {
  term?.focus()
  try {
    if (navigator.clipboard?.readText) {
      const text = await navigator.clipboard.readText()
      if (text) { term?.paste(text); return }
    }
  } catch { /* 继续回退 */ }
  // 回退：聚焦 xterm 隐藏输入框触发浏览器原生粘贴（Firefox 可用）
  try {
    const ta = term?.textarea
    if (ta) {
      ta.focus()
      if (document.execCommand('paste')) return
    }
  } catch { /* ignore */ }
  // Chrome + http：Clipboard API 不可用 → 浮层原生粘贴
  openPasteBox()
}
function onContextMenu(e) {
  e.preventDefault()
  const sel = term?.getSelection()
  if (sel) {
    void copyText(sel).then((okc) => {
      if (okc) ElMessage.success('已复制选中文本')
      else ElMessage.warning('复制失败，请选中文字后按 Ctrl+C')
    })
  } else {
    void pasteText()
  }
}

// ---------------- 生命周期 ----------------
onMounted(() => {
  initTerm()
  if (props.dbId && props.active) connect()
})
watch(() => props.dbId, (v) => {
  if (v && (status.value === 'idle' || status.value === 'error' || status.value === 'closed')) connect()
})
// 主题切换时实时更新 xterm 配色（无需重建终端）
watch(() => themeState.version, () => {
  if (term) term.options.theme = xtermTheme()
})
watch(() => props.active, (a) => {
  if (a) {
    nextTick(() => {
      initTerm()
      try {
        fit?.fit()
        if (ws && ws.readyState === WebSocket.OPEN && term) {
          ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
        }
      } catch { /* 忽略 */ }
    })
  } else {
    closePasteBox()
  }
})
onBeforeUnmount(() => {
  closePasteBox()
  ro?.disconnect()
  if (ws) { try { ws.close() } catch { /* ignore */ } }
  term?.dispose()
})

defineExpose({ clear: clearTerm, reconnect, paste: pasteText, kill })
</script>

<style scoped>
.term-host-wrap { position: absolute; inset: 0; border-radius: 10px; overflow: hidden; border: 1px solid var(--lc-border, #333); padding: 8px 4px 4px 10px; background: #161822; }
.term-host { width: 100%; height: 100%; }
.term-overlay { position: absolute; inset: 0; display: flex; flex-direction: column; gap: 12px; align-items: center; justify-content: center; background: rgba(22, 24, 34, 0.82); color: #aab; font-size: 13px; }
</style>

<style>
.term-paste-box { position: fixed; top: 18%; left: 50%; transform: translateX(-50%); width: min(560px, 82vw); z-index: 3000; background: #1e2230; border: 1px solid #444c66; border-radius: 8px; padding: 10px; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.5); }
.term-paste-box textarea { width: 100%; height: 96px; resize: vertical; background: #12141c; color: #d8dce8; border: 1px solid #333a4d; border-radius: 6px; padding: 8px; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; outline: none; }
.term-paste-box textarea:focus { border-color: #7aa2f7; }
</style>
