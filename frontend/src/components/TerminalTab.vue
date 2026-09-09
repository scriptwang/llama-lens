<template>
  <div class="tab-pane term-scope">
    <div class="term-toolbar">
      <span class="term-status" :class="'st-' + activeStatus">
        <span class="st-dot" />{{ activeStatusText }}
      </span>
      <span v-if="hostLabel" class="term-host-label">{{ hostLabel }}</span>
      <div class="term-tabs">
        <div v-for="s in sessions" :key="s.id" class="term-tab" :class="{ on: s.id === activeId }"
             :title="s.title + '（双击重命名）'" @click="focusSession(s.id)" @dblclick="renameSession(s)">
          <span class="tt-dot" :class="'st-' + (sessStatus[s.id] || 'idle')" />
          <span class="tt-title">{{ s.title }}</span>
          <span v-if="sessPersist[s.id]" class="tt-persist" title="会话保持：刷新页面后场景恢复">保</span>
          <el-icon class="tt-close" @click.stop="closeSession(s.id)"><Close /></el-icon>
        </div>
        <el-button class="tt-add" size="small" :disabled="sessions.length >= maxSessions"
                   :title="sessions.length >= maxSessions ? `最多 ${maxSessions} 个终端（config.yaml ui.terminal_max_sessions 可调）` : '新建终端'" @click="addSession">
          <el-icon><Plus /></el-icon>
        </el-button>
      </div>
      <span class="toolbar-spacer" />
      <el-button size="small" :disabled="!activeSession" @click="activeSession?.clear()">清屏</el-button>
      <el-button size="small" type="warning" plain :disabled="activeStatus !== 'connected'" @click="activeSession?.reconnect()">重连</el-button>
      <el-button size="small" :disabled="!activeSession" @click="activeSession?.paste()">粘贴</el-button>
      <el-button size="small" :type="filePanel ? 'primary' : 'default'" :plain="!filePanel" @click="filePanel = !filePanel">
        <el-icon style="margin-right:3px"><FolderOpened /></el-icon>文件
      </el-button>
    </div>
    <div class="term-body">
      <div class="term-sessions">
        <SplitPane v-if="layout" :node="layout" :sessions="sessions" :db-id="dbId" :active="active"
                   :host-label="hostLabel" :active-id="activeId" :max-sessions="maxSessions"
                   @split="splitPane" @close="(n) => closeSession(n.sessionId)" @pick="pickSession"
                   @status="onSessionStatus" @persist="onSessionPersist" @focus="focusSession" />
        <div v-else class="term-empty">
          <span>暂无终端会话</span>
          <el-button size="small" type="primary" @click="addSession">新建终端</el-button>
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
import { computed, onMounted, provide, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { Search, Star } from '@element-plus/icons-vue'
import http from '../api/client'
import { api } from '../api'
import SplitPane from './SplitPane.vue'

const props = defineProps({
  hostId: String,
  active: Boolean,
  hostLabel: { type: String, default: '' },
  maxSessions: { type: Number, default: 20 },
})

// ---------------- 多终端会话 + 树状分屏布局 ----------------
// 每个会话 = 独立 xterm + 独立 WS + 后端独立 tmux 会话（刷新后重连恢复场景）
// 布局是一棵二叉树：叶子=面板(绑定一个会话)，内部节点=分屏(方向 h/v + 可拖动比例 ratio)
let nextSessionId = 1  // 模块级自增：id 全局唯一，避免切换主机后 Vue 复用旧实例
const SESSIONS_KEY = 'llama_term_sessions'
const sessions = ref([])
const activeId = ref(null)
const sessStatus = ref({})
const sessPersist = ref({})
const sessionRefs = reactive({})  // 会话实例注册表（provide 给 SplitPane 注册 TermSession）
const layout = ref(null)          // 分屏布局树
const dbId = ref(null)
const filePanel = ref(false)

provide('termRegistry', sessionRefs)

const activeSession = computed(() => sessionRefs[activeId.value] || null)
const activeStatus = computed(() => sessStatus.value[activeId.value] || 'idle')
const activeStatusText = computed(() => ({
  idle: '未连接', connecting: '连接中', connected: '已连接', closed: '已断开', error: '连接失败',
}[activeStatus.value] || ''))

function genTmuxName() {
  return 'llama-' + Math.random().toString(36).slice(2, 8)
}
function onSessionStatus({ id, status }) { sessStatus.value[id] = status }
function onSessionPersist({ id, persist }) { sessPersist.value[id] = persist }
function focusSession(id) {
  if (id != null) activeId.value = id
}

// ---------------- 布局树操作 ----------------
function findLeaf(node, sessionId) {
  if (!node) return null
  if (node.type === 'pane') return node.sessionId === sessionId ? node : null
  return findLeaf(node.a, sessionId) || findLeaf(node.b, sessionId)
}
function firstLeaf(node) {
  if (!node) return null
  if (node.type === 'pane') return node
  return firstLeaf(node.a) || firstLeaf(node.b)
}
function leavesOf(node) {
  if (!node) return []
  if (node.type === 'pane') return [node.sessionId]
  return [...leavesOf(node.a), ...leavesOf(node.b)]
}
function replaceLeaf(node, target, replacement) {
  if (!node) return node
  if (node === target) return replacement
  if (node.type === 'split') {
    return { ...node, a: replaceLeaf(node.a, target, replacement), b: replaceLeaf(node.b, target, replacement) }
  }
  return node
}
function removeLeaf(node, sessionId) {
  if (!node) return null
  if (node.type === 'pane') return node.sessionId === sessionId ? null : node
  const a = removeLeaf(node.a, sessionId)
  const b = removeLeaf(node.b, sessionId)
  if (a === null && b === null) return null
  if (a === null) return b
  if (b === null) return a
  return { ...node, a, b }
}
// 加载时按有效会话 id 重映射布局（被截断/缺失会话对应面板丢弃，父分屏自动折叠）
function remapLayout(node, validIds) {
  if (!node) return null
  if (node.type === 'pane') return validIds.has(node.sessionId) ? { ...node } : null
  const a = remapLayout(node.a, validIds)
  const b = remapLayout(node.b, validIds)
  if (a === null && b === null) return null
  if (a === null) return b
  if (b === null) return a
  return { ...node, a, b }
}
// 无保存布局时的默认布局：等分竖向堆叠
function defaultLayout(ids) {
  if (!ids.length) return null
  let node = { type: 'pane', sessionId: ids[0] }
  for (let i = 1; i < ids.length; i++) {
    node = { type: 'split', dir: 'v', ratio: i / (i + 1), a: node, b: { type: 'pane', sessionId: ids[i] } }
  }
  return node
}

// ---------------- 会话增删 + 分屏 ----------------
function addSession() {
  if (sessions.value.length >= props.maxSessions) return
  const s = { id: nextSessionId++, title: `终端 ${sessions.value.length + 1}`, tmuxName: genTmuxName() }
  sessions.value.push(s)
  if (!layout.value) {
    layout.value = { type: 'pane', sessionId: s.id }
  } else {
    // 在“当前活动面板”处左右分屏，新终端放右侧
    const target = findLeaf(layout.value, activeId.value) || firstLeaf(layout.value)
    layout.value = replaceLeaf(layout.value, target, { type: 'split', dir: 'h', ratio: 0.5, a: target, b: { type: 'pane', sessionId: s.id } })
  }
  activeId.value = s.id
  saveSessions()
}
function splitPane(paneNode, dir) {
  if (sessions.value.length >= props.maxSessions) {
    ElMessage.warning(`最多 ${props.maxSessions} 个终端（config.yaml ui.terminal_max_sessions 可调）`)
    return
  }
  const s = { id: nextSessionId++, title: `终端 ${sessions.value.length + 1}`, tmuxName: genTmuxName() }
  sessions.value.push(s)
  layout.value = replaceLeaf(layout.value, paneNode, { type: 'split', dir: dir || 'h', ratio: 0.5, a: paneNode, b: { type: 'pane', sessionId: s.id } })
  activeId.value = s.id
  saveSessions()
}
// 面板切换显示哪个终端：与目标终端所在面板交换（保持“每个终端恰在一个面板”）
function pickSession(paneNode, newId) {
  if (newId == null || newId === paneNode.sessionId) return
  const other = findLeaf(layout.value, newId)
  if (!other) return
  const oldId = paneNode.sessionId
  paneNode.sessionId = newId
  other.sessionId = oldId
  saveSessions()
}
async function closeSession(sessionId) {
  const sess = sessions.value.find((s) => s.id === sessionId)
  if (!sess) return
  const doClose = () => {
    // 先通知后端杀 tmux 会话（kill 消息先于 WS 关闭帧，后端保证处理完才断开）
    sessionRefs[sessionId]?.kill()
    layout.value = removeLeaf(layout.value, sessionId)
    const i = sessions.value.indexOf(sess)
    if (i >= 0) sessions.value.splice(i, 1)
    delete sessStatus.value[sessionId]
    delete sessPersist.value[sessionId]
    if (activeId.value === sessionId) {
      const remaining = leavesOf(layout.value)
      activeId.value = remaining.length ? remaining[0] : null
    }
    saveSessions()
  }
  if ((sessStatus.value[sessionId] || 'idle') === 'connected') {
    ElMessageBox.confirm(`关闭「${sess.title}」将终止其 shell 会话（含正在运行的命令），确定关闭？`, '关闭终端', {
      type: 'warning', confirmButtonText: '关闭', cancelButtonText: '取消',
    }).then(doClose).catch(() => {})
  } else {
    doClose()
  }
}
async function renameSession(s) {
  try {
    const { value } = await ElMessageBox.prompt('终端名称', '重命名终端', {
      inputValue: s.title, confirmButtonText: '保存', cancelButtonText: '取消',
      inputValidator: (v) => (!v || !v.trim() ? '请输入名称' : true),
    })
    s.title = value.trim()
    saveSessions()
  } catch { /* 取消 */ }
}

// ---------------- 会话持久化（localStorage：会话 + 布局树，刷新后恢复） ----------------
function saveSessions() {
  try {
    const all = JSON.parse(localStorage.getItem(SESSIONS_KEY) || '{}')
    all[props.hostId] = {
      sessions: sessions.value.map((s) => ({ id: s.id, title: s.title, tmuxName: s.tmuxName })),
      activeId: activeId.value,
      layout: layout.value,
    }
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(all))
  } catch { /* 忽略 */ }
}
function loadSessions() {
  try {
    const all = JSON.parse(localStorage.getItem(SESSIONS_KEY) || '{}')
    const saved = all[props.hostId]
    if (saved && Array.isArray(saved.sessions) && saved.sessions.length) {
      const capped = saved.sessions.slice(0, props.maxSessions)
      sessions.value = capped.map((s) => ({
        id: (typeof s.id === 'number' ? s.id : nextSessionId++),
        title: s.title || '终端',
        tmuxName: s.tmuxName || genTmuxName(),
      }))
      nextSessionId = Math.max(0, ...sessions.value.map((s) => s.id)) + 1
      const validIds = new Set(sessions.value.map((s) => s.id))
      layout.value = saved.layout ? remapLayout(saved.layout, validIds) : null
      if (!layout.value) layout.value = defaultLayout(sessions.value.map((s) => s.id))
      activeId.value = sessions.value.some((s) => s.id === saved.activeId) ? saved.activeId : sessions.value[0].id
    }
  } catch { /* 忽略 */ }
}

async function resolveDbId() {
  try {
    const hosts = await api.hosts()
    const h = hosts.find((x) => x.id === props.hostId)
    dbId.value = h?.db_id || null
    if (dbId.value) fpLoadFavs()
  } catch { dbId.value = null }
}

// ---------------- 文件管理（所有会话共享，属主机级） ----------------
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
  if (!dbId.value) return
  fpLoading.value = true
  fpQuery.value = ''
  try {
    const data = await http.get(`/hosts/${dbId.value}/files`, { params: { path: fpPath.value || '/' } })
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
    fpFavs.value = Array.isArray(all[dbId.value]) ? all[dbId.value] : []
  } catch { fpFavs.value = [] }
}
function fpSaveFavs() {
  try {
    const all = JSON.parse(localStorage.getItem(FAVS_KEY) || '{}')
    all[dbId.value] = fpFavs.value
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
      await http.post(`/hosts/${dbId.value}/files/upload`, fd, {
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
    await http.post(`/hosts/${dbId.value}/files/mkdir`, { path: fpJoin(fpPath.value, name) })
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
    await http.delete(`/hosts/${dbId.value}/files`, { params: { path: p } })
    ElMessage.success(`已删除 ${e.name}`)
    fpLoad()
  } catch { /* client 已提示 */ }
}
function fpDownload(e) {
  // 直链 + token：浏览器原生下载管理器流式写盘（大文件不占内存、不阻塞页面）
  const p = fpJoin(fpPath.value, e.name)
  const token = encodeURIComponent(localStorage.getItem('llama_token') || '')
  const a = document.createElement('a')
  a.href = `/api/hosts/${dbId.value}/files/download?path=${encodeURIComponent(p)}&token=${token}`
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
onMounted(() => {
  resolveDbId()
  if (props.active) {
    loadSessions()
    if (!sessions.value.length) addSession()
    if (filePanel.value) fpLoad()
  }
})
watch(() => props.active, (a) => {
  if (a) {
    if (!sessions.value.length) {
      loadSessions()
      if (!sessions.value.length) addSession()
    }
    if (filePanel.value && !fpEntries.value.length) fpLoad()
  }
})
watch(filePanel, (v) => { if (v && dbId.value) fpLoad() })
watch(() => props.hostId, () => {
  // 切换主机：重建会话 + 重置布局 + 重置文件面板
  sessions.value = []
  activeId.value = null
  sessStatus.value = {}
  sessPersist.value = {}
  for (const k in sessionRefs) delete sessionRefs[k]
  layout.value = null
  dbId.value = null
  fpPath.value = '/'
  fpEntries.value = []
  fpFavs.value = []
  resolveDbId().then(() => {
    if (props.active) {
      loadSessions()
      if (!sessions.value.length) addSession()
      if (filePanel.value) fpLoad()
    }
  })
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
.term-host-label { color: var(--text-dim, #888); font-size: 12px; white-space: nowrap; }
.term-tabs { display: flex; align-items: center; gap: 6px; overflow-x: auto; max-width: 42%; padding: 2px; scrollbar-width: thin; }
.term-tab { display: inline-flex; align-items: center; gap: 6px; padding: 4px 6px 4px 10px; border-radius: 6px; border: 1px solid var(--lc-border, #333); font-size: 12px; cursor: pointer; color: var(--lc-text-muted, #999); user-select: none; flex-shrink: 0; }
.term-tab:hover { color: var(--text, #ddd); }
.term-tab.on { background: color-mix(in srgb, var(--lc-primary, #409eff) 12%, transparent); border-color: var(--lc-primary, #409eff); color: var(--text, #eee); }
.tt-dot { width: 7px; height: 7px; border-radius: 50%; background: #888; flex-shrink: 0; }
.tt-dot.st-connected { background: #22c55e; }
.tt-dot.st-connecting { background: #eab308; }
.tt-dot.st-closed, .tt-dot.st-error { background: #ef4444; }
.tt-title { max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tt-persist { font-size: 10px; line-height: 14px; color: #22c55e; border: 1px solid color-mix(in srgb, #22c55e 40%, transparent); border-radius: 3px; padding: 0 3px; flex-shrink: 0; }
.tt-close { font-size: 12px; color: #888; }
.tt-close:hover { color: #ef4444; }
.toolbar-spacer { flex: 1; }
.term-body { display: flex; gap: 12px; align-items: stretch; height: calc(100vh - 190px); min-height: 420px; }
.term-sessions { display: flex; flex: 1; min-width: 0; min-height: 0; }
.term-empty { position: relative; flex: 1; display: flex; flex-direction: column; gap: 12px; align-items: center; justify-content: center; border-radius: 10px; border: 1px solid var(--lc-border, #333); background: #161822; color: #aab; font-size: 13px; }
.file-panel { width: 400px; flex-shrink: 0; display: flex; flex-direction: column; height: 100%; border: 1px solid var(--lc-border, #333); border-radius: 10px; overflow: hidden; background: var(--lc-bg, #fff); }
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
