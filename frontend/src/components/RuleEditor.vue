<template>
  <el-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" title="扫描规则" width="600px" :teleported="false">
    <el-alert
      type="info"
      :closable="false"
      class="mb8"
      title="服务识别 = 名称匹配 或 内容匹配。内容匹配会扫描单元文件中的可执行文件名与标记，因此即使服务名不含 llama/sglang 也能被识别。"
    />
    <el-form label-width="110px">
      <el-form-item label="名称关键词">
        <el-select v-model="rules.name_keywords" multiple filterable allow-create default-first-option placeholder="输入后回车添加" style="width: 100%">
          <el-option v-for="k in rules.name_keywords" :key="k" :label="k" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="可执行文件名">
        <el-select v-model="rules.binary_names" multiple filterable allow-create default-first-option placeholder="输入后回车添加" style="width: 100%">
          <el-option v-for="k in rules.binary_names" :key="k" :label="k" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="内容标记">
        <el-select v-model="rules.content_markers" multiple filterable allow-create default-first-option placeholder="输入后回车添加" style="width: 100%">
          <el-option v-for="k in rules.content_markers" :key="k" :label="k" :value="k" />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import http from '../api/client'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'saved'])
const saving = ref(false)
const rules = reactive({ name_keywords: [], binary_names: [], content_markers: [] })

watch(
  () => props.modelValue,
  async (v) => {
    if (v) Object.assign(rules, await http.get('/scan-rules'))
  },
)

async function save() {
  saving.value = true
  try {
    await http.put('/scan-rules', { ...rules })
    ElMessage.success('规则已保存，下次扫描生效')
    emit('update:modelValue', false)
    emit('saved')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.mb8 { margin-bottom: 8px; }
</style>
