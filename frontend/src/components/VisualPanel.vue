<template>
  <div v-if="parsed" class="visual-panel">
    <div class="vp-head">
      <span class="vp-exec" :title="parsed.executable">{{ parsed.executable }}</span>
      <div class="vp-head-right">
        <el-checkbox v-model="restartOnSave">保存后重启</el-checkbox>
        <el-button type="primary" size="small" :loading="saving" @click="onSave">保存</el-button>
      </div>
    </div>

    <div class="vp-toolbar">
      <el-input v-model="search" size="small" clearable placeholder="搜索参数（flag / 别名 / 释义）" class="vp-search">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-input
        v-model="newFlag"
        size="small"
        clearable
        placeholder="+ 添加参数，如 --foo 或 -f"
        class="vp-add"
        @keyup.enter="onAdd"
      >
        <template #append><el-button @click="onAdd">添加</el-button></template>
      </el-input>
      <el-button size="small" title="将 ExecStart 格式化为多行（每参数一行 + 反斜杠续行）" @click="onReformat">格式化</el-button>
    </div>

    <div class="vp-list">
      <div
        v-for="a in filteredArgs"
        :key="a.key"
        class="param-row"
        :class="{ highlighted: isHighlighted(a) }"
        @click="onRowClick(a)"
      >
        <div class="param-label">
          <el-tooltip :content="tipFor(a)" placement="top">
            <span class="flag">
              {{ a.flag || '(位置参数)' }}<template v-if="aliasFor(a)"> / {{ aliasFor(a) }}</template>
            </span>
          </el-tooltip>
          <span class="desc">{{ descFor(a) }}</span>
        </div>
        <div class="param-control" @click.stop>
          <template v-if="entryOf(a) && entryOf(a).type === 'bool'">
            <el-switch :model-value="true" size="small" @update:model-value="setBool(a, $event)" />
          </template>
          <template v-else-if="entryOf(a) && entryOf(a).control === 'slider'">
            <el-slider
              :model-value="Number(a.value ?? entryOf(a).min ?? 0)"
              :min="entryOf(a).min ?? 0"
              :max="entryOf(a).max ?? 100"
              :step="entryOf(a).step || 1"
              size="small"
              style="width: 180px"
              @update:model-value="setVal(a, $event)"
            />
            <el-input-number
              :model-value="Number(a.value ?? 0)"
              :min="entryOf(a).min ?? undefined"
              :max="entryOf(a).max ?? undefined"
              :step="entryOf(a).step || 1"
              size="small"
              controls-position="right"
              style="width: 120px"
              @update:model-value="setVal(a, $event)"
            />
          </template>
          <template v-else-if="entryOf(a) && entryOf(a).control === 'select'">
            <el-select :model-value="a.value" size="small" style="width: 170px" @update:model-value="setVal(a, $event)">
              <el-option v-for="o in entryOf(a).options" :key="o" :label="o" :value="o" />
            </el-select>
          </template>
          <template v-else-if="entryOf(a) && entryOf(a).control === 'number'">
            <el-input-number
              :model-value="Number(a.value ?? 0)"
              :min="entryOf(a).min ?? undefined"
              :max="entryOf(a).max ?? undefined"
              :step="entryOf(a).step || 1"
              size="small"
              controls-position="right"
              style="width: 160px"
              @update:model-value="setVal(a, $event)"
            />
          </template>
          <template v-else>
            <el-input
              :model-value="a.value ?? ''"
              size="small"
              style="width: 260px"
              :placeholder="isPath(a) ? '/path/to/file.gguf' : '值（留空=仅 flag）'"
              @update:model-value="setVal(a, $event)"
            />
          </template>
          <el-button v-if="isPath(a)" size="small" text title="浏览服务器文件" @click="openBrowse(a)">
            <el-icon><FolderOpened /></el-icon>
          </el-button>
          <el-button size="small" text type="danger" title="删除该参数" @click="removeArg(a)">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
      </div>
      <el-empty v-if="!filteredArgs.length" :description="search ? '无匹配参数' : '暂无参数，请在上方添加'" :image-size="60" />
    </div>

    <FileBrowser
      v-if="browseTarget"
      :host-id="hostId"
      :roots="browseRoots"
      :initial-path="typeof browseTarget.value === 'string' ? browseTarget.value : ''"
      @select="onBrowseSelect"
      @close="browseTarget = null"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import FileBrowser from './FileBrowser.vue'

const props = defineProps({
  parsed: { type: Object, default: null },
  dict: { type: Object, default: () => ({}) },
  saving: Boolean,
  highlight: { type: String, default: '' },
  hostId: { type: Number, default: 0 },
  browsePaths: { type: String, default: '' },
})
const emit = defineEmits(['save', 'param-click', 'args-change', 'reformat'])

const localArgs = ref([])
const search = ref('')
const newFlag = ref('')
const restartOnSave = ref(true)
const browseTarget = ref(null)

const browseRoots = computed(() =>
  (props.browsePaths || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
)

watch(
  () => props.parsed,
  (p) => {
    if (!p) {
      localArgs.value = []
      return
    }
    localArgs.value = p.args.map((a, i) => {
      const e = a.canonical ? props.dict[a.canonical] : null
      const isBool = e && e.type === 'bool'
      return { ...a, key: `${a.flag || 'pos'}_${i}`, value: isBool ? true : a.value }
    })
  },
  { immediate: true },
)

const filteredArgs = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return localArgs.value
  return localArgs.value.filter((a) => {
    const e = entryOf(a)
    const hay = [
      a.flag,
      a.canonical,
      e ? e.desc : '',
      e && e.aliases ? e.aliases.join(' ') : '',
    ]
      .join(' ')
      .toLowerCase()
    return hay.includes(q)
  })
})

function entryOf(a) {
  if (!a || !a.canonical || !props.dict[a.canonical]) return null
  return props.dict[a.canonical]
}

function resolveCanonical(flag) {
  if (!flag) return null
  if (props.dict && props.dict[flag]) return flag
  for (const [c, e] of Object.entries(props.dict || {})) {
    if (e.aliases && e.aliases.includes(flag)) return c
  }
  return null
}

function isHighlighted(a) {
  return props.highlight === a.canonical || props.highlight === a.flag
}

function onRowClick(a) {
  const flag = a.canonical || a.flag
  if (flag) emit('param-click', flag)
}

function onAdd() {
  let flag = newFlag.value.trim()
  if (!flag) return
  if (!flag.startsWith('-')) flag = '--' + flag
  if (localArgs.value.some((a) => a.flag === flag || a.canonical === flag)) {
    ElMessage.warning('该参数已存在')
    return
  }
  const canonical = resolveCanonical(flag)
  const e = canonical ? props.dict[canonical] : null
  localArgs.value.push({
    flag,
    canonical,
    value: e ? (e.type === 'bool' ? true : e.default !== undefined && e.default !== '' ? e.default : '') : '',
    known: !!e,
    key: `${flag}_${Date.now()}`,
  })
  newFlag.value = ''
  emitArgs()
}

function setVal(a, v) {
  a.value = v
  emitArgs()
}

function setBool(a, v) {
  if (v) a.value = true
  else localArgs.value = localArgs.value.filter((x) => x.key !== a.key)
  emitArgs()
}

function removeArg(a) {
  localArgs.value = localArgs.value.filter((x) => x.key !== a.key)
  emitArgs()
}

function isPath(a) {
  const e = entryOf(a)
  if (e && e.type === 'path') return true
  return typeof a.value === 'string' && a.value.startsWith('/')
}

function openBrowse(a) {
  browseTarget.value = a
}

function onBrowseSelect(path) {
  if (browseTarget.value) browseTarget.value.value = path
  browseTarget.value = null
  emitArgs()
}

function aliasFor(a) {
  const e = entryOf(a)
  if (!e || !e.aliases || !e.aliases.length) return ''
  return e.aliases.find((al) => al !== a.flag) || ''
}

function descFor(a) {
  const e = entryOf(a)
  return e ? e.desc : '自定义参数，可自由编辑'
}

function tipFor(a) {
  const e = entryOf(a)
  if (!e) return '自定义参数（不在内置字典中），可自由编辑；完整说明见右侧 --help'
  let tip = `${e.desc}\n默认: ${e.default === '' || e.default === undefined ? '—' : e.default}`
  if (e.min !== null && e.min !== undefined || e.max !== null && e.max !== undefined) {
    tip += `\n范围: ${e.min ?? '—'} ~ ${e.max ?? '—'}`
  }
  if (e.options) tip += `\n可选: ${e.options.join(' / ')}`
  return tip
}

function emitArgs() {
  emit('args-change', localArgs.value.map((a) => ({
    flag: a.flag,
    canonical: a.canonical,
    value: a.value,
    known: a.known,
    positional: a.positional || false,
  })))
}

function onReformat() {
  emit('reformat', localArgs.value.map((a) => ({
    flag: a.flag,
    canonical: a.canonical,
    value: a.value,
    known: a.known,
    positional: a.positional || false,
  })))
}

function onSave() {
  const args = localArgs.value.map((a) => ({
    flag: a.flag,
    canonical: a.canonical,
    value: a.value,
    known: a.known,
    positional: a.positional || false,
  }))
  emit('save', args, restartOnSave.value)
}
</script>

<style scoped>
.visual-panel {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--lc-border);
  border-radius: 12px;
  background: var(--lc-surface-solid);
  box-shadow: var(--lc-shadow);
  overflow: hidden;
}
.vp-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--lc-border);
  flex-wrap: wrap;
}
.vp-exec {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-weight: 600;
  font-size: 13px;
  color: var(--lc-text);
}
.vp-head-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.vp-toolbar {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--lc-border);
  flex-wrap: wrap;
}
.vp-search { flex: 1; min-width: 180px; }
.vp-add { width: 240px; }
.vp-list { flex: 1; overflow-y: auto; min-height: 0; padding: 6px 8px; }
.param-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s ease;
}
.param-row:hover { background: rgba(79, 110, 247, 0.07); }
.param-row.highlighted { border-color: var(--lc-warning); background: rgba(245, 158, 11, 0.1); }
.param-label { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.flag {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 13px;
  font-weight: 600;
  color: var(--lc-text);
}
.desc { font-size: 12px; color: var(--lc-text-muted); }
.param-control { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
</style>
