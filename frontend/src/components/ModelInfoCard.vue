<template>
  <div class="model glass">
    <div class="panel-head">
      <span class="panel-title">模型</span>
      <span v-if="model.owned_by" class="mono faint small">{{ model.owned_by }}</span>
    </div>

    <template v-if="hasData">
      <div class="model-name-row">
        <span class="mname">{{ model.name || '—' }}</span>
        <span v-if="model.ftype" class="badge dim mono">{{ model.ftype }}</span>
      </div>
      <div class="kv"><span class="k">路径</span><span class="v mono">{{ model.path || '—' }}</span></div>
      <div class="grid2">
        <div class="kv"><span class="k">参数量</span><span class="v mono">{{ paramsText }}</span></div>
        <div class="kv"><span class="k">嵌入维度</span><span class="v mono">{{ fmtNum(model.n_embd) }}</span></div>
        <div class="kv"><span class="k">词表大小</span><span class="v mono">{{ fmtNum(model.n_vocab) }}</span></div>
        <div class="kv"><span class="k">vocab_type</span><span class="v mono">{{ model.vocab_type ?? '—' }}</span></div>
        <div class="kv"><span class="k">n_ctx</span><span class="v mono">{{ fmtNum(model.n_ctx) }}</span></div>
        <div class="kv"><span class="k">n_ctx_train</span><span class="v mono">{{ fmtNum(model.n_ctx_train) }}</span></div>
        <div class="kv"><span class="k">文件体积</span><span class="v mono">{{ sizeText }}</span></div>
        <div class="kv"><span class="k">mmproj</span><span class="v mono">{{ mmprojText }}</span></div>
        <div v-if="model.quantization" class="kv"><span class="k">量化</span><span class="v mono">{{ model.quantization }}</span></div>
        <div v-if="model.kv_cache_dtype" class="kv"><span class="k">KV 缓存</span><span class="v mono">{{ model.kv_cache_dtype }}</span></div>
        <div v-if="model.max_model_len" class="kv"><span class="k">最大上下文</span><span class="v mono">{{ fmtNum(model.max_model_len) }}</span></div>
        <div v-if="model.model_type" class="kv"><span class="k">model_type</span><span class="v mono">{{ model.model_type }}</span></div>
      </div>
      <div class="kv">
        <span class="k">模态</span>
        <span class="v">
          <span class="tag" :class="{ on: modalities.vision }">vision</span>
          <span class="tag" :class="{ on: modalities.video }">video</span>
          <span class="tag" :class="{ on: modalities.audio }">audio</span>
        </span>
      </div>
      <div v-if="capabilities.length" class="kv">
        <span class="k">能力</span>
        <span class="v mono small">{{ capabilities.join(', ') }}</span>
      </div>
    </template>
    <div v-else class="placeholder"><span class="icon">⌁</span>暂无模型数据</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtNum, fmtBytes, fmtParams } from '../utils'

const props = defineProps({
  model: { type: Object, default: () => ({}) }
})

const hasData = computed(() => !!(props.model.name || props.model.path))
const paramsText = computed(() => {
  const n = props.model.n_params
  if (!n) return '—'
  return `${fmtNum(n)} (${fmtParams(n)})`
})
const sizeText = computed(() => (props.model.file_size ? fmtBytes(props.model.file_size) : '—'))
const mmprojText = computed(() => {
  const p = props.model.mmproj_path
  if (!p) return '—'
  const s = props.model.mmproj_size ? ` · ${fmtBytes(props.model.mmproj_size)}` : ''
  return p.split('/').pop() + s
})
const modalities = computed(() => props.model.modalities || {})
const capabilities = computed(() => props.model.capabilities || [])
</script>

<style scoped>
.model { padding: 12px 16px; }
.panel-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
.panel-title { font-size: 11px; color: var(--text-dim); letter-spacing: 1px; }
.model-name-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.mname { font-size: 14px; font-weight: 600; color: var(--cyan); }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 18px; }
.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 8px;
  font-size: 10px;
  margin-right: 6px;
  border: 1px solid rgba(143, 163, 200, 0.25);
  color: var(--text-faint);
}
.tag.on { color: var(--green); border-color: rgba(0, 255, 157, 0.4); background: rgba(0, 255, 157, 0.07); }
</style>
