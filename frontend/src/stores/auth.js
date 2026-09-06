import { defineStore } from 'pinia'
import http from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('llama_token') || '',
    username: '',
    authEnabled: true,
    hosts: [],
    currentHostId: Number(localStorage.getItem('llama_host_id') || 0),
  }),
  getters: {
    currentHost: (s) => s.hosts.find((h) => h.id === s.currentHostId) || null,
  },
  actions: {
    async login(username, password) {
      const data = await http.post('/auth/login', { username, password })
      this.token = data.token
      this.authEnabled = data.auth_enabled
      localStorage.setItem('llama_token', data.token)
    },
    async fetchHosts() {
      this.hosts = await http.get('/hosts')
      return this.hosts
    },
    setCurrentHost(id) {
      this.currentHostId = id
      localStorage.setItem('llama_host_id', String(id))
    },
    logout() {
      this.token = ''
      localStorage.removeItem('llama_token')
    },
  },
})
