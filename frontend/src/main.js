import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { ElLoading } from 'element-plus/es/components/loading/index.mjs'
import 'element-plus/dist/index.css'
// Element Plus 暗色变量（html.dark 下生效，随主题系统同步切换）
import 'element-plus/theme-chalk/dark/css-vars.css'
import {
  ArrowLeft, ArrowRight, ArrowUp, Back, Brush, Close, CopyDocument, Cpu, DataLine,
  Document, EditPen, FirstAidKit, Folder, FolderOpened, Grid, Lightning, Monitor, Moon, Odometer,
  Operation, Platform, Plus, Refresh, RefreshRight, Search, Setting, Sunny,
  SwitchButton, Timer, VideoPause, VideoPlay, Warning,
} from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import './theme/global.css'
import './theme/terminal.css'
import './theme/light.css'
import './theme/monokai.css'
import './theme/nord.css'
import './theme/dracula.css'
import './theme/synthwave.css'
import './theme/tokyonight.css'
import './theme/matrix.css'
import './styles/ctl-theme.css'
import { initTheme } from './theme'

initTheme()
const app = createApp(App)
app.use(createPinia())
app.use(router)
// v-loading 指令（按需引入模式下需手动注册）
app.directive('loading', ElLoading.directive)
const icons = {
  ArrowLeft, ArrowRight, ArrowUp, Back, Brush, Close, CopyDocument, Cpu, DataLine,
  Document, EditPen, FirstAidKit, Folder, FolderOpened, Grid, Lightning, Monitor, Moon, Odometer,
  Operation, Platform, Plus, Refresh, RefreshRight, Search, Setting, Sunny,
  SwitchButton, Timer, VideoPause, VideoPlay, Warning,
}
for (const [name, comp] of Object.entries(icons)) app.component(name, comp)
app.mount('#app')
