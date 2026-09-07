<template>
  <div class="acct">
    <el-dropdown trigger="click" @command="onCommand">
      <button class="acct-btn" title="账户">
        <el-icon style="margin-right:3px"><Setting /></el-icon>{{ username || '账户' }}
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="password">修改密码</el-dropdown-item>
          <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>

    <el-dialog v-model="pwdVisible" title="修改密码" width="400px" append-to-body :close-on-click-modal="false">
      <el-form label-position="top" @submit.prevent="doChange">
        <el-form-item label="当前密码">
          <el-input v-model="pwdForm.old" type="password" show-password autocomplete="current-password" />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwdForm.new1" type="password" show-password autocomplete="new-password" placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="pwdForm.new2" type="password" show-password autocomplete="new-password" @keyup.enter="doChange" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="pwdVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="pwdLoading" @click="doChange">确认修改</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import http from '../api/client'
import { useAuthStore } from '../stores/auth'

// 账户菜单：修改密码 + 退出登录（门户 BrandBar 与主机详情 TopBar 共用）
const router = useRouter()
const store = useAuthStore()
const username = ref('')
const pwdVisible = ref(false)
const pwdLoading = ref(false)
const pwdForm = reactive({ old: '', new1: '', new2: '' })

onMounted(async () => {
  try {
    const me = await http.get('/auth/me', { silent: true })
    username.value = me.username
  } catch (e) { /* 未登录时忽略 */ }
})

function onCommand(cmd) {
  if (cmd === 'password') {
    pwdForm.old = pwdForm.new1 = pwdForm.new2 = ''
    pwdVisible.value = true
  } else if (cmd === 'logout') {
    store.logout()
    router.push('/login')
  }
}

async function doChange() {
  if (!pwdForm.old || !pwdForm.new1) {
    ElMessage.warning('请填写当前密码和新密码')
    return
  }
  if (pwdForm.new1.length < 6) {
    ElMessage.warning('新密码至少 6 位')
    return
  }
  if (pwdForm.new1 !== pwdForm.new2) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  pwdLoading.value = true
  try {
    await http.post('/auth/password', { old_password: pwdForm.old, new_password: pwdForm.new1 })
    ElMessage.success('密码已修改，请下次登录使用新密码')
    pwdVisible.value = false
  } catch (e) { /* client 已提示 */ } finally {
    pwdLoading.value = false
  }
}
</script>

<style scoped>
.acct { display: inline-flex; flex: none; }
.acct-btn {
  display: inline-flex;
  align-items: center;
  background: transparent;
  border: 1px solid var(--card-border);
  color: var(--text-dim);
  font-size: 12px;
  font-weight: 500;
  padding: 4px 12px;
  border-radius: 12px;
  cursor: pointer;
  font-family: inherit;
  transition: color .15s, border-color .15s, background .15s;
}
.acct-btn:hover {
  color: var(--cyan);
  border-color: color-mix(in srgb, var(--cyan) 50%, transparent);
  background: color-mix(in srgb, var(--cyan) 8%, transparent);
}
</style>
