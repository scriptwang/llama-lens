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

      <el-form v-if="authEnabled" label-position="top" @submit.prevent="doLogin">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="admin" size="large" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password size="large" @keyup.enter="doLogin" />
        </el-form-item>
        <el-button type="primary" class="full" size="large" :loading="loading" @click="doLogin">登 录</el-button>
      </el-form>

      <div class="login-foot">SSH 凭证经 Fernet 加密存储 · 全量操作审计</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import http from '../api/client'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const store = useAuthStore()
const loading = ref(false)
const authEnabled = ref(true)
const form = reactive({ username: 'admin', password: '' })

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
  if (store.token) home()
})

// 登录成功后直接进门户；主机在门户【主机管理】中维护
async function doLogin() {
  loading.value = true
  try {
    await store.login(form.username, form.password)
    home()
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page { height: 100%; display: flex; align-items: center; justify-content: center; position: relative; }
.full { width: 100%; margin-top: 6px; letter-spacing: 4px; }
</style>
