<template>
  <el-drawer :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" title="主机管理" size="640px" :teleported="false">
    <div class="toolbar">
      <el-button type="primary" size="small" @click="openCreate">
        <el-icon><Plus /></el-icon>新建主机
      </el-button>
    </div>
    <el-table :data="hosts" size="small">
      <el-table-column prop="alias" label="别名" width="90" />
      <el-table-column label="地址" min-width="140">
        <template #default="{ row }">{{ row.host }}:{{ row.port }}</template>
      </el-table-column>
      <el-table-column prop="username" label="用户" width="70" />
      <el-table-column label="浏览路径" min-width="120">
        <template #default="{ row }">
          <span v-if="row.browse_paths" class="bp">{{ row.browse_paths }}</span>
          <span v-else class="bp-empty">未配置</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="210">
        <template #default="{ row }">
          <el-button size="small" text type="primary" :loading="testingId === row.id" @click="test(row)">测试</el-button>
          <el-button size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" text type="primary" @click="use(row)">使用</el-button>
          <el-button size="small" text type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!hosts.length" description="暂无主机，点击右上角「新建主机」添加" />
    <div class="tip">提示：凭证以 Fernet 加密存储于本地 SQLite，接口永不回传明文。浏览路径用于可视化面板文件选择器的快捷目录（逗号分隔，如 /share,/models）。</div>

    <el-dialog v-model="showEdit" title="编辑主机" width="480px" append-to-body :teleported="false">
      <el-form label-position="top">
        <el-form-item label="别名">
          <el-input v-model="editForm.alias" placeholder="可选" />
        </el-form-item>
        <el-form-item label="文件浏览快捷目录（逗号分隔）">
          <el-input v-model="editForm.browse_paths" placeholder="如 /share,/models，用于模型 / mmproj 文件选择" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEdit = false">取消</el-button>
        <el-button type="primary" :loading="savingEdit" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showCreate" title="新建主机" width="480px" append-to-body :teleported="false">
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="别名">
            <el-input v-model="createForm.alias" placeholder="可选，如 GPU-01" />
          </el-form-item>
          <el-form-item label="端口">
            <el-input-number v-model="createForm.port" :min="1" :max="65535" style="width: 100%" />
          </el-form-item>
        </div>
        <el-form-item label="主机">
          <el-input v-model="createForm.host" placeholder="主机名或 IP，如 ai.lan" />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="createForm.username" placeholder="root" />
        </el-form-item>
        <el-form-item label="认证方式">
          <el-radio-group v-model="createForm.auth_type">
            <el-radio-button value="password">密码</el-radio-button>
            <el-radio-button value="key">私钥</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="createForm.auth_type === 'password'" label="密码">
          <el-input v-model="createForm.password" type="password" show-password />
        </el-form-item>
        <template v-else>
          <el-form-item label="私钥">
            <el-input
              v-model="createForm.key_data"
              type="textarea"
              :rows="3"
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
            />
          </el-form-item>
          <el-form-item label="密钥口令">
            <el-input v-model="createForm.key_passphrase" type="password" show-password placeholder="可选" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="savingCreate" @click="submitCreate">连接并保存</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import http from '../api/client'
import { useAuthStore } from '../stores/auth'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'changed'])
const store = useAuthStore()
const hosts = ref([])
const testingId = ref(0)
const showEdit = ref(false)
const savingEdit = ref(false)
const editForm = reactive({ id: 0, alias: '', browse_paths: '' })
const showCreate = ref(false)
const savingCreate = ref(false)
const createForm = reactive({
  alias: '', host: '', port: 22, username: 'root',
  auth_type: 'password', password: '', key_data: '', key_passphrase: '',
})

watch(
  () => props.modelValue,
  async (v) => {
    if (v) hosts.value = await store.fetchHosts()
  },
)

async function test(row) {
  testingId.value = row.id
  try {
    await http.post(`/hosts/${row.id}/test`)
    ElMessage.success('连接正常')
  } finally {
    testingId.value = 0
  }
}

function openEdit(row) {
  editForm.id = row.id
  editForm.alias = row.alias || ''
  editForm.browse_paths = row.browse_paths || ''
  showEdit.value = true
}

async function saveEdit() {
  savingEdit.value = true
  try {
    await http.put(`/hosts/${editForm.id}`, { alias: editForm.alias, browse_paths: editForm.browse_paths })
    ElMessage.success('已保存')
    showEdit.value = false
    hosts.value = await store.fetchHosts()
  } finally {
    savingEdit.value = false
  }
}

function use(row) {
  store.setCurrentHost(row.db_id)
  emit('update:modelValue', false)
  emit('changed')
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除主机 ${row.alias || row.host} 吗？`, '删除确认', { type: 'warning' })
  await http.delete(`/hosts/${row.db_id}`)
  ElMessage.success('已删除')
  hosts.value = await store.fetchHosts()
}

function openCreate() {
  createForm.alias = ''
  createForm.host = ''
  createForm.port = 22
  createForm.username = 'root'
  createForm.auth_type = 'password'
  createForm.password = ''
  createForm.key_data = ''
  createForm.key_passphrase = ''
  showCreate.value = true
}

async function submitCreate() {
  if (!createForm.host || !createForm.username) {
    ElMessage.warning('请填写主机和用户名')
    return
  }
  if (createForm.auth_type === 'password' && !createForm.password) {
    ElMessage.warning('请填写密码')
    return
  }
  if (createForm.auth_type === 'key' && !createForm.key_data) {
    ElMessage.warning('请填写私钥内容')
    return
  }
  savingCreate.value = true
  try {
    const data = await http.post('/hosts/connect', { ...createForm })
    ElMessage.success('连接成功，凭证已加密保存')
    showCreate.value = false
    hosts.value = await store.fetchHosts()
    store.setCurrentHost(data.host_id)
    emit('changed')
  } finally {
    savingCreate.value = false
  }
}
</script>

<style scoped>
.tip { margin-top: 12px; font-size: 12px; color: #909399; }
.toolbar { margin-bottom: 12px; display: flex; justify-content: flex-end; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
.bp { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; color: var(--lc-text-secondary); word-break: break-all; }
.bp-empty { font-size: 12px; color: var(--lc-text-muted); }
</style>
