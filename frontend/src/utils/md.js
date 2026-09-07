import { marked } from 'marked'
import DOMPurify from 'dompurify'

// LLM 输出 Markdown 渲染（GFM + 换行转 <br>），DOMPurify 消毒防 XSS
marked.setOptions({ gfm: true, breaks: true })

export function renderMd(text) {
  if (!text) return ''
  try {
    return DOMPurify.sanitize(marked.parse(text))
  } catch (e) {
    return text
  }
}
