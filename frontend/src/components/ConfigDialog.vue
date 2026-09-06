<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    :title="`编辑配置 - ${service ? service.name : ''}`"
    fullscreen
    destroy-on-close
  >
    <div v-loading="loading">
      <el-alert
        v-if="parseError"
        type="warning"
        :closable="false"
        class="mb8"
        :title="`ExecStart 解析失败：${parseError}，已回退到源码模式`"
      />
      <el-alert
        v-if="!inEtc"
        type="info"
        :closable="false"
        class="mb8"
        title="该服务文件不在 /etc/systemd/system 下（发行版目录），修改可能在下一次系统更新时被覆盖"
      />
      <el-tabs v-model="tab">
        <el-tab-pane label="源码模式" name="source">
          <SourceEditor v-model="rawContent" :original="originalContent" />
          <div class="editor-bar">
            <el-checkbox v-model="restartAfterSave">保存后重启服务</el-checkbox>
            <el-button type="primary" :loading="saving" @click="saveSource">保存（备份 + daemon-reload）</el-button>
          </div>
        </el-tab-pane>
        <el-tab-pane label="可视化参数" name="visual" :disabled="!parsed">
          <div class="visual-layout">
            <VisualPanel
              :parsed="parsed"
              :dict="dict"
              :saving="saving"
              :highlight="highlightFlag"
              :host-id="store.currentHostId"
              :browse-paths="browsePaths"
              @save="saveVisual"
              @param-click="onParamClick"
              @args-change="onArgsChange"
              @reformat="onReformat"
            />
            <HelpViewer
              :help="help.help"
              :version="help.version"
              :executable="help.executable"
              :loading="helpLoading"
              :error="helpError"
              :highlight="highlightFlag"
              @refresh="onHelpRefresh"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import http from '../api/client'
import { useAuthStore } from '../stores/auth'
import SourceEditor from './SourceEditor.vue'
import VisualPanel from './VisualPanel.vue'
import HelpViewer from './HelpViewer.vue'

const props = defineProps({ modelValue: Boolean, service: Object })
const emit = defineEmits(['update:modelValue', 'saved'])
const store = useAuthStore()

const loading = ref(false)
const saving = ref(false)
const tab = ref('source')
const rawContent = ref('')
const originalContent = ref('')
const parsed = ref(null)
const parseError = ref('')
const inEtc = ref(true)
const dict = ref({})
const restartAfterSave = ref(false)
const highlightFlag = ref('')
const help = ref({ help: '', version: '', executable: '' })
const helpLoading = ref(false)
const helpError = ref('')

const browsePaths = computed(() => (store.currentHost && store.currentHost.browse_paths) || '')

// 参数字典是静态数据，模块级缓存（弹窗 destroy-on-close 后组件内缓存会丢失）
let dictPromise = null
function loadDict() {
  if (!dictPromise) {
    dictPromise = http.get('/params').catch((e) => {
      dictPromise = null
      throw e
    })
  }
  return dictPromise
}

// 源码 <-> 可视化 实时联动：rawContent 为唯一数据源
const skipParse = ref(false)
const currentStyle = ref('auto')
let parseTimer = null
let buildTimer = null

onBeforeUnmount(() => {
  clearTimeout(parseTimer)
  clearTimeout(buildTimer)
})

function setRawContent(content) {
  skipParse.value = true
  rawContent.value = content
  nextTick(() => { skipParse.value = false })
}

watch(rawContent, (content) => {
  if (skipParse.value) {
    skipParse.value = false
    return
  }
  if (!content) return
  clearTimeout(parseTimer)
  parseTimer = setTimeout(() => doParse(content), 400)
})

async function doParse(content) {
  try {
    const data = await http.post('/parse', { content })
    if (rawContent.value !== content) return
    parsed.value = data.parsed
    parseError.value = data.parse_error || ''
  } catch (e) { /* 预览解析失败忽略 */ }
}

function onArgsChange(args) {
  clearTimeout(buildTimer)
  buildTimer = setTimeout(() => doBuild(args, currentStyle.value), 400)
}

async function doBuild(args, style = 'auto') {
  const base = rawContent.value
  try {
    const data = await http.post('/build', { content: base, args, style })
    if (rawContent.value !== base) return
    setRawContent(data.content)
  } catch (e) { /* 预览拼接失败忽略 */ }
}

function onReformat(args) {
  currentStyle.value = 'multiline'
  doBuild(args, 'multiline')
}

async function loadConfig(resetTab = true) {
  if (!props.service) return
  loading.value = true
  try {
    const data = await http.get(`/services/${props.service.name}/config`, {
      params: { host_id: store.currentHostId },
    })
    setRawContent(data.raw)
    currentStyle.value = 'auto'
    originalContent.value = data.raw
    parsed.value = data.parsed
    parseError.value = data.parse_error || ''
    inEtc.value = data.in_etc
    if (resetTab) tab.value = data.parsed ? 'visual' : 'source'
    if (!Object.keys(dict.value).length) dict.value = await loadDict()
    if (data.parsed) loadHelp(false)
  } finally {
    loading.value = false
  }
}

async function loadHelp(refresh = false) {
  if (!props.service) return
  helpLoading.value = true
  helpError.value = ''
  try {
    const data = await http.get(`/services/${props.service.name}/help`, {
      params: { host_id: store.currentHostId, refresh },
    })
    help.value = data
  } catch (e) {
    helpError.value = e.message || '获取 --help 失败'
  } finally {
    helpLoading.value = false
  }
}

function onHelpRefresh() {
  loadHelp(true)
}

watch(
  () => props.modelValue,
  (v) => {
    if (v) loadConfig()
  },
)

async function saveSource() {
  saving.value = true
  try {
    const data = await http.put(
      `/services/${props.service.name}/config`,
      { mode: 'source', content: rawContent.value, restart: restartAfterSave.value },
      { params: { host_id: store.currentHostId } },
    )
    ElMessage.success(`已保存，备份：${data.backup}` + (data.restarted ? '，服务已重启' : ''))
    emit('saved')
    await loadConfig(false)
  } finally {
    saving.value = false
  }
}

async function saveVisual(args, restart) {
  saving.value = true
  try {
    const data = await http.put(
      `/services/${props.service.name}/config`,
      { mode: 'visual', args, restart: !!restart, style: currentStyle.value },
      { params: { host_id: store.currentHostId } },
    )
    if (data.restarted) ElMessage.success(`已保存并重启服务，备份：${data.backup}`)
    else if (data.restart_error) ElMessage.warning(`已保存（重启失败：${data.restart_error}），备份：${data.backup}`)
    else ElMessage.success(`已保存，备份：${data.backup}`)
    emit('saved')
    await loadConfig(false)
  } finally {
    saving.value = false
  }
}

function onParamClick(flag) {
  highlightFlag.value = flag
}
</script>

<style scoped>
.mb8 { margin-bottom: 8px; }
.editor-bar { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; }
.visual-layout {
  display: flex;
  gap: 14px;
  align-items: stretch;
  height: calc(100vh - 150px);
}
</style>
