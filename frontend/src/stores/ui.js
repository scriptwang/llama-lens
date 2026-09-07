import { defineStore } from 'pinia'

const EDITOR_KEY = 'llama_editor_theme'

// 页面明暗由主题系统统一管理（theme/index.js 按 data-theme 同步 html.dark），
// 此处只保留 Monaco 编辑器主题偏好。
export const useUiStore = defineStore('ui', {
  state: () => ({
    editorThemeId: localStorage.getItem(EDITOR_KEY) || 'solarized-night',
  }),
  actions: {
    setEditorTheme(id) {
      this.editorThemeId = id
      localStorage.setItem(EDITOR_KEY, id)
    },
  },
})
