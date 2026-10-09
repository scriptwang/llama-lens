<template>
  <!-- Element Plus 全局中文（日期面板星期/今天/确定、表格空态、对话框按钮等） -->
  <el-config-provider :locale="zhCn">
    <TerminalFrame>
      <!-- :key 按主机 id 强制重建：切换主机时重置 WS 流 / 历史 / 服务管理等全部组件状态 -->
      <router-view :key="$route.params.id" />
    </TerminalFrame>
  </el-config-provider>
</template>

<script setup>
import { watch } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import TerminalFrame from './components/TerminalFrame.vue'
import { totalSpeed } from './speed'

// 动态标题：有生成速度时，在浏览器标签页标题实时展示总 token 速度
// （全局监听，门户/详情页均生效；数据源见 BrandBar → speed.js）
const BASE_TITLE = 'LLMLens · LLM 推理服务实时监控'
watch(totalSpeed, (speed) => {
  document.title = speed > 0 ? `⚡ ${speed.toFixed(1)} tok/s · ${BASE_TITLE}` : BASE_TITLE
}, { immediate: true })
</script>
