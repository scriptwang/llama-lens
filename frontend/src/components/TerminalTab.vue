<template>
  <div class="tab-pane term-scope">
    <div class="term-toolbar">
      <span class="term-status" :class="'st-' + status">
        <span class="st-dot" />{{ statusText }}
      </span>
      <span v-if="hostLabel" class="term-host">{{ hostLabel }}</span>
      <span class="toolbar-spacer" />
      <el-button size="small" @click="clearTerm">清屏</el-button>
      <el-button size="small" type="warning" plain :disabled="status !== 'connected'" @click="reconnect">重连</el-button>
      <el-button size="small" :type="filePanel ? 'primary' : 'default'" :plain="!filePanel" @click="filePanel = !filePanel">
        <el-icon style="margin-right:3px"><FolderOpened /></el-icon>文件
      </el-button>
    </div>
    <div class="term-body">
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
      <aside v-if="filePanel" class="file-panel">
        <div class="fp-bar">
          <el-input v-model="fpPath" size="small" placeholder="/root" @keyup.enter="fpLoad" />
          <el-button size="small" @click="fpUp">上级</el-button>
          <el-button size="small" @click="fpLoad">进入</el-button>
          <el-button size="small" :type="fpIsFav ? 'warning' : 'default'" :plain="!fpIsFav" @click="fpToggleFav"
                     :title="fpIsFav ? '取消收藏当前目录' : '收藏当前目录'">
            <el-icon><Star /></el-icon>
          </el-button>
        </div>
        <div class="fp-search">
          <el-input v-model="fpQuery" size="small" clearable :prefix-icon="Search" placeholder="搜索当前目录文件名…" />
        </div>
        <div class="fp-actions">
          <el-dropdown trigger="click" @command="fpGoFav">
            <el-button size="small"><el-icon style="margin-right:3px"><Star /></el-icon>收藏</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="!fpFavs.length" disabled>暂无收藏目录</el-dropdown-item>
                <el-dropdown-item v-for="p in fpFavs" :key="p" :command="p" class="fav-item">
                  <span class="fav-path" :title="p">{{ p }}</span>
                  <el-icon class="fav-del" @click.stop="fpRemoveFav(p)"><Close /></el-icon>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button size="small" type="primary" plain :loading="fpUploading > 0" @click="pickFiles">
            上传{{ fpUploading ? `（${fpUploading}）` : '' }}
          </el-button>
          <el-button size="small" @click="fpMkdir">新建目录</el-button>
          <el-button size="small" @click="fpLoad">刷新</el-button>
          <input ref="fileInput" type="file" multiple style="display:none" @change="onFilesPicked" />
        </div>
        <div v-if="fpUploading > 0" class="fp-up-progress">
          <span class="fp-up-label" :title="fpUploadName">{{ fpUploadPos ? `${fpUploadPos} · ` : '' }}{{ fpUploadName }}</span>
          <el-progress :percentage="fpUploadProgress" :stroke-width="8" class="fp-up-bar" />
        </div>
        <div v-loading="fpLoading" class="fp-list">
          <div v-for="e in fpPageEntries" :key="e.name" class="fp-row" :class="{ dir: e.is_dir }"
               :title="e.name" @click="fpOpen(e)">
            <el-icon class="fp-ico"><Folder v-if="e.is_dir" /><Document v-else /></el-icon>
            <span class="fp-name">{{ e.name }}</span>
            <span class="fp-meta">{{ e.is_dir ? '—' : fmtSize(e.size) }}</span>
            <span class="fp-meta">{{ fmtTime(e.mtime) }}</span>
            <span class="fp-ops">
              <el-button v-if="!e.is_dir" link size="small" @click.stop="fpDownload(e)">下载</el-button>
              <el-button link size="small" type="danger" @click.stop="fpDelete(e)">删除</el-button>
            </span>
          </div>
          <div v-if="!fpFilteredEntries.length && !fpLoading" class="fp-empty">{{ fpQuery ? '无匹配文件' : '空目录' }}</div>
        </div>
        <div class="fp-pager">
          <span class="fp-pager-info">
            {{ fpQuery ? `匹配 ${fpFilteredEntries.length}/${fpEntries.length} 项` : `共 ${fpEntries.length} 项` }}
            <template v-if="fpPageCount > 1"> · 第 {{ fpPage }}/{{ fpPageCount }} 页</template>
          </span>
          <el-button v-if="fpPageCount > 1" size="small" :disabled="fpPage <= 1" @click="fpPage--">上一页</el-button>
          <el-button v-if="fpPageCount > 1" size="small" :disabled="fpPage >= fpPageCount" @click="fpPage++">下一页</el-button>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { ClipboardAddon } from '@xterm/addon-clipboard'
import { Search, Star } from '@element-plus/icons-vue'
import '@xterm/xterm/css/xterm.css'
import http from '../api/client'
import { api } from '../api'

const props = defineProps({
  hostId: String,
  active: Boolean,
  hostLabel: { type: String, default: '' },
})

// ---------------- 终端 ----------------
const termHost = ref(null)
const status = ref('idle')  // idle | connecting | connected | closed | error
const errMsg = ref('')
const filePanel = ref(false)
let term = null
let fit = null
let ws = null
let ro = null
let dbId = null

const statusText = computed(() => ({
  idle: '未连接', connecting: '连接中', connected: '已连接', closed: '已断开', error: '连接失败',
}[status.value] || ''))

function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = encodeURIComponent(localStorage.getItem('llama_token') || '')
  return `${proto}://${location.host}/api/hosts/${dbId}/terminal/ws?token=${token}`
}

function initTerm() {
  if (term || !termHost.value) return
  term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: 'ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace',
    theme: {
      background: '#161822',
      foreground: '#d8dce8',
      cursor: '#7aa2f7',
      selectionBackground: '#3d4466',
    },
    scrollback: 5000,
    allowProposedApi: true,
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.loadAddon(new WebLinksAddon())
  term.loadAddon(new ClipboardAddon())
  term.open(termHost.value)
  fit.fit()
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
  if (!dbId) return
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
      if (m.state === 'ready') status.value = 'connected'
      else if (m.state === 'closed') status.value = 'closed'
      else if (m.state === 'error') { status.value = 'error'; errMsg.value = m.reason || '' }
    }
  }
  ws.onerror = () => { if (status.value !== 'connected') { status.value = 'error'; errMsg.value = 'WebSocket 连接错误' } }
  ws.onclose = () => { if (status.value === 'connected' || status.value === 'connecting') status.value = 'closed' }
}

function reconnect() {
  connect()
}

function clearTerm() {
  term?.clear()
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
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const okc = document.execCommand('copy')
    ta.remove()
    return okc
  } catch { return false }
}
async function pasteText() {
  term?.focus()
  try {
    if (navigator.clipboard?.readText) {
      const text = await navigator.clipboard.readText()
      if (text) { term?.paste(text); return }
    }
  } catch { /* 继续回退 */ }
  // 回退：聚焦 xterm 隐藏输入框触发浏览器原生粘贴（Firefox 可用；Chrome 拦截则提示）
  try {
    const ta = term?.textarea
    if (ta) {
      ta.focus()
      if (document.execCommand('paste')) return
    }
  } catch { /* ignore */ }
  ElMessage.info('http 环境无法读取剪贴板，请直接在终端按 Ctrl+V 粘贴')
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

// ---------------- 文件管理 ----------------
const fpPath = ref('/')
const fpEntries = ref([])
const fpLoading = ref(false)
const fpUploading = ref(0)
const fileInput = ref(null)
const fpUploadName = ref('')
const fpUploadProgress = ref(0)
const fpUploadPos = ref('')
const FAVS_KEY = 'llama_favs'
const fpFavs = ref([])
const fpIsFav = computed(() => fpFavs.value.includes(fpPath.value || '/'))
const FP_PAGE_SIZE = 100
const fpPage = ref(1)
const fpQuery = ref('')
// 搜索：客户端按文件名过滤（大小写不敏感）；分页基于过滤结果
const fpFilteredEntries = computed(() => {
  const q = fpQuery.value.trim().toLowerCase()
  if (!q) return fpEntries.value
  return fpEntries.value.filter((e) => e.name.toLowerCase().includes(q))
})
const fpPageCount = computed(() => Math.max(1, Math.ceil(fpFilteredEntries.value.length / FP_PAGE_SIZE)))
const fpPageEntries = computed(() =>
  fpFilteredEntries.value.slice((fpPage.value - 1) * FP_PAGE_SIZE, fpPage.value * FP_PAGE_SIZE))
watch(fpQuery, () => { fpPage.value = 1 })

function fpJoin(base, name) {
  return base === '/' ? `/${name}` : `${base}/${name}`
}
async function fpLoad() {
  if (!dbId) return
  fpLoading.value = true
  fpQuery.value = ''
  try {
    const data = await http.get(`/hosts/${dbId}/files`, { params: { path: fpPath.value || '/' } })
    fpEntries.value = data.entries || []
    fpPage.value = 1
  } catch { /* client 已提示 */ }
  finally { fpLoading.value = false }
}
function fpOpen(e) {
  if (e.is_dir) fpPath.value = fpJoin(fpPath.value, e.name)
  else fpDownload(e)
  if (e.is_dir) fpLoad()
}
function fpUp() {
  if (fpPath.value === '/') return
  const i = fpPath.value.lastIndexOf('/')
  fpPath.value = i <= 0 ? '/' : fpPath.value.slice(0, i)
  fpLoad()
}
// 收藏目录：按主机存 localStorage（个人收藏，不入库）
function fpLoadFavs() {
  try {
    const all = JSON.parse(localStorage.getItem(FAVS_KEY) || '{}')
    fpFavs.value = Array.isArray(all[dbId]) ? all[dbId] : []
  } catch { fpFavs.value = [] }
}
function fpSaveFavs() {
  try {
    const all = JSON.parse(localStorage.getItem(FAVS_KEY) || '{}')
    all[dbId] = fpFavs.value
    localStorage.setItem(FAVS_KEY, JSON.stringify(all))
  } catch { /* 忽略存储失败 */ }
}
function fpToggleFav() {
  const p = fpPath.value || '/'
  const i = fpFavs.value.indexOf(p)
  if (i >= 0) { fpFavs.value.splice(i, 1); ElMessage.success(`已取消收藏 ${p}`) }
  else { fpFavs.value.unshift(p); ElMessage.success(`已收藏 ${p}`) }
  fpSaveFavs()
}
function fpGoFav(p) {
  fpPath.value = p
  fpLoad()
}
function fpRemoveFav(p) {
  const i = fpFavs.value.indexOf(p)
  if (i >= 0) fpFavs.value.splice(i, 1)
  fpSaveFavs()
}
function pickFiles() { fileInput.value?.click() }
async function onFilesPicked(ev) {
  const files = [...(ev.target.files || [])]
  ev.target.value = ''
  if (!files.length) return
  let okCount = 0
  for (let i = 0; i < files.length; i++) {
    const f = files[i]
    fpUploading.value++
    fpUploadName.value = f.name
    fpUploadPos.value = files.length > 1 ? `${i + 1}/${files.length}` : ''
    fpUploadProgress.value = 0
    try {
      const fd = new FormData()
      fd.append('path', fpPath.value || '/')
      fd.append('file', f)
      // timeout:0 —— 大文件上传可能超过默认 120s，不能中途掐断
      await http.post(`/hosts/${dbId}/files/upload`, fd, {
        timeout: 0,
        onUploadProgress: (e) => {
          if (e.total) fpUploadProgress.value = Math.min(100, Math.round((e.loaded / e.total) * 100))
        },
      })
      fpUploadProgress.value = 100
      okCount++
    } catch { /* client 已提示 */ }
    finally { fpUploading.value-- }
  }
  if (okCount === files.length) ElMessage.success(`已上传 ${okCount} 个文件到 ${fpPath.value}`)
  else if (okCount > 0) ElMessage.warning(`成功 ${okCount}/${files.length} 个文件，其余失败`)
  if (okCount > 0) fpLoad()
}
async function fpMkdir() {
  let name
  try {
    const { value } = await ElMessageBox.prompt(`在 ${fpPath.value} 下新建目录`, '新建目录', {
      confirmButtonText: '创建', cancelButtonText: '取消',
      inputValidator: (v) => (!v || !v.trim() ? '请输入目录名' : true),
    })
    name = value.trim()
  } catch { return }
  try {
    await http.post(`/hosts/${dbId}/files/mkdir`, { path: fpJoin(fpPath.value, name) })
    fpLoad()
  } catch { /* client 已提示 */ }
}
async function fpDelete(e) {
  const p = fpJoin(fpPath.value, e.name)
  try {
    await ElMessageBox.confirm(`将永久删除 ${p}${e.is_dir ? '（含其中所有内容）' : ''}，此操作不可撤销。`, '删除', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch { return }
  try {
    await http.delete(`/hosts/${dbId}/files`, { params: { path: p } })
    ElMessage.success(`已删除 ${e.name}`)
    fpLoad()
  } catch { /* client 已提示 */ }
}
function fpDownload(e) {
  // 直链 + token：浏览器原生下载管理器流式写盘（大文件不占内存、不阻塞页面）
  const p = fpJoin(fpPath.value, e.name)
  const token = encodeURIComponent(localStorage.getItem('llama_token') || '')
  const a = document.createElement('a')
  a.href = `/api/hosts/${dbId}/files/download?path=${encodeURIComponent(p)}&token=${token}`
  a.download = e.name
  document.body.appendChild(a)
  a.click()
  a.remove()
}
function fmtSize(n) {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}
function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const p = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// ---------------- 生命周期 ----------------
onMounted(async () => {
  initTerm()
  try {
    const hosts = await api.hosts()
    const h = hosts.find((x) => x.id === props.hostId)
    if (h?.db_id) { dbId = h.db_id; fpLoadFavs() }
  } catch { /* 无管理数据 */ }
  if (props.active) {
    await nextTick()
    initTerm()
    connect()
    if (filePanel.value) fpLoad()
  }
})
watch(() => props.active, (a) => {
  if (a) {
    nextTick(() => {
      initTerm()
      if (status.value === 'idle' || status.value === 'error' || status.value === 'closed') connect()
      if (filePanel.value && !fpEntries.value.length) fpLoad()
    })
  }
})
watch(filePanel, (v) => { if (v && dbId) fpLoad() })
watch(() => props.hostId, () => {
  dbId = null
  fpFavs.value = []
  status.value = 'idle'
  if (ws) { try { ws.close() } catch { /* ignore */ } ws = null }
  if (props.active) {
    ;(async () => {
      const hosts = await api.hosts()
      const h = hosts.find((x) => x.id === props.hostId)
      if (h?.db_id) { dbId = h.db_id; fpLoadFavs() }
      if (dbId) connect()
    })()
  }
})
onBeforeUnmount(() => {
  ro?.disconnect()
  if (ws) { try { ws.close() } catch { /* ignore */ } }
  term?.dispose()
})
</script>

<style scoped>
.term-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.term-status { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 3px 10px; border-radius: 20px; border: 1px solid var(--lc-border, #333); white-space: nowrap; }
.st-dot { width: 8px; height: 8px; border-radius: 50%; background: #888; }
.st-connected { color: #22c55e; } .st-connected .st-dot { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
.st-connecting { color: #eab308; } .st-connecting .st-dot { background: #eab308; animation: term-pulse 1s infinite; }
.st-closed, .st-error { color: #ef4444; } .st-closed .st-dot, .st-error .st-dot { background: #ef4444; }
@keyframes term-pulse { 50% { opacity: 0.3; } }
.term-host { color: var(--text-dim, #888); font-size: 12px; }
.term-body { display: flex; gap: 12px; align-items: stretch; }
.term-host-wrap { position: relative; flex: 1; min-width: 0; height: calc(100vh - 190px); min-height: 420px; border-radius: 10px; overflow: hidden; border: 1px solid var(--lc-border, #333); padding: 8px 4px 4px 10px; }
.term-host { width: 100%; height: 100%; background: #161822; }
.term-overlay { position: absolute; inset: 0; display: flex; flex-direction: column; gap: 12px; align-items: center; justify-content: center; background: rgba(22, 24, 34, 0.82); color: #aab; font-size: 13px; }
.file-panel { width: 400px; flex-shrink: 0; display: flex; flex-direction: column; height: calc(100vh - 190px); min-height: 420px; border: 1px solid var(--lc-border, #333); border-radius: 10px; overflow: hidden; background: var(--lc-bg, #fff); }
.fp-bar { display: flex; gap: 6px; padding: 8px; border-bottom: 1px solid var(--lc-border, #eee); }
.fp-bar .el-input { flex: 1; }
.fp-search { padding: 8px 8px 0; }
.fp-actions { display: flex; gap: 6px; padding: 8px; border-bottom: 1px solid var(--lc-border, #eee); }
.fp-list { flex: 1; overflow-y: auto; min-height: 0; }
.fp-row { display: flex; align-items: center; gap: 8px; padding: 6px 10px; cursor: pointer; border-bottom: 1px solid color-mix(in srgb, var(--lc-border, #ddd) 40%, transparent); font-size: 13px; }
.fp-row:hover { background: color-mix(in srgb, var(--lc-primary, #409eff) 8%, transparent); }
.fp-row .fp-ops { margin-left: auto; display: none; }
.fp-row:hover .fp-ops { display: inline-flex; }
.fp-ico { color: var(--lc-text-muted, #999); }
.fp-row.dir .fp-ico { color: #eab308; }
.fp-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fp-meta { color: var(--lc-text-muted, #999); font-size: 12px; flex-shrink: 0; }
.fp-empty { padding: 30px 0; text-align: center; color: var(--lc-text-muted, #999); font-size: 13px; }
.fp-pager { display: flex; align-items: center; gap: 8px; padding: 8px; border-top: 1px solid var(--lc-border, #eee); }
.fp-pager-info { flex: 1; font-size: 12px; color: var(--lc-text-muted, #999); }
.fav-item { display: flex; align-items: center; gap: 8px; }
.fav-path { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fav-del { color: #999; flex-shrink: 0; }
.fav-del:hover { color: #ef4444; }
.fp-up-progress { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-bottom: 1px solid var(--lc-border, #eee); background: color-mix(in srgb, var(--lc-primary, #409eff) 6%, transparent); }
.fp-up-label { font-size: 12px; color: var(--lc-text-muted, #666); max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fp-up-bar { flex: 1; }
</style>
