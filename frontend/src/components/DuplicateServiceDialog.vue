<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="复制服务"
    width="720px"
    destroy-on-close
   :teleported="false">
    <el-alert
      type="info"
      :closable="false"
      class="mb12"
      :title="`将复制 ${service ? service.name : ''} 的单元文件内容，在 /etc/systemd/system 下创建新的 .service 文件。下方内容可直接编辑（如改模型路径、端口等），内容中引用的原服务名会自动替换为新服务名，创建后自动执行 daemon-reload。已存在的同名服务会被拒绝。`"
    />
    <div v-loading="loading">
      <el-form label-position="top">
        <el-form-item label="新服务名" required>
          <el-input v-model="form.name" placeholder="my-llama-server-copy" @input="onNameInput">
            <template #append>.service</template>
          </el-input>
        </el-form-item>
      </el-form>

      <div class="preview-title">新单元文件内容（可直接编辑）</div>
      <el-input
        v-model="editContent"
        type="textarea"
        :rows="14"
        spellcheck="false"
        class="dup-content"
        @input="userEdited = true"
      />
    </div>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="loading" @click="duplicate">复制并 daemon-reload</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import http from '../api/client'
import { useAuthStore } from '../stores/auth'

const props = defineProps({
  modelValue: Boolean,
  service: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'created'])
const store = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const rawContent = ref('')
const editContent = ref('')
const userEdited = ref(false)

const form = reactive({ name: '' })

watch(
  () => props.modelValue,
  async (v) => {
    if (v && props.service) {
      form.name = props.service.name.replace(/\.service$/i, '') + '-copy'
      rawContent.value = ''
      editContent.value = ''
      userEdited.value = false
      loading.value = true
      try {
        const data = await http.get(`/services/${props.service.name}/config`, {
          params: { host_id: store.currentHostId },
        })
        rawContent.value = data.raw || ''
        editContent.value = rawContent.value
      } catch {
        rawContent.value = '（读取原服务内容失败，请关闭后重试）'
        editContent.value = ''
      } finally {
        loading.value = false
      }
    }
  },
)

function onNameInput(v) {
  form.name = v.replace(/\.service$/i, '').replace(/[^A-Za-z0-9._@-]/g, '')
  // 未手动编辑内容时，自动把内容中的原服务名替换为新服务名
  if (!userEdited.value && props.service && fullName.value && rawContent.value) {
    editContent.value = rawContent.value.split(props.service.name).join(fullName.value)
  }
}

const fullName = computed(() => (form.name ? `${form.name}.service` : ''))

async function duplicate() {
  if (!fullName.value) {
    ElMessage.warning('请填写新服务名')
    return
  }
  if (props.service && fullName.value === props.service.name) {
    ElMessage.warning('新服务名不能与原服务名相同')
    return
  }
  if (!editContent.value.trim()) {
    ElMessage.warning('服务文件内容不能为空')
    return
  }
  saving.value = true
  try {
    const data = await http.post(
      `/services/${props.service.name}/duplicate`,
      { new_name: fullName.value, content: editContent.value },
      { params: { host_id: store.currentHostId } },
    )
    ElMessage.success(`已复制为 ${data.created} 并 daemon-reload`)
    emit('update:modelValue', false)
    emit('created')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.mb12 { margin-bottom: 12px; }
.dup-content :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
}
</style>
