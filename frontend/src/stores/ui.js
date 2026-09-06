import { defineStore } from 'pinia'

const PAGE_KEY = 'llama_page_theme'
const EDITOR_KEY = 'llama_editor_theme'

function applyPageTheme(theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

export const useUiStore = defineStore('ui', {
  state: () => ({
    pageTheme: localStorage.getItem(PAGE_KEY) || 'dark',
    editorThemeId: localStorage.getItem(EDITOR_KEY) || 'solarized-night',
  }),
  actions: {
    setPageTheme(theme) {
      this.pageTheme = theme
      localStorage.setItem(PAGE_KEY, theme)
      applyPageTheme(theme)
    },
    togglePageTheme() {
      this.setPageTheme(this.pageTheme === 'dark' ? 'light' : 'dark')
    },
    setEditorTheme(id) {
      this.editorThemeId = id
      localStorage.setItem(EDITOR_KEY, id)
    },
  },
})

applyPageTheme(localStorage.getItem(PAGE_KEY) || 'dark')
