import axios from 'axios'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import router from '../router'

const http = axios.create({ baseURL: '/api', timeout: 120000 })

http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('llama_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

http.interceptors.response.use(
  (res) => {
    const body = res.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) return body.data
      if (body.code === 3001) {
        localStorage.removeItem('llama_token')
        router.push('/login')
      }
      if (!res.config?.silent) ElMessage.error(body.msg || '请求失败')
      return Promise.reject(new Error(body.msg))
    }
    return body
  },
  (err) => {
    const body = err.response?.data
    if (err.response?.status === 401 || body?.code === 3001) {
      localStorage.removeItem('llama_token')
      router.push('/login')
    }
    if (!err.config?.silent) ElMessage.error(body?.msg || err.message || '网络错误')
    return Promise.reject(err)
  },
)

export default http
