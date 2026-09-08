<template>
  <div class="editor-shell">
    <div class="editor-toolbar">
      <span class="file-dot" />
      <span class="file-name">.service 单元文件</span>
      <span class="diff-badge">对比模式</span>
      <span class="toolbar-spacer" />
      <span class="diff-legend">
        <span class="legend-item"><i class="swatch swatch-orig"></i>原始 · 只读</span>
        <span class="legend-item"><i class="swatch swatch-mod"></i>编辑</span>
      </span>
      <span class="theme-label">
        <el-icon><Brush /></el-icon> 编辑器主题
      </span>
      <el-select
        :model-value="ui.editorThemeId"
        size="small"
        style="width: 170px"
        @change="onThemeChange"
      >
        <el-option
          v-for="t in EDITOR_THEMES"
          :key="t.id"
          :label="t.label"
          :value="t.id"
        >
          <span style="display:flex;align-items:center;gap:8px">
            <span class="theme-swatch" :style="{ background: t.base.bg }">
              <i :style="{ background: t.colors.heading }"></i>
              <i :style="{ background: t.colors.string, width: '72%' }"></i>
              <i :style="{ background: t.colors.comment, width: '46%' }"></i>
            </span>
            {{ t.label }}
          </span>
        </el-option>
      </el-select>
    </div>
    <div ref="host" class="editor-body"></div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EDITOR_THEMES, registerMonaco } from '../themes/editorThemes'
import { useUiStore } from '../stores/ui'

// modelValue：右侧可编辑内容（v-model）。
// original：左侧只读基线（上次保存的内容），用于实时对比差异。
const props = defineProps({
  modelValue: String,
  original: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])
const ui = useUiStore()
const host = ref(null)
let diffEditor = null
let originalModel = null
let modifiedModel = null
let monaco = null
let suppress = false

const DIFF_OPTIONS = {
  originalEditable: false,
  renderSideBySide: true,
  automaticLayout: true,
  minimap: { enabled: true, renderCharacters: false, maxColumn: 120 },
  fontSize: 16,
  fontFamily: 'ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, Consolas, "Liberation Mono", monospace',
  lineHeight: 1.5,
  scrollBeyondLastLine: false,
  renderLineHighlight: 'line',
  renderWhitespace: 'selection',
  wordWrap: 'on',
  mouseWheelZoom: true,
  tabSize: 4,
  padding: { top: 8, bottom: 8 },
  scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
  fixedOverflowWidgets: true,
}

onMounted(async () => {
  // 自定义入口：editor.api 核心 + 编辑 contributions（find/zoom/keybinding/diff），
  // 不含内置语言与 worker（systemd 高亮走主线程，避免 worker 回归）。
  monaco = (await import('../themes/monacoEntry')).default
  registerMonaco(monaco)
  monaco.editor.setTheme(ui.editorThemeId)

  // 左侧只读基线 + 右侧可编辑，语言均为 systemd（高亮一致）。
  originalModel = monaco.editor.createModel(props.original || '', 'systemd')
  modifiedModel = monaco.editor.createModel(props.modelValue || '', 'systemd')
  diffEditor = monaco.editor.createDiffEditor(host.value, DIFF_OPTIONS)
  diffEditor.setModel({ original: originalModel, modified: modifiedModel })

  modifiedModel.onDidChangeContent(() => {
    if (!suppress) emit('update:modelValue', modifiedModel.getValue())
  })
})

watch(
  () => props.modelValue,
  (v) => {
    if (!modifiedModel || v === modifiedModel.getValue()) return
    suppress = true
    modifiedModel.setValue(v)
    suppress = false
  },
)

watch(
  () => props.original,
  (v) => {
    if (!originalModel) return
    originalModel.setValue(v || '')
  },
)

function onThemeChange(id) {
  ui.setEditorTheme(id)
  if (monaco) monaco.editor.setTheme(id)
}

onBeforeUnmount(() => {
  if (diffEditor) { diffEditor.dispose(); diffEditor = null }
  if (originalModel) { originalModel.dispose(); originalModel = null }
  if (modifiedModel) { modifiedModel.dispose(); modifiedModel = null }
})
</script>

<style scoped>
.toolbar-spacer { flex: 1; }
.diff-badge {
  padding: 1px 9px; border-radius: 10px; font-size: 12px;
  color: var(--lc-text-muted); background: var(--lc-surface);
  border: 1px solid var(--lc-border);
}
.diff-legend {
  display: inline-flex; align-items: center; gap: 12px;
  font-size: 12px; color: var(--lc-text-muted);
}
.legend-item { display: inline-flex; align-items: center; gap: 5px; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; }
.swatch-orig { background: var(--lc-text-muted); opacity: 0.55; }
.swatch-mod { background: var(--lc-primary); }
.theme-label {
  display: inline-flex; align-items: center; gap: 5px;
  color: var(--lc-text-muted);
}
/* 迷你主题预览：底色=主题背景 + 三条语法色"代码行"。
   纯色块在明暗下拉背景上都会有一方隐形（黑块配暗底/白块配亮底），
   内部语法色保证任何背景下都有对比，同时真实预览主题观感 */
.theme-swatch {
  display: inline-flex; flex-direction: column; justify-content: center; gap: 2px;
  width: 18px; height: 18px; padding: 3px; box-sizing: border-box;
  border-radius: 4px; flex: none;
  border: 1px solid rgba(128, 128, 128, 0.55);
}
.theme-swatch i {
  display: block; height: 2px; border-radius: 1px;
}
</style>
