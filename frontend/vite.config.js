import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
// Element Plus 按组件子路径解析器：
// 官方 ElementPlusResolver 生成 `from: 'element-plus/es'` 全量入口，
// 库内部循环依赖使 rollup 摇树失效（实测 120/123 个组件全部进包）。
// 这里直接生成 per-component 子路径导入，只打包实际用到的组件。
// 注意：子路径必须带 /index.mjs（exports 映射 ./es/* -> ./es/*.mjs，目录形式无法解析）。
const EP_COMPONENT_DIR_ALIAS = {
  ElFormItem: 'form',
  ElOption: 'select',
  ElOptionGroup: 'select',
  ElRadioButton: 'radio',
  ElRadioGroup: 'radio',
  ElStep: 'steps',
  ElTableColumn: 'table',
  ElTabPane: 'tabs',
  ElDropdownMenu: 'dropdown',
  ElDropdownItem: 'dropdown',
}
function ElementPlusSubpathResolver() {
  return {
    type: 'component',
    resolve: (name) => {
      if (!name.startsWith('El')) return
      const dir =
        EP_COMPONENT_DIR_ALIAS[name] ||
        name.slice(2).replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase()
      return { name, from: `element-plus/es/components/${dir}/index.mjs` }
    },
  }
}

export default defineConfig({
  plugins: [
    vue(),
    // Element Plus 组件按需引入（样式仍用全量 CSS，避免逐组件 style 导入的复杂度）
    Components({
      resolvers: [ElementPlusSubpathResolver()],
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true }
    }
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('monaco-editor')) return // 保持 Monaco 独立懒加载 chunk
          if (id.includes('element-plus')) return 'ep'
          // echarts 仅被 TrendChart/GaugeCard 动态 import：独立 chunk，随图表懒加载，不进首屏 vendor
          if (id.includes('echarts')) return 'echarts'
          return 'vendor'
        },
      },
    },
  }
})
