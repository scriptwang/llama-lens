import { createRouter, createWebHistory } from 'vue-router'
import PortalView from './views/PortalView.vue'
import HostDetailView from './views/HostDetailView.vue'
import LoginView from './views/LoginView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'portal', component: PortalView },
    { path: '/host/:id', name: 'host', component: HostDetailView, props: true },
    { path: '/login', name: 'login', component: LoginView }
  ]
})

// 无 token 一律进登录页；auth 关闭时 LoginView 会自动以 local 登录放行
router.beforeEach((to) => {
  const token = localStorage.getItem('llama_token')
  if (!token && to.path !== '/login') {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
})

export default router
