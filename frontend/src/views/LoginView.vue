<template>
  <div class="ctl-scope login-page">
    <div class="login-bg">
      <div class="login-orb orb-1" />
      <div class="login-orb orb-2" />
      <div class="login-orb orb-3" />
      <div class="login-grid-overlay" />
    </div>

    <div class="login-card">
      <div class="login-logo">🦙</div>
      <div class="login-title">
        <h1><span class="gradient-text">Llama</span>Lens · llama灵境</h1>
        <p>llama.cpp 多主机监控 + 服务管理控制台</p>
      </div>

      <el-steps :active="step" align-center class="steps" finish-status="success">
        <el-step title="账号登录" v-if="authEnabled" />
        <el-step title="连接主机" v-if="!hosts.length" />
      </el-steps>

      <el-form v-if="authEnabled && step === 0" label-position="top" @submit.prevent="doLogin">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="admin" size="large" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password size="large" @keyup.enter="doLogin" />
        </el-form-item>
        <el-button type="primary" class="full" size="large" :loading="loading" @click="doLogin">登 录</el-button>
      </el-form>

      <div v-else-if="step === 1">
        <el-form label-position="top">
          <div class="form-grid">
            <el-form-item label="别名">
              <el-input v-model="hform.alias" placeholder="可选，如 GPU-01" />
            </el-form-item>
            <el-form-item label="端口">
              <el-input-number v-model="hform.port" :min="1" :max="65535" style="width: 100%" />
            </el-form-item>
          </div>
          <el-form-item label="主机">
            <el-input v-model="hform.host" placeholder="主机名或 IP，如 ai.lan" />
          </el-form-item>
          <el-form-item label="用户名">
            <el-input v-model="hform.username" placeholder="root" />
          </el-form-item>
          <el-form-item label="认证方式">
            <el-radio-group v-model="hform.auth_type">
              <el-radio-button value="password">密码</el-radio-button>
              <el-radio-button value="key">私钥</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="hform.auth_type === 'password'" label="密码">
            <el-input v-model="hform.password" type="password" show-password />
          </el-form-item>
          <template v-else>
            <el-form-item label="私钥">
              <el-input v-model="hform.key_data" type="textarea" :rows="3"
                placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
            </el-form-item>
            <el-form-item label="密钥口令">
              <el-input v-model="hform.key_passphrase" type="password" show-password placeholder="可选" />
            </el-form-item>
          </template>
          <el-button type="primary" class="full" size="large" :loading="connecting" @click="connect">连 接</el-button>
        </el-form>
      </div>

      <div class="login-foot">SSH 凭证经 Fernet 加密存储 · 全量操作审计</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import http from '../api/client'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const store = useAuthStore()
const step = ref(0)
const loading = ref(false)
const connecting = ref(false)
const authEnabled = ref(true)
const hosts = ref([])
const form = reactive({ username: 'admin', password: '' })
const hform = reactive({
  alias: '', host: '', port: 22, username: 'root',
  auth_type: 'password', password: '', key_data: '', key_passphrase: '',
})

function home() {
  const redirect = route.query.redirect
  router.push(typeof redirect === 'string' && redirect.startsWith('/') ? redirect : '/')
}

onMounted(async () => {
  const cfg = await http.get('/auth/config')
  authEnabled.value = cfg.auth_enabled
  if (!cfg.auth_enabled) {
    await store.login('local', '')
    home()
    return
  }
  if (store.token) {
    hosts.value = await store.fetchHosts()
    if (hosts.value.length) home()
    else step.value = 1
  }
})

async function doLogin() {
  loading.value = true
  try {
    await store.login(form.username, form.password)
    hosts.value = await store.fetchHosts()
    if (hosts.value.length) home()
    else step.value = 1
  } finally {
    loading.value = false
  }
}

async function connect() {
  if (!hform.host || !hform.username) {
    ElMessage.warning('请填写主机和用户名')
    return
  }
  connecting.value = true
  try {
    const data = await http.post('/hosts/connect', { ...hform })
    hosts.value = await store.fetchHosts()
    store.setCurrentHost(data.host_id)
    ElMessage.success('连接成功，凭证已加密保存')
    home()
  } finally {
    connecting.value = false
  }
}
</script>

<style scoped>
.login-page { height: 100%; display: flex; align-items: center; justify-content: center; position: relative; }
.steps { margin-bottom: 24px; }
.full { width: 100%; margin-top: 6px; letter-spacing: 4px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
</style>
