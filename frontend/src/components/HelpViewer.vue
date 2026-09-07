<template>
  <div class="helpdesk">
    <div class="hd-head" @click="collapsed = !collapsed">
      <span class="hd-title">llama-server --help</span>
      <div class="hd-head-actions" @click.stop>
        <el-button v-show="!collapsed" size="small" text :loading="loading" title="刷新 --help" @click="$emit('refresh')">
          <el-icon><Refresh /></el-icon>
        </el-button>
        <el-icon><component :is="collapsed ? 'ArrowRight' : 'ArrowLeft'" /></el-icon>
      </div>
    </div>
    <div v-show="!collapsed" class="hd-content">
      <div v-if="version" class="hd-version">{{ version.split('\n')[0] }}</div>
      <el-input v-model="search" size="small" clearable placeholder="搜索 help 内容" class="hd-search">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <div v-if="error" class="hd-error">{{ error }}</div>
      <div v-else-if="!lines.length" class="hd-empty">暂无 --help 内容</div>
      <div v-else ref="body" class="hd-body">
        <div v-if="!visibleLines.length" class="hd-empty">无匹配内容</div>
        <div
          v-for="item in visibleLines"
          :key="item.i"
          class="hd-line"
          :class="{ match: isMatch(item.line), active: activeLine === item.i }"
          :data-line="item.i"
        >{{ item.line || ' ' }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  help: { type: String, default: '' },
  version: { type: String, default: '' },
  executable: { type: String, default: '' },
  loading: Boolean,
  error: { type: String, default: '' },
  highlight: { type: String, default: '' },
})
defineEmits(['refresh'])

const collapsed = ref(false)
const search = ref('')
const activeLine = ref(-1)
const body = ref(null)

const lines = computed(() => (props.help ? props.help.split('\n') : []))

const visibleLines = computed(() => {
  const all = lines.value
  const q = search.value.trim().toLowerCase()
  if (!q) return all.map((line, i) => ({ line, i }))
  const isFlag = (l) => /^\s*-{1,2}\S/.test(l)
  const keep = new Set()
  for (let i = 0; i < all.length; i++) {
    if (all[i].toLowerCase().includes(q)) {
      keep.add(i)
      if (isFlag(all[i])) {
        let j = i + 1
        while (j < all.length && !isFlag(all[j])) {
          keep.add(j)
          j++
        }
      }
    }
  }
  return all.map((line, i) => ({ line, i })).filter((x) => keep.has(x.i))
})

function isMatch(line) {
  const q = search.value.trim().toLowerCase()
  return q && line.toLowerCase().includes(q)
}

watch(
  () => props.highlight,
  async (flag) => {
    if (!flag) return
    await nextTick()
    const idx = lines.value.findIndex((l) => l.includes(flag))
    if (idx < 0) return
    activeLine.value = idx
    scrollToLine(idx)
    setTimeout(() => {
      if (activeLine.value === idx) activeLine.value = -1
    }, 2000)
  },
)

function scrollToLine(idx) {
  if (!body.value) return
  const el = body.value.querySelector(`[data-line="${idx}"]`)
  if (!el) return
  const bodyRect = body.value.getBoundingClientRect()
  const elRect = el.getBoundingClientRect()
  body.value.scrollTop += elRect.top - bodyRect.top - body.value.clientHeight / 2 + el.clientHeight / 2
}
</script>

<style scoped>
.helpdesk {
  width: 400px;
  min-width: 400px;
  height: 100%;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--lc-border);
  border-radius: 12px;
  background: var(--lc-surface-solid);
  box-shadow: var(--lc-shadow);
  overflow: hidden;
}
.helpdesk.collapsed { width: 44px; min-width: 44px; }
.hd-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  font-weight: 600;
  cursor: pointer;
  border-bottom: 1px solid var(--lc-border);
  user-select: none;
  color: var(--lc-text);
}
.helpdesk.collapsed .hd-head { flex-direction: column; gap: 6px; }
.helpdesk.collapsed .hd-title { display: none; }
.hd-title { font-size: 13px; }
.hd-head-actions { display: flex; align-items: center; gap: 4px; }
.hd-content { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.hd-version {
  padding: 8px 12px 0;
  font-size: 12px;
  color: var(--lc-text-muted);
  font-family: ui-monospace, Menlo, Consolas, monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.hd-search { padding: 8px 12px 4px; }
.hd-error { padding: 12px; font-size: 12px; color: var(--lc-danger); }
.hd-empty { padding: 12px; font-size: 12px; color: var(--lc-text-muted); }
.hd-body { flex: 1; overflow-y: auto; min-height: 0; padding: 4px 12px 12px; }
.hd-line {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: var(--lc-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  padding: 1px 4px;
  border-radius: 4px;
}
.hd-line.match { background: color-mix(in srgb, var(--lc-primary) 12%, transparent); color: var(--lc-text); }
.hd-line.active { background: color-mix(in srgb, var(--lc-warning) 22%, transparent); color: var(--lc-text); }
</style>
