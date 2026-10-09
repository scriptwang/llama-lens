<template>
  <header class="brandbar">
    <div class="brand">
      <span class="logo">◉</span>
      <span class="name">LLMLens</span>
      <span class="sub">LLM 推理服务实时监控</span>
      <nav class="viewnav">
        <button :class="{ on: route.name === 'portal' }" @click="go('portal')">主机</button>
        <button :class="{ on: route.name === 'cluster' }" @click="go('cluster')">集群</button>
      </nav>
    </div>
    <div class="stats mono">
      <span>主机 <b>{{ hostsTotal }}</b></span>
      <span class="sep">·</span>
      <span>在线 <b class="lv-green">{{ onlineCount }}</b></span>
      <span v-if="totalSpeed > 0" class="sep">·</span>
      <span v-if="totalSpeed > 0">Token 速度 <b class="lv-cyan">{{ totalSpeed.toFixed(1) }} tok/s</b></span>
      <span class="conn" :class="connected ? 'ok' : 'bad'">{{ connected ? 'WS 实时' : '轮询中' }}</span>
      <button class="ctl-entry" @click="hostMgr = true">⚙ 主机管理</button>
      <ThemeSwitcher />
      <AccountMenu />
      <span class="clock-sep"></span>
      <LiveClock />
    </div>
  </header>
  <div class="ctl-scope">
    <HostManager v-model="hostMgr" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import LiveClock from './LiveClock.vue'
import ThemeSwitcher from './ThemeSwitcher.vue'
import AccountMenu from './AccountMenu.vue'
import HostManager from './HostManager.vue'
import { totalSpeed as globalSpeed } from '../speed'

const hostMgr = ref(false)
const router = useRouter()
const route = useRoute()
function go(name) { router.push(name === 'portal' ? '/' : '/cluster') }

const props = defineProps({
  hosts: { type: Array, default: () => [] },
  connected: { type: Boolean, default: false }
})

const hostsTotal = computed(() => props.hosts.length)
const onlineCount = computed(() => props.hosts.filter((h) => h.online).length)
const totalSpeed = computed(() => props.hosts.reduce((s, h) => s + (h.gen_speed_tps || 0), 0))

// 门户级聚合速度同步到全局：浏览器标签页标题（App.vue）与
// Terminal 窗口标题栏（TerminalFrame.vue）据此展示，任意视图均可见
watch(totalSpeed, (v) => { globalSpeed.value = v }, { immediate: true })
</script>

<style scoped>
.viewnav { display: flex; gap: 4px; margin-left: 18px; }
.viewnav button {
  background: transparent; color: var(--text-dim); border: 1px solid transparent;
  border-radius: 6px; font-size: 12px; padding: 3px 12px; cursor: pointer; font-family: inherit;
}
.viewnav button:hover { color: var(--text); }
.viewnav button.on { color: var(--cyan); border-color: var(--card-border); background: rgba(0, 229, 255, 0.08); }
.ctl-entry {
  background: rgba(16, 24, 40, 0.55);
  color: var(--text-dim);
  border: 1px solid var(--card-border);
  border-radius: 6px;
  font-size: 12px;
  padding: 4px 10px;
  cursor: pointer;
  font-family: inherit;
}
.ctl-entry:hover { color: var(--cyan); border-color: var(--card-border-hover); }
.brandbar {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  border-bottom: 1px solid rgba(0, 229, 255, 0.12);
  background: rgba(10, 14, 23, 0.6);
  backdrop-filter: blur(12px);
  position: sticky;
  top: var(--chrome-top, 0px);
  z-index: 20;
}
.brand { display: flex; align-items: baseline; gap: 10px; }
.logo {
  color: var(--cyan);
  font-size: 20px;
  text-shadow: 0 0 12px rgba(0, 229, 255, 0.8);
  align-self: center;
}
.name {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 1px;
  background: linear-gradient(90deg, #00e5ff, #00ff9d);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.sub { color: var(--text-faint); font-size: 11px; }
.stats { display: flex; align-items: center; gap: 10px; color: var(--text-dim); font-size: 12px; }
.stats b { color: var(--text); font-weight: 600; }
.sep { color: var(--text-faint); }
.lv-green { color: var(--green) !important; }
.lv-cyan { color: var(--cyan) !important; }
.conn { padding: 2px 8px; border-radius: 10px; font-size: 11px; }
.conn.ok { color: var(--green); border: 1px solid rgba(0, 255, 157, 0.35); }
.conn.bad { color: var(--amber); border: 1px solid rgba(255, 197, 61, 0.35); }
.clock-sep { width: 1px; height: 16px; background: rgba(143, 163, 200, 0.25); }
</style>
