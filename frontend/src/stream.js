import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api } from './api'

function wsUrl(path) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const token = localStorage.getItem('llama_token')
  const sep = path.includes('?') ? '&' : '?'
  return `${proto}://${window.location.host}${path}${token ? `${sep}token=${encodeURIComponent(token)}` : ''}`
}

/**
 * 单主机数据流：WS 为主，断线 3 次自动降级 1s 轮询。
 * mode: 'ws' | '1s' | '2s' | '5s' | 'paused'
 */
export function useHostStream(hostId) {
  const snapshot = ref(null)
  const connected = ref(false)
  const degraded = ref(false)
  const mode = ref('ws')

  let ws = null
  let pingTimer = null
  let pollTimer = null
  let reconnectTimer = null
  let failCount = 0
  let missedPongs = 0
  let closedByUser = false

  function stopTimers() {
    if (pingTimer) { clearInterval(pingTimer); pingTimer = null }
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  }

  function closeWs() {
    stopTimers()
    if (ws) {
      closedByUser = true
      try { ws.onclose = null; ws.close() } catch (e) { /* ignore */ }
      ws = null
    }
    connected.value = false
  }

  function startPoll(ms) {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = setInterval(async () => {
      try {
        snapshot.value = await api.overview(hostId)
        connected.value = true
      } catch (e) {
        connected.value = false
      }
    }, ms)
  }

  function startPing() {
    if (pingTimer) clearInterval(pingTimer)
    missedPongs = 0
    pingTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: 'ping' })) } catch (e) { /* ignore */ }
        missedPongs += 1
        if (missedPongs >= 3) { try { ws.close() } catch (e) { /* ignore */ } }
      }
    }, 10000)
  }

  function scheduleReconnect() {
    connected.value = false
    failCount += 1
    if (failCount >= 3) {
      degraded.value = true
      startPoll(1000) // 降级：1s 轮询
    } else {
      const delay = Math.min(1000 * 2 ** (failCount - 1), 4000)
      reconnectTimer = setTimeout(connectWs, delay)
    }
  }

  function connectWs() {
    closedByUser = false
    let sock
    try {
      sock = new WebSocket(wsUrl(`/ws/hosts/${encodeURIComponent(hostId)}`))
    } catch (e) {
      scheduleReconnect()
      return
    }
    ws = sock
    ws.onopen = () => {
      connected.value = true
      degraded.value = false
      failCount = 0
      startPing()
    }
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data && data.type === 'pong') {
          missedPongs = 0
          return
        }
        snapshot.value = data
      } catch (err) { /* ignore */ }
    }
    ws.onclose = () => {
      if (pingTimer) { clearInterval(pingTimer); pingTimer = null }
      if (!closedByUser) scheduleReconnect()
    }
    ws.onerror = () => { try { ws.close() } catch (e) { /* ignore */ } }
  }

  function setMode(m) {
    mode.value = m
    closeWs()
    if (m === 'ws') {
      failCount = 0
      degraded.value = false
      connectWs()
    } else if (m === '1s') startPoll(1000)
    else if (m === '2s') startPoll(2000)
    else if (m === '5s') startPoll(5000)
    // paused：停止一切请求，保留最后数据
  }

  onMounted(() => connectWs())
  onBeforeUnmount(() => { closeWs(); stopTimers() })

  return { snapshot, connected, degraded, mode, setMode }
}

/** 门户数据流：WS /ws/portal，断线降级 2s 轮询。 */
export function usePortalStream() {
  const hosts = ref([])
  const connected = ref(false)

  let ws = null
  let pollTimer = null
  let pingTimer = null
  let failCount = 0
  let missedPongs = 0
  let closedByUser = false

  function startPoll() {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = setInterval(async () => {
      try {
        hosts.value = await api.hosts()
        connected.value = true
      } catch (e) {
        connected.value = false
      }
    }, 2000)
  }

  function startPing() {
    if (pingTimer) clearInterval(pingTimer)
    missedPongs = 0
    pingTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: 'ping' })) } catch (e) { /* ignore */ }
        missedPongs += 1
        if (missedPongs >= 3) { try { ws.close() } catch (e) { /* ignore */ } }
      }
    }, 10000)
  }

  function connect() {
    closedByUser = false
    let sock
    try {
      sock = new WebSocket(wsUrl('/ws/portal'))
    } catch (e) {
      startPoll()
      return
    }
    ws = sock
    ws.onopen = () => {
      connected.value = true
      failCount = 0
      startPing()
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    }
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data && data.type === 'pong') { missedPongs = 0; return }
        hosts.value = data
      } catch (err) { /* ignore */ }
    }
    ws.onclose = () => {
      if (pingTimer) { clearInterval(pingTimer); pingTimer = null }
      connected.value = false
      if (!closedByUser) {
        failCount += 1
        if (failCount >= 3) startPoll()
        else setTimeout(connect, Math.min(1000 * 2 ** (failCount - 1), 4000))
      }
    }
    ws.onerror = () => { try { ws.close() } catch (e) { /* ignore */ } }
  }

  onMounted(() => connect())
  onBeforeUnmount(() => {
    closedByUser = true
    if (pollTimer) clearInterval(pollTimer)
    if (pingTimer) clearInterval(pingTimer)
    if (ws) { try { ws.onclose = null; ws.close() } catch (e) { /* ignore */ } }
  })

  return { hosts, connected }
}
