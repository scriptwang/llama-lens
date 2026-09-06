<template>
  <el-drawer :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" title="操作日志" size="680px">
    <el-table :data="logs" size="small" v-loading="loading">
      <el-table-column prop="created_at" label="时间" width="165" />
      <el-table-column prop="user" label="用户" width="80" />
      <el-table-column prop="action" label="操作" width="120" />
      <el-table-column prop="service_name" label="服务" min-width="150" />
      <el-table-column prop="detail" label="详情" min-width="180" show-overflow-tooltip />
    </el-table>
  </el-drawer>
</template>

<script setup>
import { ref, watch } from 'vue'
import http from '../api/client'
import { useAuthStore } from '../stores/auth'

const props = defineProps({ modelValue: Boolean })
defineEmits(['update:modelValue'])
const store = useAuthStore()
const logs = ref([])
const loading = ref(false)

watch(
  () => props.modelValue,
  async (v) => {
    if (!v) return
    loading.value = true
    try {
      logs.value = await http.get('/logs', {
        params: { host_id: store.currentHostId || undefined, limit: 200 },
      })
    } finally {
      loading.value = false
    }
  },
)
</script>
