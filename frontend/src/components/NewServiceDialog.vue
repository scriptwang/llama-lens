<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="新建服务"
    width="720px"
    destroy-on-close
  >
    <el-alert
      type="info"
      :closable="false"
      class="mb12"
      title="将在目标主机的 /etc/systemd/system 下创建新的 .service 单元文件，创建后自动执行 daemon-reload。已存在的同名服务会被拒绝。"
    />
    <el-form label-position="top">
      <div class="form-grid">
        <el-form-item label="服务名" required>
          <el-input v-model="form.name" placeholder="my-llama-server" @input="onNameInput">
            <template #append>.service</template>
          </el-input>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" placeholder="可选，如 Qwen3 27B 推理服务" />
        </el-form-item>
      </div>
      <el-form-item label="可执行文件路径" required>
        <el-input v-model="form.executable" placeholder="/home/wx/llama.cpp/build/bin/llama-server">
          <template #prefix><el-icon><Monitor /></el-icon></template>
        </el-input>
      </el-form-item>
      <el-form-item label="启动参数（空格分隔，与命令行一致）">
        <el-input
          v-model="form.args"
          type="textarea"
          :rows="3"
          placeholder="-m /path/to/model.gguf --n-gpu-layers 99 --ctx-size 8192 --port 8080 --host 0.0.0.0"
        />
      </el-form-item>
      <div class="form-grid">
        <el-form-item label="重启策略">
          <el-select v-model="form.restart" style="width: 100%">
            <el-option label="不重启（no）" value="no" />
            <el-option label="总是重启（always）" value="always" />
            <el-option label="失败时重启（on-failure）" value="on-failure" />
          </el-select>
        </el-form-item>
        <el-form-item label="开机自启">
          <el-switch v-model="form.enabled" active-text="是" inactive-text="否" />
        </el-form-item>
      </div>
    </el-form>

    <div class="preview-title">单元文件预览</div>
    <div class="preview-box">{{ preview }}</div>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="create">创建并 daemon-reload</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import http from '../api/client'
import { useAuthStore } from '../stores/auth'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'created'])
const store = useAuthStore()
const saving = ref(false)

const form = reactive({
  name: '',
  description: '',
  executable: '/home/wx/llama.cpp/build/bin/llama-server',
  args: '-m /path/to/model.gguf --n-gpu-layers 99 --ctx-size 8192 --port 8080 --host 0.0.0.0',
  restart: 'no',
  enabled: false,
})

watch(() => props.modelValue, (v) => {
  if (v) {
    form.name = ''
    form.description = ''
  }
})

function onNameInput(v) {
  form.name = v.replace(/\.service$/i, '').replace(/[^A-Za-z0-9._@-]/g, '')
}

const fullName = computed(() => (form.name ? `${form.name}.service` : ''))

const preview = computed(() => {
  const lines = ['[Unit]']
  lines.push(`Description=${form.description || fullName.value || 'llama.cpp service'}`)
  lines.push('After=network.target')
  lines.push('')
  lines.push('[Service]')
  lines.push('Type=simple')
  const cmd = [form.executable, form.args.trim()].filter(Boolean).join(' ')
  lines.push(`ExecStart=${cmd}`)
  lines.push(`Restart=${form.restart}`)
  lines.push('')
  if (form.enabled) {
    lines.push('[Install]')
    lines.push('WantedBy=multi-user.target')
    lines.push('')
  }
  return lines.join('\n').trimEnd()
})

async function create() {
  if (!fullName.value) {
    ElMessage.warning('请填写服务名')
    return
  }
  if (!form.executable.trim()) {
    ElMessage.warning('请填写可执行文件路径')
    return
  }
  saving.value = true
  try {
    const data = await http.post(
      '/services',
      { name: fullName.value, content: preview.value },
      { params: { host_id: store.currentHostId } },
    )
    ElMessage.success(`已创建 ${data.created} 并 daemon-reload`)
    emit('update:modelValue', false)
    emit('created')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.mb12 { margin-bottom: 12px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
</style>
