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

export const api = {
  hosts: () => getJson('/api/hosts'),
  overview: (id) => getJson(`/api/hosts/${encodeURIComponent(id)}/overview`),
  history: (id, window) => getJson(`/api/hosts/${encodeURIComponent(id)}/history?window=${window}`),
  events: (id, limit = 50) => getJson(`/api/hosts/${encodeURIComponent(id)}/events?limit=${limit}`),
  uiConfig: () => getJson('/api/ui')
}
