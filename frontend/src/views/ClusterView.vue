<template>
  <div class="cluster">
    <BrandBar :hosts="hosts" :connected="connected" />

    <!-- 集群视角板块：总览 / 接入 / API Keys / 观测（网关是集群视角的一部分，不是全部） -->
    <nav class="tabs">
      <button v-for="t in tabs" :key="t.key" :class="{ on: tab === t.key }" @click="tab = t.key">{{ t.label }}</button>
    </nav>

    <main class="body">
      <!-- ============ 总览 ============ -->
      <div v-show="tab === 'overview'" class="tab-pane">
        <!-- 网关概览 -->
        <div class="stat-cards ov-cards">
          <div class="stat-card">
            <span class="sc-k">网关地址</span>
            <b class="sc-v mono sc-url">{{ status ? shortBase(status.base_url) : '—' }}</b>
            <span class="sc-foot" v-if="status"><button class="mini" @click="copy(status.base_url)">复制完整地址</button></span>
          </div>
          <div class="stat-card">
            <span class="sc-k">在线主机</span>
            <b class="sc-v">{{ status ? status.hosts_online : '—' }}<span class="sc-sub">/ {{ status ? status.hosts_total : '—' }}</span></b>
            <span class="sc-foot dim">参与网关路由</span>
          </div>
          <div class="stat-card">
            <span class="sc-k">兜底主机</span>
            <b class="sc-v sc-host">{{ status ? (status.default_host || '—') : '—' }}</b>
            <span class="sc-foot dim">无匹配时落此</span>
          </div>
          <div class="stat-card">
            <span class="sc-k">审计保留</span>
            <b class="sc-v">{{ status ? status.audit_days : '—' }}<span class="sc-sub"> 天</span></b>
            <span class="sc-foot dim">链路 {{ status ? status.trace_days : '—' }} 天</span>
          </div>
          <div class="stat-card" v-if="status && status.db_size_mb">
            <span class="sc-k">存储占用</span>
            <b class="sc-v">{{ status.db_size_mb }}<span class="sc-sub"> MB</span></b>
            <span class="sc-foot dim">gateway.db</span>
          </div>
        </div>

        <!-- 主机列表 -->
        <section class="glass card">
          <div class="card-head">
            <h2>主机</h2>
            <span class="dim hint-inline">{{ hosts.length }} 台 · 在线 {{ onlineHosts }} 台</span>
          </div>
          <div class="host-list">
            <div v-for="h in hosts" :key="h.id" class="hl-row">
              <span class="hl-name"><i class="dot" :class="h.gateway_excluded ? 'warn' : (h.online ? 'ok' : 'off')"></i>{{ h.name }}</span>
              <span class="badge" :class="hostBadge(h)">{{ hostStateText(h) }}</span>
              <code class="mono hl-model">{{ h.model_name || '未加载模型' }}</code>
              <span class="hl-speed">{{ h.gen_speed_tps ? h.gen_speed_tps.toFixed(1) + ' t/s' : '—' }}</span>
              <span class="hl-meta">
                <span v-if="h.cpu_pct != null">CPU {{ Math.round(h.cpu_pct) }}%</span>
                <span v-if="h.mem_pct != null">内存 {{ h.mem_pct }}%</span>
                <span v-if="gpuPct(h) != null">GPU {{ gpuPct(h) }}%</span>
                <span v-if="h.gateway_excluded" class="warn">已排除出网关</span>
              </span>
            </div>
            <div v-if="!hosts.length" class="hl-empty">暂无主机</div>
          </div>
        </section>

        <!-- 模型总览 + 路由策略（两列） -->
        <div class="ov-cols">
          <section class="glass card">
            <div class="card-head">
              <h2>模型总览</h2>
              <span class="dim hint-inline">按模型文件聚合</span>
            </div>
            <p v-if="!modelGroups.length" class="dim">当前无已加载模型</p>
            <div v-for="g in modelGroups" :key="g.model" class="model-group">
              <div class="mg-model">
                <span class="mg-icon">◆</span>
                <code class="mono mg-name">{{ g.model }}</code>
                <span class="mg-count">{{ g.hosts.length }} 台</span>
              </div>
              <div class="mg-hosts">
                <span v-for="h in g.hosts" :key="h.id" class="chip" :class="{ off: !h.online && !h.gateway_excluded, warn: h.gateway_excluded }">
                  <i class="dot" :class="h.gateway_excluded ? 'warn' : (h.online ? 'ok' : 'off')"></i>
                  {{ h.name }}
                  <em v-if="h.gateway_excluded">已排除</em>
                </span>
              </div>
            </div>
          </section>

          <section class="glass card">
            <div class="card-head">
              <h2>路由策略</h2>
              <button class="mini" @click="tab = 'access'">去调整</button>
            </div>
            <div class="strat-list">
              <div class="strat-item" :class="{ on: status && status.strategy.affinity }">
                <span class="strat-dot"></span>
                <div class="strat-txt"><b>档1 模型亲和</b><span class="dim">按模型找承载主机</span></div>
                <span class="strat-state">{{ status && status.strategy.affinity ? '开' : '关' }}</span>
              </div>
              <div class="strat-item" :class="{ on: status && status.strategy.same_model_lb }">
                <span class="strat-dot"></span>
                <div class="strat-txt"><b>档2 同模型 LB</b><span class="dim">同模型多机按速度分流</span></div>
                <span class="strat-state">{{ status && status.strategy.same_model_lb ? '开' : '关' }}</span>
              </div>
              <div class="strat-item" :class="{ on: status && status.strategy.cross_model_fallback }">
                <span class="strat-dot"></span>
                <div class="strat-txt"><b>档3 跨模型降级</b><span class="dim">不可用时按规则降级</span></div>
                <span class="strat-state">{{ status && status.strategy.cross_model_fallback ? '开' : '关' }}</span>
              </div>
            </div>
          </section>
        </div>
      </div>

      <!-- ============ 接入 ============ -->
      <div v-show="tab === 'access'" class="tab-pane">
        <AccessTab :hosts="hosts" :status="status" :keys="keys"
          @refresh="load" @goto-keys="tab = 'keys'" />
      </div>

      <!-- ============ 测试 ============ -->
      <div v-show="tab === 'test'" class="tab-pane">
        <GatewayTestTab :keys="keys" @trace="openTraceById" />
      </div>

      <!-- ============ API Keys ============ -->
      <div v-show="tab === 'keys'" class="tab-pane">
        <div class="stat-cards">
          <div class="stat-card">
            <span class="sc-k">Key 总数</span>
            <b class="sc-v">{{ keys.length }}</b>
            <span class="sc-foot dim">启用 {{ enabledKeys }} 个</span>
          </div>
          <div class="stat-card">
            <span class="sc-k">累计 Tokens</span>
            <b class="sc-v">{{ fmtNum(totalTokens) }}</b>
            <span class="sc-foot dim">全部 Key 合计</span>
          </div>
          <div class="stat-card">
            <span class="sc-k">累计费用</span>
            <b class="sc-v">{{ totalCost > 0 ? '¥' + totalCost.toFixed(4) : '—' }}</b>
            <span class="sc-foot dim">按模型单价计费</span>
          </div>
        </div>
        <section class="glass card">
          <div class="card-head">
            <h2>API Keys</h2>
            <button class="btn" @click="openCreate">+ 新建</button>
          </div>
          <table class="keys">
            <thead>
              <tr><th>名称</th><th>Key</th><th>额度(tokens)</th><th>已用</th><th>剩余</th><th>费用(已用/额度)</th><th>允许模型</th><th>有效期</th><th>状态</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="k in keys" :key="k.id" class="clickable" @click="openKeyDetail(k)">
                <td>{{ k.name || '—' }}</td>
                <td><code class="mono">{{ mask(k.key) }}</code>
                  <button class="mini" @click.stop="copy(k.key)">复制</button></td>
                <td>{{ k.quota_tokens > 0 ? fmtNum(k.quota_tokens) : '不限' }}</td>
                <td>{{ fmtNum(k.used_tokens) }}</td>
                <td>{{ k.quota_tokens > 0 ? fmtNum(Math.max(0, k.quota_tokens - k.used_tokens)) : '—' }}</td>
                <td>{{ fmtCost(k.used_cost) }} / {{ k.quota_cost > 0 ? '¥' + k.quota_cost : '不限' }}</td>
                <td class="dim">{{ k.allowed_models || '不限' }}</td>
                <td class="dim">{{ k.expires_at ? k.expires_at.slice(0, 10) : '永久' }}</td>
                <td><span :class="k.enabled ? 'ok' : 'off'">{{ k.enabled ? '启用' : '禁用' }}</span></td>
                <td class="ops" @click.stop>
                  <button class="mini" @click="openEdit(k)">编辑</button>
                  <button class="mini" @click="toggle(k)">{{ k.enabled ? '禁用' : '启用' }}</button>
                  <button class="mini danger" @click="remove(k)">删除</button>
                </td>
              </tr>
              <tr v-if="!keys.length"><td colspan="10" class="empty">暂无 API key，点「新建」创建</td></tr>
            </tbody>
          </table>
          <p class="dim hint">点击行查看该 key 的用量趋势与最近请求</p>
        </section>
      </div>

      <!-- ============ 观测 ============ -->
      <div v-show="tab === 'observe'" class="tab-pane">
        <section class="glass card">
          <div class="card-head">
            <h2>成本统计（近 {{ statDays }} 天）</h2>
            <div class="filters">
              <select v-model="statDays" class="sel" @change="loadStats">
                <option :value="1">1 天</option>
                <option :value="7">7 天</option>
                <option :value="30">30 天</option>
              </select>
            </div>
          </div>
          <div class="stat-cards">
            <div class="stat-card"><span class="sc-k">请求</span><b class="sc-v">{{ fmtNum(statSummary.requests) }}</b></div>
            <div class="stat-card"><span class="sc-k">Tokens</span><b class="sc-v">{{ fmtNum(statSummary.tokens) }}</b></div>
            <div class="stat-card"><span class="sc-k">费用</span><b class="sc-v">{{ statSummary.cost > 0 ? '¥' + statSummary.cost.toFixed(4) : '—' }}</b></div>
            <div class="stat-card"><span class="sc-k">降级</span><b class="sc-v" :class="statSummary.degraded ? 'warn' : ''">{{ statSummary.degraded }}</b></div>
            <div class="stat-card"><span class="sc-k">错误</span><b class="sc-v" :class="statSummary.errors ? 'err' : ''">{{ statSummary.errors }}</b></div>
          </div>
          <div class="stat-cols">
            <div>
              <p class="ex-title">按 Key</p>
              <table class="keys mini-tbl">
                <thead><tr><th>Key</th><th>请求</th><th>Tokens</th><th>费用</th><th>降级</th><th>错误</th></tr></thead>
                <tbody>
                  <tr v-for="s in statsByKey" :key="s.group">
                    <td>{{ s.name }}</td><td>{{ s.requests }}</td><td>{{ fmtNum(s.tokens) }}</td>
                    <td>{{ s.cost > 0 ? '¥' + s.cost.toFixed(4) : '—' }}</td><td>{{ s.degraded }}</td><td>{{ s.errors }}</td>
                  </tr>
                  <tr v-if="!statsByKey.length"><td colspan="6" class="empty">暂无数据</td></tr>
                </tbody>
              </table>
            </div>
            <div>
              <p class="ex-title">按模型</p>
              <table class="keys mini-tbl">
                <thead><tr><th>模型</th><th>请求</th><th>Tokens</th><th>费用</th><th>降级</th><th>错误</th></tr></thead>
                <tbody>
                  <tr v-for="s in statsByModel" :key="s.group">
                    <td><code class="mono">{{ s.group || '—' }}</code></td><td>{{ s.requests }}</td><td>{{ fmtNum(s.tokens) }}</td>
                    <td>{{ s.cost > 0 ? '¥' + s.cost.toFixed(4) : '—' }}</td><td>{{ s.degraded }}</td><td>{{ s.errors }}</td>
                  </tr>
                  <tr v-if="!statsByModel.length"><td colspan="6" class="empty">暂无数据</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section class="glass card">
          <div class="card-head">
            <h2>请求审计</h2>
            <div class="filters">
              <select v-model="f.key" class="sel" @change="loadRequests">
                <option value="">全部 Key</option>
                <option v-for="k in keys" :key="k.id" :value="k.id">{{ k.name || k.id }}</option>
              </select>
              <select v-model="f.host" class="sel" @change="loadRequests">
                <option value="">全部主机</option>
                <option v-for="h in hosts" :key="h.id" :value="h.id">{{ h.name }}</option>
              </select>
              <select v-model="f.degraded" class="sel" @change="loadRequests">
                <option value="">降级:全部</option>
                <option value="1">仅降级</option>
                <option value="0">仅正常</option>
              </select>
              <select v-model="f.status" class="sel" @change="loadRequests">
                <option value="">状态:全部</option>
                <option value="200">2xx</option>
                <option value="401">401</option>
                <option value="429">429</option>
                <option value="500">5xx</option>
              </select>
              <input v-model="f.model" class="sel" placeholder="模型" @change="loadRequests" />
              <select v-model="f.range" class="sel" @change="loadRequests">
                <option value="">全部时间</option>
                <option value="1">近 1 小时</option>
                <option value="24">近 24 小时</option>
                <option value="168">近 7 天</option>
                <option value="720">近 30 天</option>
              </select>
              <button class="mini" @click="loadRequests">刷新</button>
            </div>
          </div>
          <table class="keys reqs">
            <thead>
              <tr><th>时间</th><th>Key</th><th>模型</th><th>目标</th><th>状态</th><th>Tokens</th><th>费用</th><th>TTFT</th><th>总耗时</th><th>重试</th><th>降级</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="r in requests" :key="r.id" class="clickable" @click="openTrace(r)">
                <td class="nowrap">{{ fmtTs(r.ts) }}</td>
                <td>{{ keyName(r.api_key_id) }}</td>
                <td><code class="mono">{{ r.req_model }}</code></td>
                <td>{{ r.target_host || '—' }}</td>
                <td :class="r.status >= 200 && r.status < 300 ? 'ok' : 'err'">{{ r.status || '—' }}</td>
                <td>{{ r.prompt_tokens + r.completion_tokens }}</td>
                <td>{{ r.cost > 0 ? '¥' + r.cost.toFixed(4) : '—' }}</td>
                <td>{{ r.ttft_ms != null ? r.ttft_ms.toFixed(0) + 'ms' : '—' }}</td>
                <td>{{ r.total_ms != null ? r.total_ms.toFixed(0) + 'ms' : '—' }}</td>
                <td>{{ r.retries || 0 }}</td>
                <td>{{ r.degraded ? '是' : '' }}</td>
                <td class="ops"><button class="mini" @click.stop="openTrace(r)">链路</button></td>
              </tr>
              <tr v-if="!requests.length"><td colspan="12" class="empty">暂无请求记录</td></tr>
            </tbody>
          </table>
          <p class="dim hint">所有经网关的请求（含 Codex 等 API 调用）都会记录在此；点「链路」查看内容详情与 span 时间线。
            审计数据保留 {{ status ? status.audit_days : '—' }} 天（更早的已自动清理）</p>
        </section>
      </div>
    </main>

    <!-- 新建 / 编辑 Key 对话框 -->
    <el-dialog v-model="showKeyDialog" :title="editing ? '编辑 API Key' : '新建 API Key'" width="460px">
      <el-form label-width="110px">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="如 Codex-ai" /></el-form-item>
        <el-form-item label="额度(tokens)"><el-input-number v-model="form.quota_tokens" :min="0" /></el-form-item>
        <el-form-item label="费用额度(元)"><el-input-number v-model="form.quota_cost" :min="0" :precision="2" /></el-form-item>
        <el-form-item label="允许模型"><el-input v-model="form.allowed_models" placeholder="逗号分隔，空=不限（如 qwen3.8,llama，支持部分匹配）" /></el-form-item>
        <el-form-item label="有效期"><el-date-picker v-model="form.expires_at" type="date" value-format="YYYY-MM-DD"
          placeholder="空 = 永久" clearable style="width: 100%" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showKeyDialog = false">取消</el-button>
        <el-button type="primary" @click="saveKey">{{ editing ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>

    <!-- Key 详情抽屉 -->
    <el-drawer v-model="showKeyDetail" :title="keyDetail ? ('Key 详情：' + (keyDetail.name || keyDetail.id)) : 'Key 详情'" size="560px">
      <div v-if="keyDetail" class="kd">
        <div class="stat-cards">
          <div class="stat-card"><span class="sc-k">已用 Tokens</span><b class="sc-v">{{ fmtNum(keyDetail.used_tokens) }}</b></div>
          <div class="stat-card"><span class="sc-k">额度</span><b class="sc-v">{{ keyDetail.quota_tokens > 0 ? fmtNum(keyDetail.quota_tokens) : '不限' }}</b></div>
          <div class="stat-card"><span class="sc-k">已用费用</span><b class="sc-v">{{ fmtCost(keyDetail.used_cost) }}</b></div>
          <div class="stat-card"><span class="sc-k">费用额度</span><b class="sc-v">{{ keyDetail.quota_cost > 0 ? '¥' + keyDetail.quota_cost : '不限' }}</b></div>
        </div>
        <div class="kd-sec">
          <p class="ex-title">近 {{ keyTrendDays }} 天用量（按天）</p>
          <div v-if="keyTrend.length" class="trend">
            <div v-for="d in keyTrend" :key="d.day" class="trend-row">
              <span class="trend-day">{{ d.day.slice(5) }}</span>
              <div class="trend-track"><i :style="{ width: trendWidth(d.tokens) }"></i></div>
              <span class="trend-val">{{ fmtNum(d.tokens) }} tok · {{ d.requests }} 次</span>
            </div>
          </div>
          <p v-else class="dim">近 {{ keyTrendDays }} 天无请求</p>
        </div>
        <div class="kd-sec">
          <p class="ex-title">最近请求</p>
          <table class="keys mini-tbl">
            <thead><tr><th>时间</th><th>模型</th><th>目标</th><th>状态</th><th>Tokens</th><th>降级</th><th></th></tr></thead>
            <tbody>
              <tr v-for="r in keyReqs" :key="r.id" class="clickable" @click="openTrace(r)">
                <td class="nowrap">{{ fmtTs(r.ts) }}</td>
                <td><code class="mono">{{ r.req_model }}</code></td>
                <td>{{ r.target_host || '—' }}</td>
                <td :class="r.status >= 200 && r.status < 300 ? 'ok' : 'err'">{{ r.status || '—' }}</td>
                <td>{{ r.prompt_tokens + r.completion_tokens }}</td>
                <td>{{ r.degraded ? '是' : '' }}</td>
                <td class="ops"><button class="mini" @click.stop="openTrace(r)">链路</button></td>
              </tr>
              <tr v-if="!keyReqs.length"><td colspan="7" class="empty">暂无请求</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </el-drawer>

    <!-- 请求链路抽屉（旁路观测：内容详情 + span 瀑布） -->
    <el-drawer v-model="showTrace" title="请求链路" size="680px">
      <div v-if="traceReq" class="trace">
        <p class="row">请求 <code class="mono">{{ traceReq.id }}</code>
          <span v-if="traceReq.ts" class="dim">· {{ fmtTs(traceReq.ts) }}</span></p>
        <p class="row">模型 <code class="mono">{{ traceReq.req_model || '—' }}</code> → 目标 {{ traceReq.target_host || '—' }}（实际 {{ traceReq.served_by || '—' }}）</p>
        <p class="row">状态 <b :class="traceReq.status >= 200 && traceReq.status < 300 ? 'ok' : 'err'">{{ traceReq.status || '—' }}</b>
          · Tokens {{ (traceReq.prompt_tokens || 0) + (traceReq.completion_tokens || 0) }}（{{ traceReq.prompt_tokens || 0 }} + {{ traceReq.completion_tokens || 0 }}）
          · 费用 {{ fmtCost(traceReq.cost) }}</p>
        <p class="row dim">路径 {{ traceReq.path || '—' }} · Key {{ keyName(traceReq.api_key_id) }}
          · 首字 {{ traceReq.ttft_ms != null ? Math.round(traceReq.ttft_ms) + ' ms' : '—' }}
          · 总耗时 {{ traceReq.total_ms != null ? (traceReq.total_ms / 1000).toFixed(2) + ' s' : '—' }}
          · 重试 {{ traceReq.retries || 0 }}</p>
        <p v-if="traceReq.degraded" class="row warn-text">降级：{{ traceReq.served_by }}（served_by 标注）</p>
        <p v-if="traceReq.error" class="row err-text">错误：{{ traceReq.error }}</p>

        <!-- 内容详情（请求/响应预览，网关旁路捕获） -->
        <div v-if="traceReq.req_preview || traceReq.resp_preview" class="td-sec">
          <p class="ex-title">内容详情</p>
          <div v-if="traceReq.req_preview" class="td-block">
            <span class="td-k">请求<button class="mini" @click="copy(traceReq.req_preview)">复制</button></span>
            <pre class="mono td-pre">{{ traceReq.req_preview }}</pre>
          </div>
          <div v-if="traceReq.resp_preview" class="td-block">
            <span class="td-k">响应<button class="mini" @click="copy(traceReq.resp_preview)">复制</button></span>
            <pre class="mono td-pre">{{ traceReq.resp_preview }}</pre>
          </div>
        </div>

        <!-- span 瀑布（中文名） -->
        <div v-if="traceSpans.length" class="wf">
          <p class="ex-title">链路时间线</p>
          <div class="wf-axis">
            <span>0ms</span><span>{{ Math.round(maxSpanEnd) }}ms</span>
          </div>
          <div v-for="(sp, i) in traceSpans" :key="i" class="wf-row">
            <div class="wf-name">{{ spanName(sp.name) }}</div>
            <div class="wf-track">
              <div class="wf-bar" :class="barClass(sp)"
                   :style="{ left: (sp.start_ms / maxSpanEnd * 100) + '%', width: Math.max(0.8, (sp.end_ms - sp.start_ms) / maxSpanEnd * 100) + '%' }"></div>
            </div>
            <div class="wf-time">{{ sp.start_ms.toFixed(0) }}→{{ sp.end_ms.toFixed(0) }}ms</div>
            <div v-if="sp.meta" class="wf-meta-lines">
              <div v-for="(kv, j) in metaLines(sp.meta)" :key="j" class="wf-meta-line">
                <span class="wk">{{ kv.k }}</span><span class="wv">{{ kv.v }}</span>
              </div>
            </div>
          </div>
          <div class="wf-legend">
            <span><i class="lg gw"></i>网关</span><span><i class="lg up"></i>上游</span>
            <span><i class="lg retry"></i>重试</span><span><i class="lg deg"></i>降级</span>
          </div>
        </div>
        <p v-else class="dim">无 span 记录</p>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import BrandBar from '../components/BrandBar.vue'
import GatewayTestTab from '../components/GatewayTestTab.vue'
import AccessTab from '../components/AccessTab.vue'
import { usePortalStream } from '../stream'
import { api } from '../api'
import { fmtNum } from '../utils'

const tabs = [
  { key: 'overview', label: '总览' },
  { key: 'access', label: '接入' },
  { key: 'test', label: '测试' },
  { key: 'keys', label: 'API Keys' },
  { key: 'observe', label: '观测' },
]
const tab = ref('overview')

const { hosts, connected } = usePortalStream()
const status = ref(null)
const keys = ref([])
const showKeyDialog = ref(false)
const editing = ref(null)
const form = reactive({ name: '', quota_tokens: 0, quota_cost: 0, allowed_models: '', expires_at: '' })

// 模型总览：按模型聚合主机
const modelGroups = computed(() => {
  const map = new Map()
  for (const h of hosts.value) {
    if (!h.model_name) continue
    if (!map.has(h.model_name)) map.set(h.model_name, [])
    map.get(h.model_name).push(h)
  }
  return Array.from(map.entries()).map(([model, hs]) => ({ model, hosts: hs }))
})

function hostStateText(h) {
  if (h.gateway_excluded) return '已排除'
  return h.online ? '服务中' : '已摘除'
}
function hostBadge(h) {
  if (h.gateway_excluded) return 'warn'
  return h.online ? 'ok' : 'dim'
}
function shortBase(url) {
  return (url || '').replace(/^https?:\/\//, '')
}
const onlineHosts = computed(() => hosts.value.filter((h) => h.online).length)
function gpuPct(h) {
  const g = (h.gpus || []).map((x) => x.util_pct).filter((v) => v != null)
  return g.length ? Math.round(g.reduce((a, b) => a + b, 0) / g.length) : null
}
// Key 汇总
const enabledKeys = computed(() => keys.value.filter((k) => k.enabled).length)
const totalTokens = computed(() => keys.value.reduce((s, k) => s + (k.used_tokens || 0), 0))
const totalCost = computed(() => keys.value.reduce((s, k) => s + (k.used_cost || 0), 0))

// ---- 观测：成本统计 ----
const statDays = ref(30)
const statsByKey = ref([])
const statsByModel = ref([])
const statSummary = reactive({ requests: 0, tokens: 0, cost: 0, degraded: 0, errors: 0 })
let statsLoaded = false
async function loadStats() {
  try {
    const [byKey, byModel] = await Promise.all([
      api.gatewayStats({ group_by: 'key', days: statDays.value }),
      api.gatewayStats({ group_by: 'model', days: statDays.value }),
    ])
    statsByKey.value = byKey.stats || []
    statsByModel.value = byModel.stats || []
    Object.assign(statSummary, { requests: 0, tokens: 0, cost: 0, degraded: 0, errors: 0 })
    for (const s of statsByKey.value) {
      statSummary.requests += s.requests
      statSummary.tokens += s.tokens
      statSummary.cost += s.cost
      statSummary.degraded += s.degraded
      statSummary.errors += s.errors
    }
    statsLoaded = true
  } catch (e) {
    ElMessage.error('加载统计失败：' + e.message)
  }
}

// ---- 观测：审计列表 ----
const requests = ref([])
const f = reactive({ key: '', host: '', degraded: '', status: '', model: '', range: '' })
let reqLoaded = false
async function loadRequests() {
  try {
    const params = { limit: 200, api_key_id: f.key, target_host: f.host, degraded: f.degraded, model: f.model }
    if (f.status !== '') params.status = f.status
    if (f.range !== '') params.since = Date.now() / 1000 - Number(f.range) * 3600
    const d = await api.gatewayRequests(params)
    requests.value = d.requests
  } catch (e) {
    ElMessage.error('加载请求记录失败：' + e.message)
  }
}

// ---- 链路瀑布图 ----
const showTrace = ref(false)
const traceReq = ref(null)
const traceSpans = ref([])
const SPAN_NAMES = {
  received: '接收 (received)', auth: '鉴权 (auth)', route: '路由 (route)',
  normalize: '规范化 (normalize)', forward: '转发 (forward)',
  ttft: '首字 (ttft)', done: '完成 (done)', retry: '重试 (retry)',
}
const META_KEYS = {
  path: '路径', model: '模型', ip: '来源IP', api_key: 'Key', ok: '结果',
  error: '错误', host: '主机', upstream: '上游', explicit: '显式指定',
  new_model: '实际模型', degraded: '降级原因', status: '状态码',
  tokens: 'Tokens', ms: '耗时(ms)', attempt: '重试次数', wait: '等待(s)',
  changes: '变更',
}
function spanName(n) { return SPAN_NAMES[n] || n }
function metaLines(meta) {
  try {
    const obj = JSON.parse(meta)
    return Object.entries(obj).map(([k, v]) => ({
      k: META_KEYS[k] || k,
      v: typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v),
    }))
  } catch { return [{ k: '', v: meta }] }
}
async function openTrace(r) {
  traceReq.value = { ...r }
  traceSpans.value = []
  showTrace.value = true
  try {
    const d = await api.gatewayTrace(r.id)
    traceSpans.value = d.spans || []
    if (d.request) traceReq.value = { ...r, ...d.request }
  } catch (e) {
    ElMessage.error('加载链路失败：' + e.message)
  }
}
// 测试页下钻：审计行/spans 异步落库，未落库时先用测试页快照，落库后自动补全
async function openTraceById(payload) {
  const id = payload.id
  traceReq.value = { cost: 0, ...payload.partial }
  traceSpans.value = []
  showTrace.value = true
  for (let i = 0; i < 4; i++) {
    try {
      const d = await api.gatewayTrace(id)
      if (d.request) traceReq.value = d.request
      if (d.spans && d.spans.length) { traceSpans.value = d.spans; return }
    } catch { /* 重试 */ }
    await new Promise((r) => setTimeout(r, 1200))
  }
}
const maxSpanEnd = computed(() => Math.max(1, traceSpans.value.reduce((m, s) => Math.max(m, s.end_ms), 1)))
function barClass(sp) {
  if (sp.name === 'retry') return 'retry'
  if (sp.name === 'route' && sp.meta && sp.meta.includes('"degraded": "')) return 'deg'
  if (sp.name === 'forward') return 'up'
  return 'gw'
}
function prettyMeta(meta) {
  try { return JSON.stringify(JSON.parse(meta), null, 2) } catch { return meta }
}

// ---- Key 详情 ----
const showKeyDetail = ref(false)
const keyDetail = ref(null)
const keyReqs = ref([])
const keyTrend = ref([])
async function openKeyDetail(k) {
  keyDetail.value = k
  keyReqs.value = []
  keyTrend.value = []
  showKeyDetail.value = true
  try {
    const [rq, st30] = await Promise.all([
      api.gatewayRequests({ limit: 50, api_key_id: k.id }),
      api.gatewayStats({ group_by: 'day', days: keyTrendDays.value, api_key_id: k.id }),
    ])
    keyReqs.value = rq.requests || []
    keyTrend.value = (st30.stats || []).slice().reverse()
  } catch (e) {
    ElMessage.error('加载 key 详情失败：' + e.message)
  }
}
const keyTrendDays = computed(() => Math.min(30, (status.value && status.value.audit_days) || 30))
const maxKeyTokens = computed(() => Math.max(1, keyTrend.value.reduce((m, d) => Math.max(m, d.tokens), 1)))
function trendWidth(tokens) { return Math.max(1, tokens / maxKeyTokens.value * 100) + '%' }

// ---- 工具 ----
function keyName(id) {
  const k = keys.value.find((x) => x.id === id)
  return k ? (k.name || k.id) : (id || '—')
}
function fmtTs(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}
function fmtCost(c) { return c > 0 ? '¥' + Number(c).toFixed(4) : '—' }
function mask(k) { return k && k.length > 12 ? k.slice(0, 6) + '…' + k.slice(-4) : k }

// 剪贴板：非 HTTPS 环境（http://dev.lan）clipboard API 不可用，回退 execCommand（同 PlaygroundTab）
function copy(t) {
  const doCopy = () => {
    const ta = document.createElement('textarea')
    ta.value = t
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    let okc = false
    try { okc = document.execCommand('copy') } catch { okc = false }
    document.body.removeChild(ta)
    return okc
  }
  const run = () => {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(t)
    }
    return new Promise((resolve, reject) => {
      if (doCopy()) resolve()
      else reject(new Error('copy failed'))
    })
  }
  run().then(() => ElMessage.success('已复制'))
    .catch(() => ElMessage.warning('复制失败，请手动选择'))
}

// ---- 网关 / keys ----
async function load() {
  try {
    status.value = await api.gatewayStatus()
    keys.value = (await api.gatewayKeys()).keys
  } catch (e) {
    ElMessage.error('加载网关信息失败：' + e.message)
  }
}
function openCreate() {
  editing.value = null
  form.name = ''; form.quota_tokens = 0; form.quota_cost = 0; form.allowed_models = ''; form.expires_at = ''
  showKeyDialog.value = true
}
function openEdit(k) {
  editing.value = k
  form.name = k.name || ''
  form.quota_tokens = k.quota_tokens || 0
  form.quota_cost = k.quota_cost || 0
  form.allowed_models = k.allowed_models || ''
  form.expires_at = k.expires_at ? k.expires_at.slice(0, 10) : ''
  showKeyDialog.value = true
}
async function saveKey() {
  try {
    const payload = { ...form, expires_at: form.expires_at ? form.expires_at + 'T23:59:59Z' : '' }
    if (editing.value) {
      await api.gatewayUpdateKey(editing.value.id, payload)
      ElMessage.success('已保存')
    } else {
      await api.gatewayCreateKey(payload)
      ElMessage.success('已创建')
    }
    showKeyDialog.value = false
    await load()
  } catch (e) { ElMessage.error(e.message) }
}
async function toggle(k) {
  try { await api.gatewayUpdateKey(k.id, { enabled: !k.enabled }); await load() }
  catch (e) { ElMessage.error(e.message) }
}
async function remove(k) {
  try { await api.gatewayDeleteKey(k.id); await load() }
  catch (e) { ElMessage.error(e.message) }
}
onMounted(load)
// 观测 tab 首次切入时加载
watch(tab, (v) => {
  if (v === 'observe') {
    if (!reqLoaded) { reqLoaded = true; loadRequests() }
    if (!statsLoaded) loadStats()
  }
})
</script>

<style scoped>
.tabs {
  position: sticky;
  top: 56px;
  z-index: 19;
  display: flex;
  gap: 6px;
  padding: 14px 24px 0;
  background: color-mix(in srgb, var(--bg) 85%, transparent);
  backdrop-filter: blur(12px);
}
.tabs button {
  padding: 8px 20px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-dim);
  background: transparent;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  cursor: pointer;
  transition: color .15s, background .15s;
}
.tabs button:hover { color: var(--text); }
.tabs button.on {
  color: var(--cyan);
  background: var(--card-bg);
  border-color: var(--card-border);
}
.body { padding: 20px 24px; }
.tab-pane { display: flex; flex-direction: column; gap: 16px; animation: pane-in .18s ease; }
@keyframes pane-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
.card { padding: 18px 20px; transition: border-color .2s; }
.card:hover { border-color: var(--card-border-hover); }
.card h2 {
  font-size: 15px; margin: 0 0 14px; color: var(--text);
  display: flex; align-items: center; gap: 8px;
}
.card h2::before {
  content: ''; width: 3px; height: 14px; background: var(--cyan);
  box-shadow: 0 0 6px var(--cyan); border-radius: 2px; flex: none;
}
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; gap: 12px; flex-wrap: wrap; }
.card-head h2 { margin: 0; }
.row { margin: 6px 0; color: var(--text-dim); font-size: 13px; display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.row b { color: var(--green); }
.dim { color: var(--text-faint); }
.err-text { color: var(--red); }
.warn-text { color: var(--amber); }
.mono { font-family: var(--mono, ui-monospace, monospace); }
code.mono { background: rgba(0, 229, 255, 0.08); padding: 2px 6px; border-radius: 4px; color: var(--cyan); }
/* 路由策略 */
.strategy { display: flex; flex-direction: column; gap: 10px; }
.st-row { display: flex; align-items: center; gap: 10px; }
.st-name { font-size: 13px; color: var(--text); min-width: 96px; }
.st-desc { font-size: 12px; }
.fb { margin-top: 6px; }
.fb-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.fb-editor {
  width: 100%; background: var(--inset-bg); border: 1px solid var(--card-border);
  border-radius: 6px; padding: 8px 10px; font-size: 12px; color: var(--text-dim);
  font-family: var(--mono, ui-monospace, monospace); resize: vertical;
}
.hint-inline { font-size: 12px; }
.model-group { padding: 12px 0; border-bottom: 1px solid rgba(143, 163, 200, 0.08); }
.model-group:last-child { border-bottom: none; padding-bottom: 0; }
.mg-model { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.mg-icon { color: var(--cyan); font-size: 12px; }
.mg-name { font-size: 13px; }
.mg-count { font-size: 11px; color: var(--text-faint); border: 1px solid var(--card-border); border-radius: 10px; padding: 1px 8px; }
.mg-hosts { display: flex; gap: 8px; flex-wrap: wrap; padding-left: 20px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-dim);
  border: 1px solid var(--card-border); border-radius: 12px; padding: 3px 11px;
  transition: border-color .15s, color .15s;
}
.chip.off { color: var(--text-faint); }
.chip.warn { color: var(--amber); border-color: rgba(255, 197, 61, 0.35); }
.chip em { font-style: normal; font-size: 10px; color: var(--amber); }
/* 主机列表 */
.host-list { display: flex; flex-direction: column; }
.hl-row {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 10px 12px; border-bottom: 1px solid var(--card-border);
  transition: background .15s;
}
.hl-row:last-child { border-bottom: none; }
.hl-row:hover { background: var(--card-inset); }
.hl-name { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; color: var(--text); min-width: 130px; }
.hl-model { flex: 1; min-width: 160px; font-size: 12px; background: var(--inset-bg); padding: 2px 8px; border-radius: 4px; }
.hl-speed { font-size: 13px; font-weight: 600; color: var(--cyan); min-width: 72px; text-align: right; }
.hl-meta { display: flex; gap: 10px; flex-wrap: wrap; font-size: 11px; color: var(--text-dim); }
.hl-empty { text-align: center; color: var(--text-faint); padding: 24px; }
/* 两列布局 */
.ov-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 900px) { .ov-cols { grid-template-columns: 1fr; } }
/* 路由策略 */
.strat-list { display: flex; flex-direction: column; gap: 10px; }
.strat-item {
  display: flex; align-items: center; gap: 10px;
  border: 1px solid var(--card-border); border-radius: 8px; padding: 10px 12px;
  background: var(--card-inset);
}
.strat-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text-faint); flex: none; }
.strat-item.on .strat-dot { background: var(--green); box-shadow: 0 0 8px var(--green); }
.strat-txt { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
.strat-txt b { font-size: 13px; color: var(--text); }
.strat-txt .dim { font-size: 11px; }
.strat-state { font-size: 12px; color: var(--text-faint); flex: none; }
.strat-item.on .strat-state { color: var(--green); }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot.ok { background: var(--green); }
.dot.off { background: var(--text-faint); }
.dot.warn { background: var(--amber); }
.warn { color: var(--amber); }
.ex { margin: 12px 0; }
.ex-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.ex-title { font-size: 12px; color: var(--text-faint); }
.ex pre {
  background: var(--inset-bg); border: 1px solid var(--card-border); border-radius: 6px;
  padding: 10px 12px; font-size: 12px; color: var(--text-dim);
  white-space: pre-wrap; word-break: break-all; margin: 0;
}
.btn {
  background: rgba(0, 229, 255, 0.12); color: var(--cyan); border: 1px solid var(--card-border);
  border-radius: 6px; font-size: 12px; padding: 5px 12px; cursor: pointer; font-family: inherit;
}
.btn:hover { background: rgba(0, 229, 255, 0.2); }
.keys { width: 100%; border-collapse: collapse; font-size: 13px; }
.keys th, .keys td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--card-border); }
.keys th {
  color: var(--text-faint); font-weight: 600; font-size: 11px; letter-spacing: 0.4px;
  background: rgba(0, 229, 255, 0.03);
}
.keys.reqs td { font-size: 12px; }
.mini-tbl { font-size: 12px; }
.mini-tbl th, .mini-tbl td { padding: 6px 8px; }
.ok { color: var(--green); }
.off { color: var(--text-faint); }
.err { color: var(--red); }
.nowrap { white-space: nowrap; }
.ops { white-space: nowrap; }
.clickable { cursor: pointer; }
.keys tbody tr { transition: background .12s; }
.keys tbody tr:hover td { background: rgba(0, 229, 255, 0.04); }
.mini {
  background: transparent; border: 1px solid var(--card-border); color: var(--text-dim);
  border-radius: 4px; font-size: 11px; padding: 2px 8px; cursor: pointer; margin-left: 6px; font-family: inherit;
}
.mini:hover { color: var(--cyan); border-color: var(--card-border-hover); }
.mini.danger:hover { color: var(--red); border-color: var(--red); }
.empty { text-align: center; color: var(--text-faint); padding: 20px; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.sel {
  background-color: var(--inset-bg-strong);
  background-image: var(--select-arrow);
  background-repeat: no-repeat;
  background-position: right 8px center;
  background-size: 10px 6px;
  color: var(--text); border: 1px solid var(--card-border-hover);
  border-radius: 6px; font-size: 12px; padding: 5px 26px 5px 10px; font-family: inherit;
}
.sel:focus { border-color: var(--cyan); }
.sel:disabled { opacity: 0.5; cursor: not-allowed; }
.hint { font-size: 12px; margin: 10px 0 0; }
/* 成本统计 */
.stat-cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.stat-card {
  flex: 1; min-width: 130px; position: relative; overflow: hidden;
  background: var(--card-inset-grad);
  border: 1px solid var(--card-border);
  border-radius: 10px; padding: 12px 16px; display: flex; flex-direction: column; gap: 6px;
  transition: border-color .15s, transform .15s, box-shadow .15s;
}
.stat-card::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: linear-gradient(180deg, var(--cyan), transparent); opacity: 0.55;
}
.stat-card:hover { border-color: var(--card-border-hover); transform: translateY(-2px); box-shadow: 0 6px 18px rgba(0, 229, 255, 0.12); }
.sc-k { font-size: 11px; color: var(--text-faint); letter-spacing: 0.4px; }
.sc-v { font-size: 20px; color: var(--text); font-weight: 600; line-height: 1.2; }
.sc-sub { font-size: 13px; color: var(--text-dim); font-weight: 400; margin-left: 2px; }
.sc-foot { font-size: 11px; color: var(--text-faint); margin-top: 2px; }
.sc-url { font-size: 13px; word-break: break-all; font-weight: 500; }
.sc-host { font-size: 16px; }
.stat-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; }
/* Key 详情 */
.kd { display: flex; flex-direction: column; gap: 18px; }
.kd-sec { display: flex; flex-direction: column; gap: 8px; }
.trend { display: flex; flex-direction: column; gap: 5px; max-height: 260px; overflow-y: auto; }
.trend-row { display: flex; align-items: center; gap: 8px; font-size: 11px; }
.trend-day { color: var(--text-faint); min-width: 44px; }
.trend-track { flex: 1; height: 8px; background: var(--track-bg); border-radius: 4px; overflow: hidden; }
.trend-track i { display: block; height: 100%; background: rgba(0, 229, 255, 0.5); border-radius: 4px; }
.trend-val { color: var(--text-dim); min-width: 130px; text-align: right; }
/* 链路瀑布图 */
.trace .row { margin: 8px 0; }
.wf { margin-top: 16px; }
.wf-axis { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-faint); margin-bottom: 4px; padding-left: 118px; padding-right: 84px; }
.wf-row { margin-bottom: 8px; }
.wf-row .wf-name, .wf-row .wf-track, .wf-row .wf-time { display: inline-block; vertical-align: middle; }
.wf-name { width: 112px; font-size: 11px; color: var(--cyan); font-weight: 600; white-space: nowrap; }
.wf-track {
  position: relative; width: calc(100% - 202px); height: 12px; margin: 0 6px;
  background: var(--track-bg); border: 1px solid var(--card-border); border-radius: 3px;
}
.wf-bar { position: absolute; top: 1px; bottom: 1px; border-radius: 2px; }
.wf-bar.gw { background: rgba(0, 150, 255, 0.75); }
.wf-bar.up { background: rgba(0, 255, 136, 0.65); }
.wf-bar.retry { background: rgba(255, 180, 84, 0.8); }
.wf-bar.deg { background: rgba(255, 107, 107, 0.8); }
.wf-time { width: 78px; font-size: 10px; color: var(--text-faint); text-align: right; }
.wf-meta-lines {
  display: block; width: 100%; box-sizing: border-box; margin: 4px 0 0 118px;
  background: var(--inset-bg); border: 1px solid var(--card-border); border-radius: 6px;
  padding: 6px 10px; font-size: 11px;
}
.wf-meta-line { display: flex; gap: 8px; line-height: 1.7; }
.wk { color: var(--cyan); min-width: 64px; flex-shrink: 0; }
.wv { color: var(--text-dim); word-break: break-all; }
/* 内容详情 */
.td-sec { margin: 14px 0; }
.td-block { margin-bottom: 10px; }
.td-k {
  display: inline-block; font-size: 11px; color: var(--text-faint);
  border: 1px solid var(--card-border); border-radius: 4px 4px 0 0;
  padding: 1px 8px; margin-bottom: -1px; background: var(--chip-inset);
}
.td-pre {
  background: var(--inset-bg); border: 1px solid var(--card-border); border-radius: 6px;
  padding: 8px 12px; font-size: 12px; color: var(--text-dim);
  white-space: pre-wrap; word-break: break-word; margin: 0;
  max-height: 220px; overflow-y: auto;
}
.wf-legend { display: flex; gap: 14px; margin-top: 10px; font-size: 11px; color: var(--text-faint); }
.wf-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
.lg.gw { background: rgba(0, 150, 255, 0.75); }
.lg.up { background: rgba(0, 255, 136, 0.65); }
.lg.retry { background: rgba(255, 180, 84, 0.8); }
.lg.deg { background: rgba(255, 107, 107, 0.8); }
</style>
