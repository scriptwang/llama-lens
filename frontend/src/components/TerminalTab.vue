<template>
  <div class="tab-pane term-scope">
    <div class="term-toolbar">
      <span class="term-status" :class="'st-' + activeStatus">
        <span class="st-dot" />{{ activeStatusText }}
      </span>
      <span v-if="hostLabel" class="term-host-label">{{ hostLabel }}</span>
      <div class="term-tabs">
        <div v-for="t in tabs" :key="t.id" class="term-tab" :class="{ on: t.id === activeTabId }"
             :title="t.title + '（双击重命名）'" @click="switchTab(t.id)" @dblclick="renameTab(t)">
          <span class="tt-title">{{ t.title }}</span>
          <span v-if="t.sessions.length > 1" class="tt-count" :title="'已分屏 ' + t.sessions.length + ' 个终端'">{{ t.sessions.length }}</span>
          <el-icon class="tt-close" @click.stop="closeTab(t.id)"><Close /></el-icon>
        </div>
        <el-button class="tt-add" size="small" :disabled="totalSessions >= maxSessions"
                   :title="totalSessions >= maxSessions ? `最多 ${maxSessions} 个终端（config.yaml ui.terminal_max_sessions 可调）` : '新建标签（全屏终端，可再内部分屏）'" @click="addTab">
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
      <el-button size="small" type="danger" plain :disabled="!totalSessions"
                 :title="`一键关闭全部 ${totalSessions} 个终端（含正在运行的命令）`" @click="closeAll">关闭全部</el-button>
      <el-button size="small" plain :disabled="!dbId"
                 title="清理远端主机上不在当前标签里的孤儿终端会话（tmux），释放残留资源" @click="cleanupOrphans">清理孤儿</el-button>
    </div>
    <div class="term-body">
      <div class="term-sessions">
        <SplitPane v-if="activeTab && activeTab.layout" :node="activeTab.layout" :sessions="activeTab.sessions"
                   :db-id="dbId" :active="active" :host-label="hostLabel" :active-id="activeTab.activeId"
                   :max-sessions="maxSessions"
                   @split="splitPane" @close="closePane" @pick="pickSession"
                   @status="onSessionStatus" @persist="onSessionPersist" @focus="focusSession" />
        <div v-else class="term-empty">
          <span>暂无终端</span>
          <el-button size="small" type="primary" @click="addTab">新建标签</el-button>
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

// ---------------- 多标签 + 每标签独立树状分屏 ----------------
// 每个标签(TAB) = 一个独立工作区：可全屏(1 终端)或内部分屏(多终端)
// 标签内布局是二叉树：叶子=面板(绑定一个终端)，内部节点=分屏(方向 h/v + 可拖动比例)
// 每个终端 = 独立 xterm + 独立 WS + 后端独立 tmux 会话（刷新/切标签后重连恢复场景）
let nextTabId = 1
let nextSessionId = 1
const SESSIONS_KEY = 'llama_term_sessions'
const tabs = ref([])            // [{id, title, sessions:[{id,title,tmuxName}], layout, activeId}]
const activeTabId = ref(null)
const sessionRefs = reactive({})  // 会话实例注册表（provide 给 SplitPane 注册 TermSession）
const sessStatus = ref({})
const sessPersist = ref({})
const dbId = ref(null)
const filePanel = ref(false)

provide('termRegistry', sessionRefs)

const activeTab = computed(() => tabs.value.find((t) => t.id === activeTabId.value) || null)
const totalSessions = computed(() => tabs.value.reduce((n, t) => n + t.sessions.length, 0))
const activeSession = computed(() => (activeTab.value ? sessionRefs[activeTab.value.activeId] || null : null))
const activeStatus = computed(() => (activeTab.value ? sessStatus.value[activeTab.value.activeId] || 'idle' : 'idle'))
const activeStatusText = computed(() => ({
  idle: '未连接', connecting: '连接中', connected: '已连接', closed: '已断开', error: '连接失败',
}[activeStatus.value] || ''))

function genTmuxName() { return 'llama-' + Math.random().toString(36).slice(2, 8) }
function usedSessionNums() {
  const nums = new Set()
  tabs.value.forEach((t) => t.sessions.forEach((s) => {
    const m = s.title.match(/^终端 (\d+)$/)
    if (m) nums.add(parseInt(m[1], 10))
  }))
  return nums
}
function newSession() {
  const used = usedSessionNums()
  let num = 1
  while (used.has(num)) num++
  return { id: nextSessionId++, title: `终端 ${num}`, tmuxName: genTmuxName() }
}
function onSessionStatus({ id, status }) { sessStatus.value[id] = status }
function onSessionPersist({ id, persist }) { sessPersist.value[id] = persist }
function focusSession(id) { if (activeTab.value && id != null) activeTab.value.activeId = id }

// ---------------- 布局树操作（作用于某标签的 layout） ----------------
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

// ---------------- 标签操作 ----------------
function usedTabNums() {
  const nums = new Set()
  tabs.value.forEach((t) => {
    const m = t.title.match(/^标签 (\d+)$/)
    if (m) nums.add(parseInt(m[1], 10))
  })
  return nums
}
function addTab() {
  if (totalSessions.value >= props.maxSessions) return
  const s = newSession()
  const used = usedTabNums()
  let num = 1
  while (used.has(num)) num++
  const tab = { id: nextTabId++, title: `标签 ${num}`, sessions: [s], layout: { type: 'pane', sessionId: s.id }, activeId: s.id }
  tabs.value.push(tab)
  activeTabId.value = tab.id
  save()
}
function switchTab(id) {
  if (id !== activeTabId.value) { activeTabId.value = id; save() }
}
async function renameTab(t) {
  try {
    const { value } = await ElMessageBox.prompt('标签名称', '重命名标签', {
      inputValue: t.title, confirmButtonText: '保存', cancelButtonText: '取消',
      inputValidator: (v) => (!v || !v.trim() ? '请输入名称' : true),
    })
    t.title = value.trim()
    save()
  } catch { /* 取消 */ }
}
function closeTab(tabId) {
  const tab = tabs.value.find((t) => t.id === tabId)
  if (!tab) return
  const doClose = () => {
    tab.sessions.forEach((s) => sessionRefs[s.id]?.kill())
    const i = tabs.value.indexOf(tab)
    if (i >= 0) tabs.value.splice(i, 1)
    tab.sessions.forEach((s) => { delete sessStatus.value[s.id]; delete sessPersist.value[s.id] })
    if (activeTabId.value === tabId) activeTabId.value = tabs.value.length ? tabs.value[tabs.value.length - 1].id : null
    save()
  }
  const anyConnected = tab.sessions.some((s) => (sessStatus.value[s.id] || 'idle') === 'connected')
  if (anyConnected) {
    ElMessageBox.confirm(`关闭「${tab.title}」将终止其中 ${tab.sessions.length} 个终端会话（含正在运行的命令），确定关闭？`, '关闭标签', {
      type: 'warning', confirmButtonText: '关闭', cancelButtonText: '取消',
    }).then(doClose).catch(() => {})
  } else doClose()
}

// 一键关闭所有终端：杀掉全部会话，重置为单个空白标签
function closeAll() {
  if (!totalSessions.value) return
  ElMessageBox.confirm(`将关闭全部 ${totalSessions.value} 个终端会话（含正在运行的命令），并新建一个空白标签，确定？`, '关闭全部终端', {
    type: 'warning', confirmButtonText: '关闭全部', cancelButtonText: '取消',
  }).then(() => {
    tabs.value.forEach((t) => t.sessions.forEach((s) => {
      sessionRefs[s.id]?.kill()
      delete sessStatus.value[s.id]
      delete sessPersist.value[s.id]
    }))
    tabs.value = []
    activeTabId.value = null
    addTab()
    save()
  }).catch(() => {})
}

// 清理远端孤儿 tmux 会话：keep 收集【所有】标签正在用的会话名（非仅活动标签），避免误杀切走的标签
async function cleanupOrphans() {
  if (!dbId.value) return
  const keep = tabs.value.flatMap((t) => t.sessions.map((s) => s.tmuxName))
  try {
    await ElMessageBox.confirm('将清理远端主机上不在当前标签里的孤儿终端会话（tmux），确定？', '清理孤儿会话', {
      type: 'warning', confirmButtonText: '清理', cancelButtonText: '取消',
    })
  } catch { return }
  try {
    const data = await http.post(`/hosts/${dbId.value}/terminal/cleanup`, { keep })
    ElMessage.success(`已清理 ${data?.killed?.length || 0} 个孤儿会话`)
  } catch { /* client 已提示 */ }
}

// ---------------- 面板操作（作用于活动标签） ----------------
function splitPane(paneNode, dir) {
  const tab = activeTab.value
  if (!tab) return
  if (totalSessions.value >= props.maxSessions) {
    ElMessage.warning(`最多 ${props.maxSessions} 个终端（config.yaml ui.terminal_max_sessions 可调）`)
    return
  }
  const s = newSession()
  tab.sessions.push(s)
  tab.layout = replaceLeaf(tab.layout, paneNode, { type: 'split', dir: dir || 'h', ratio: 0.5, a: paneNode, b: { type: 'pane', sessionId: s.id } })
  tab.activeId = s.id
  save()
}
function closePane(paneNode) {
  const tab = activeTab.value
  if (!tab) return
  const sess = tab.sessions.find((s) => s.id === paneNode.sessionId)
  if (!sess) return
  const doClose = () => {
    sessionRefs[paneNode.sessionId]?.kill()
    tab.layout = removeLeaf(tab.layout, paneNode.sessionId)
    tab.sessions = tab.sessions.filter((s) => s.id !== paneNode.sessionId)
    delete sessStatus.value[paneNode.sessionId]
    delete sessPersist.value[paneNode.sessionId]
    if (tab.activeId === paneNode.sessionId) {
      const remaining = leavesOf(tab.layout)
      tab.activeId = remaining.length ? remaining[0] : null
    }
    if (!tab.sessions.length) { closeTab(tab.id); return }
    save()
  }
  if ((sessStatus.value[paneNode.sessionId] || 'idle') === 'connected') {
    ElMessageBox.confirm(`关闭「${sess.title}」将终止其 shell 会话（含正在运行的命令），确定关闭？`, '关闭终端', {
      type: 'warning', confirmButtonText: '关闭', cancelButtonText: '取消',
    }).then(doClose).catch(() => {})
  } else doClose()
}
// 面板切换显示哪个终端：与目标终端所在面板交换（限本标签内，保持“每个终端恰在一个面板”）
function pickSession(paneNode, newId) {
  const tab = activeTab.value
  if (!tab || newId == null || newId === paneNode.sessionId) return
  const other = findLeaf(tab.layout, newId)
  if (!other) return
  const oldId = paneNode.sessionId
  paneNode.sessionId = newId
  other.sessionId = oldId
  save()
}

// ---------------- 持久化（localStorage：标签 + 各标签布局，刷新后恢复） ----------------
function save() {
  try {
    const all = JSON.parse(localStorage.getItem(SESSIONS_KEY) || '{}')
    all[props.hostId] = {
      tabs: tabs.value.map((t) => ({
        id: t.id, title: t.title,
        sessions: t.sessions.map((s) => ({ id: s.id, title: s.title, tmuxName: s.tmuxName })),
        layout: t.layout, activeId: t.activeId,
      })),
      activeTabId: activeTabId.value,
    }
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(all))
  } catch { /* 忽略 */ }
}
function load() {
  try {
    const all = JSON.parse(localStorage.getItem(SESSIONS_KEY) || '{}')
    const saved = all[props.hostId]
    if (!saved) return
    if (Array.isArray(saved.tabs) && saved.tabs.length) {
      tabs.value = saved.tabs.map((t) => ({
        id: t.id, title: t.title || '标签',
        sessions: (t.sessions || []).map((s) => ({ id: s.id, title: s.title || '终端', tmuxName: s.tmuxName || genTmuxName() })),
        layout: t.layout, activeId: t.activeId,
      }))
      tabs.value.forEach((t) => {
        const valid = new Set(t.sessions.map((s) => s.id))
        t.layout = remapLayout(t.layout, valid) || defaultLayout(t.sessions.map((s) => s.id))
      })
      tabs.value = tabs.value.filter((t) => t.sessions.length)
      activeTabId.value = tabs.value.some((t) => t.id === saved.activeTabId) ? saved.activeTabId : (tabs.value[0]?.id ?? null)
    } else if (Array.isArray(saved.sessions) && saved.sessions.length) {
      // 旧格式（单一布局）迁移为单个标签
      const capped = saved.sessions.slice(0, props.maxSessions)
      const sessions = capped.map((s) => ({ id: (typeof s.id === 'number' ? s.id : nextSessionId++), title: s.title || '终端', tmuxName: s.tmuxName || genTmuxName() }))
      const valid = new Set(sessions.map((s) => s.id))
      const layout = (saved.layout ? remapLayout(saved.layout, valid) : null) || defaultLayout(sessions.map((s) => s.id))
      tabs.value = [{ id: nextTabId++, title: '标签 1', sessions, layout, activeId: sessions.some((s) => s.id === saved.activeId) ? saved.activeId : sessions[0].id }]
      activeTabId.value = tabs.value[0].id
    }
    if (tabs.value.length) {
      nextSessionId = Math.max(0, ...tabs.value.flatMap((t) => t.sessions.map((s) => s.id))) + 1
      nextTabId = Math.max(0, ...tabs.value.map((t) => t.id)) + 1
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

// ---------------- 文件管理（所有标签共享，属主机级） ----------------
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
    load()
    if (!tabs.value.length) addTab()
    if (filePanel.value) fpLoad()
  }
})
watch(() => props.active, (a) => {
  if (a) {
    if (!tabs.value.length) {
      load()
      if (!tabs.value.length) addTab()
    }
    if (filePanel.value && !fpEntries.value.length) fpLoad()
  }
})
watch(filePanel, (v) => { if (v && dbId.value) fpLoad() })
watch(() => props.hostId, () => {
  // 切换主机：重建标签 + 重置文件面板
  tabs.value = []
  activeTabId.value = null
  sessStatus.value = {}
  sessPersist.value = {}
  for (const k in sessionRefs) delete sessionRefs[k]
  dbId.value = null
  fpPath.value = '/'
  fpEntries.value = []
  fpFavs.value = []
  resolveDbId().then(() => {
    if (props.active) {
      load()
      if (!tabs.value.length) addTab()
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
.term-tabs { display: flex; align-items: center; gap: 6px; flex: 0 1 auto; min-width: 0; overflow-x: auto; padding: 2px; scrollbar-width: thin; }
.term-tab { display: inline-flex; align-items: center; gap: 6px; padding: 4px 6px 4px 10px; border-radius: 6px; border: 1px solid var(--lc-border, #333); font-size: 12px; cursor: pointer; color: var(--lc-text-muted, #999); user-select: none; flex-shrink: 0; }
.term-tab:hover { color: var(--text, #ddd); }
.term-tab.on { background: color-mix(in srgb, var(--lc-primary, #409eff) 12%, transparent); border-color: var(--lc-primary, #409eff); color: var(--text, #eee); }
.tt-title { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tt-count { font-size: 10px; line-height: 14px; color: var(--lc-primary, #409eff); border: 1px solid color-mix(in srgb, var(--lc-primary, #409eff) 40%, transparent); border-radius: 8px; padding: 0 4px; flex-shrink: 0; }
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
