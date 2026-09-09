<script>
export default { name: 'SplitPane' }
</script>
<script setup>
import { inject } from 'vue'
import { ArrowDown, Close } from '@element-plus/icons-vue'
import TermSession from './TermSession.vue'

// 递归分屏组件：
//   节点 { type:'split', dir:'h'|'v', ratio, a, b } → flex 容器 + 可拖动分隔条 + 两个子节点
//   节点 { type:'pane', sessionId }                 → 面板（头部工具条 + TermSession）
const props = defineProps({
  node: Object,
  sessions: Array,
  dbId: { type: Number, default: null },
  active: Boolean,
  hostLabel: { type: String, default: '' },
  activeId: Number,
  maxSessions: Number,
})
const emit = defineEmits(['split', 'close', 'pick', 'status', 'persist', 'focus'])

// 会话实例注册表（TerminalTab provide）：供关闭杀会话 / 清屏 / 重连 / 粘贴调用
const registry = inject('termRegistry', null)
function regRef(id, el) {
  if (!registry) return
  if (el) registry[id] = el
  else delete registry[id]
}
function tmuxNameOf(id) {
  const s = props.sessions.find((x) => x.id === id)
  return s ? s.tmuxName : ''
}

// ---------------- 拖动分隔条（pointer capture，本地状态，不污染事件循环） ----------------
let drag = null
function startDrag(e) {
  const container = e.currentTarget.parentElement
  drag = { node: props.node, dir: props.node.dir, rect: container.getBoundingClientRect() }
  try { e.currentTarget.setPointerCapture(e.pointerId) } catch { /* ignore */ }
  e.preventDefault()
}
function onDrag(e) {
  if (!drag) return
  const r = drag.rect
  let ratio = drag.dir === 'h' ? (e.clientX - r.left) / r.width : (e.clientY - r.top) / r.height
  ratio = Math.min(0.85, Math.max(0.15, ratio))
  drag.node.ratio = ratio
}
function endDrag(e) {
  if (drag && e.currentTarget) { try { e.currentTarget.releasePointerCapture(e.pointerId) } catch { /* ignore */ } }
  drag = null
}
</script>

<template>
  <!-- 分屏节点 -->
  <div v-if="node.type === 'split'" class="sp-split" :class="node.dir === 'h' ? 'sp-h' : 'sp-v'">
    <div class="sp-child" :style="{ flexBasis: (node.ratio * 100) + '%' }">
      <SplitPane :node="node.a" :sessions="sessions" :db-id="dbId" :active="active"
                 :host-label="hostLabel" :active-id="activeId" :max-sessions="maxSessions"
                 @split="(n, d) => emit('split', n, d)" @close="(n) => emit('close', n)"
                 @pick="(n, v) => emit('pick', n, v)" @status="(e) => emit('status', e)"
                 @persist="(e) => emit('persist', e)" @focus="(id) => emit('focus', id)" />
    </div>
    <div class="sp-divider" :class="node.dir === 'h' ? 'div-h' : 'div-v'"
         :title="node.dir === 'h' ? '拖动调整左右宽度' : '拖动调整上下高度'"
         @pointerdown="startDrag" @pointermove="onDrag" @pointerup="endDrag" @pointercancel="endDrag" />
    <div class="sp-child" :style="{ flexBasis: ((1 - node.ratio) * 100) + '%' }">
      <SplitPane :node="node.b" :sessions="sessions" :db-id="dbId" :active="active"
                 :host-label="hostLabel" :active-id="activeId" :max-sessions="maxSessions"
                 @split="(n, d) => emit('split', n, d)" @close="(n) => emit('close', n)"
                 @pick="(n, v) => emit('pick', n, v)" @status="(e) => emit('status', e)"
                 @persist="(e) => emit('persist', e)" @focus="(id) => emit('focus', id)" />
    </div>
  </div>
  <!-- 终端面板节点 -->
  <div v-else class="sp-pane" :class="{ on: node.sessionId === activeId }">
    <div class="sp-pane-head">
      <el-select :model-value="node.sessionId" size="small" class="sp-sel"
                 :title="'切换此面板显示的终端'" @change="(v) => emit('pick', node, v)">
        <el-option v-for="s in sessions" :key="s.id" :label="s.title" :value="s.id" />
      </el-select>
      <el-dropdown trigger="click" @command="(c) => emit('split', node, c)">
        <el-button size="small" text class="sp-split-btn">分屏<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="h">左右分屏</el-dropdown-item>
            <el-dropdown-item command="v">上下分屏</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button size="small" text class="sp-close" title="关闭此终端" @click="emit('close', node)">
        <el-icon><Close /></el-icon>
      </el-button>
    </div>
    <div class="sp-pane-body" @click="emit('focus', node.sessionId)">
      <TermSession :key="node.sessionId" :id="node.sessionId" :db-id="dbId" :active="active"
                   :tmux-name="tmuxNameOf(node.sessionId)" :host-label="hostLabel"
                   :ref="(el) => regRef(node.sessionId, el)"
                   @status="(e) => emit('status', e)" @persist="(e) => emit('persist', e)" />
    </div>
  </div>
</template>

<style scoped>
.sp-split { display: flex; flex: 1; width: 100%; height: 100%; min-width: 0; min-height: 0; }
.sp-h { flex-direction: row; }
.sp-v { flex-direction: column; }
.sp-child { position: relative; display: flex; min-width: 0; min-height: 0; overflow: hidden; }
.sp-divider { flex-shrink: 0; background: var(--lc-border, #2a2e3d); z-index: 6; transition: background 0.15s; }
.sp-divider:hover, .sp-divider:active { background: var(--lc-primary, #409eff); }
.div-h { width: 5px; cursor: col-resize; }
.div-v { height: 5px; cursor: row-resize; }
.sp-pane { position: relative; flex: 1; width: 100%; height: 100%; display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.sp-pane-head { height: 28px; flex-shrink: 0; display: flex; align-items: center; gap: 2px; padding: 0 4px; background: #1a1d29; border-bottom: 1px solid var(--lc-border, #2a2e3d); }
.sp-pane.on .sp-pane-head {
  background: color-mix(in srgb, var(--lc-primary, #409eff) 9%, #1a1d29);
  box-shadow: inset 0 2px 0 color-mix(in srgb, var(--lc-primary, #409eff) 45%, transparent);
}
.sp-sel { flex: 1; min-width: 60px; max-width: 220px; }
.sp-split-btn { padding: 0 4px; font-size: 12px; color: var(--lc-text-muted, #999); }
.sp-split-btn:hover { color: var(--lc-primary, #409eff); }
.sp-close { padding: 0 4px; color: #888; }
.sp-close:hover { color: #ef4444; }
.sp-pane-body { position: relative; flex: 1; min-height: 0; }
</style>
