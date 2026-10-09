async function getJson(url) {
  const headers = { Accept: 'application/json' }
  const token = localStorage.getItem('llama_token')
  if (token) headers.Authorization = `Bearer ${token}`
  const resp = await fetch(url, { headers })
  if (resp.status === 401) {
    localStorage.removeItem('llama_token')
    window.location.href = '/login'
    throw new Error(`${url} → 401`)
  }
  if (!resp.ok) throw new Error(`${url} → ${resp.status}`)
  return resp.json()
}

async function request(method, url, body) {
  const headers = { Accept: 'application/json' }
  const token = localStorage.getItem('llama_token')
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const resp = await fetch(url, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined })
  if (resp.status === 401) {
    localStorage.removeItem('llama_token')
    window.location.href = '/login'
    throw new Error(`${url} → 401`)
  }
  if (!resp.ok) throw new Error(`${url} → ${resp.status}`)
  const j = await resp.json()
  return j && typeof j === 'object' && 'data' in j ? j.data : j
}

export const api = {
  hosts: () => getJson('/api/hosts'),
  overview: (id) => getJson(`/api/hosts/${encodeURIComponent(id)}/overview`),
  history: (id, window, start, end) => {
    const q = new URLSearchParams()
    if (window) q.set('window', window)
    if (start) q.set('start', start)
    if (end) q.set('end', end)
    return getJson(`/api/hosts/${encodeURIComponent(id)}/history?${q}`)
  },
  events: (id, limit = 50) => getJson(`/api/hosts/${encodeURIComponent(id)}/events?limit=${limit}`),
  // /api/ui 是 ctl 接口（{code,msg,data} 信封），需解包
  uiConfig: async () => {
    const j = await getJson('/api/ui')
    return j && typeof j === 'object' && 'data' in j ? j.data : j
  },
  // 网关（集群视角，详见 docs/07）
  gatewayStatus: () => request('GET', '/api/gateway/status'),
  gatewayKeys: () => request('GET', '/api/gateway/keys'),
  gatewayCreateKey: (body) => request('POST', '/api/gateway/keys', body),
  gatewayUpdateKey: (id, body) => request('PATCH', `/api/gateway/keys/${id}`, body),
  gatewayDeleteKey: (id) => request('DELETE', `/api/gateway/keys/${id}`),
  gatewayRequests: (params = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== '' && v != null) q.set(k, v) })
    const qs = q.toString()
    return request('GET', `/api/gateway/requests${qs ? '?' + qs : ''}`)
  },
  gatewayTrace: (id) => request('GET', `/api/gateway/requests/${encodeURIComponent(id)}/trace`),
  gatewayHosts: () => request('GET', '/api/gateway/hosts'),
  gatewayStats: (params = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== '' && v != null) q.set(k, v) })
    const qs = q.toString()
    return request('GET', `/api/gateway/stats${qs ? '?' + qs : ''}`)
  },
  gatewaySetStrategy: (body) => request('PATCH', '/api/gateway/strategy', body),
  gatewaySetPrices: (body) => request('PATCH', '/api/gateway/prices', body),
  gatewaySetHostExcluded: (id, excluded) => request('PATCH', `/api/gateway/hosts/${encodeURIComponent(id)}`, { excluded }),
}
