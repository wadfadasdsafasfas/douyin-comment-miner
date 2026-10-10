/* 听潮 · 前端逻辑（无构建依赖，Electron / 浏览器通用）
   所有数据来自本地 sidecar：/api/*，实时事件走 /api/crawl/events (SSE) */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const API = '';                       // 同源，直接相对路径

/* ---------------------------------------------------------------- 基础 */
async function api(path, opt = {}) {
  const o = Object.assign({ headers: { 'Content-Type': 'application/json' } }, opt);
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  const r = await fetch(API + path, o);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { const d = await r.json(); msg = (d.detail && (d.detail.message || d.detail)) || msg; } catch (e) {}
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return r.json();
}

function toast(msg, kind = 'ok') {
  const t = document.createElement('div');
  t.className = 'tc-toast';
  const color = kind === 'err' ? 'var(--tc-brand)' : '#5EEAD4';
  t.innerHTML = `<svg class="tc-icon" style="color:${color}"><use href="/assets/icons/sprite.svg#${kind === 'err' ? 'tc-alert' : 'tc-check'}"/></svg><span></span>`;
  t.querySelector('span').textContent = msg;
  $('#toasts').appendChild(t);
  setTimeout(() => { t.style.transition = 'opacity .25s,transform .25s'; t.style.opacity = 0; t.style.transform = 'translateY(10px)'; }, 2400);
  setTimeout(() => t.remove(), 2700);
}

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
// 对外统一显示为 V1.0 这种两段式；内部版本号仍是 1.0.0（升级比较要用完整语义化版本）
const displayVersion = v => {
  const p = String(v || '').split('.');
  return p.length >= 2 ? `V${p[0]}.${p[1]}` : (v ? `V${v}` : '');
};
const BRAND_VENDOR = '层峰科技';
// preload 暴露的原生桥（浏览器里调试时为 null，全部降级为 no-op）
// 必须在任何顶层使用之前声明，否则 const TDZ 直接把整个脚本打死 → 白屏
const bridge = window.tingchao || null;
const PLAT_CN = { douyin: '抖音', xhs: '小红书', wechat: '视频号' };
const STATUS_CN = { new: '待联系', contacted: '已联系', replied: '已回复', won: '已成交', invalid: '无效' };
const STATUS_CLS = { new: 'tc-pill-warning', contacted: 'tc-pill-neutral', replied: 'tc-pill-success', won: 'tc-pill-success', invalid: 'tc-pill-danger' };

/* ---------------------------------------------------------------- 状态 */
const S = { me: null, cfg: null, page: 'monitor', leads: { page: 1, size: 20, total: 0, items: [] }, sel: new Set(), es: null, busy: false };

/* ---------------------------------------------------------------- 启动 */
async function boot() {
  try {
    const h = await api('/api/health');
    $('#srv-dot').style.background = 'var(--tc-teal)';
    $('#srv-text').textContent = `本地服务在线 · ${displayVersion(h.version)}`;
  } catch (e) {
    $('#srv-dot').style.background = 'var(--tc-brand)';
    $('#srv-text').textContent = '连不上本地服务，请重启应用';
    return;
  }
  const me = await api('/api/auth/me');
  S.me = me;
  if (me.logged_in) enterApp(); else showLogin();
}

function showLogin() {
  $('#view-app').classList.add('hidden');
  $('#view-login').classList.remove('hidden');
  api('/api/config').then(c => { S.cfg = c; }).catch(() => {});
}

function enterApp() {
  $('#view-login').classList.add('hidden');
  $('#view-app').classList.remove('hidden');
  renderMe();
  loadConfig().then(() => { go(S.page); connectEvents(); refreshStats(); });
  startAuthWatch();
}

// 每 3 分钟向服务器核对一次授权；到期/被停用/被顶号立即踢回登录页
async function pollAuthOnce() {
  if (!S.me || $('#view-app').classList.contains('hidden')) return;
  try {
    const me = await api('/api/auth/me');
    if (me.logged_in) { S.me = me; renderMe(); return; }
    if (S.authTimer) { clearInterval(S.authTimer); S.authTimer = null; }
    await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
    // 服务器返回的 msg 已含精确到秒的到期时间（auth_db._fmt_exp）
    const msg = me.msg || '登录状态已失效，请重新登录';
    S.me = null;
    showLogin();
    loginErr(msg);
    toast(msg, 'err');
  } catch (e) { /* 本地服务或网络抖动：静默，下个周期再试 */ }
}
function startAuthWatch() {
  if (S.authTimer) clearInterval(S.authTimer);
  S.authTimer = setInterval(pollAuthOnce, 3 * 60 * 1000);
}

function renderMe() {
  const m = S.me || {};
  $('#top-user').textContent = m.username || '—';
  $('#top-av').textContent = (m.username || '?').slice(0, 1).toUpperCase();
  // 侧栏只露出品方；授权服务器地址收进「系统设置」，不对终端用户展示
  $('#side-ver').textContent = `${displayVersion(m.version)} · ${BRAND_VENDOR}`;
  const days = m.days_left;
  $('#quota-days').textContent = days == null ? '永久' : `${days} 天`;
  $('#quota-exp').textContent = m.expires_at ? `到期 ${String(m.expires_at).slice(0, 10)}` : '—';
  const pct = days == null ? 100 : Math.max(4, Math.min(100, Math.round(days / 365 * 100)));
  $('#quota-bar').style.width = pct + '%';
}

/* ---------------------------------------------------------------- 登录 */
$('#lg-eye').onclick = () => {
  const i = $('#lg-pass');
  i.type = i.type === 'password' ? 'text' : 'password';
  $('#lg-eye').style.color = i.type === 'text' ? 'var(--tc-brand-ink)' : '';
};
function loginErr(msg) { const e = $('#lg-error'); e.textContent = msg; e.classList.toggle('hidden', !msg); }

async function doLogin(kick) {
  const u = $('#lg-user').value.trim(), p = $('#lg-pass').value;
  if (!u || !p) return loginErr('请输入账号和密码');
  const btn = $('#lg-btn'); btn.disabled = true; btn.innerHTML = '<span class="tc-spinner"></span>登录中…';
  loginErr('');
  try {
    const r = await api('/api/auth/login', { method: 'POST', body: { username: u, password: p, kick_existing: !!kick } });
    if (!r.ok) {
      if (r.conflict_device) { $('#conflict-dev').textContent = `已登录设备：${r.conflict_device}`; $('#ov-conflict').classList.remove('hidden'); }
      else loginErr(r.msg || '登录失败');
      return;
    }
    S.me = await api('/api/auth/me');
    enterApp(); toast('登录成功，欢迎回来');
  } catch (e) { loginErr(e.message); }
  finally { btn.disabled = false; btn.textContent = '登 录'; }
}
$('#lg-btn').onclick = () => doLogin(false);
$('#lg-pass').onkeydown = e => { if (e.key === 'Enter') doLogin(false); };
$('#conflict-ok').onclick = () => { $('#ov-conflict').classList.add('hidden'); doLogin(true); };
$('#lg-trial').onclick = async () => {
  const b = $('#lg-trial'); b.disabled = true;
  try { const r = await api('/api/auth/trial', { method: 'POST' }); toast(`试用已开通：${r.trial.expires_at || ''}`); await doLoginTrial(); }
  catch (e) { toast(e.message, 'err'); }
  finally { b.disabled = false; }
};
async function doLoginTrial() { S.me = await api('/api/auth/me'); if (S.me.logged_in) enterApp(); }

$('#btn-logout').onclick = async () => {
  await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
  if (S.es) { S.es.close(); S.es = null; }
  S.me = null; showLogin();
};

/* ---------------------------------------------------------------- 导航 */
const TITLES = { monitor: '评论监控', leads: '线索名单', platforms: '平台账号', settings: '系统设置', help: '使用说明' };
$$('.tc-nav-item').forEach(n => n.onclick = () => go(n.dataset.page));
function go(page) {
  S.page = page;
  $$('.tc-nav-item').forEach(n => n.classList.toggle('is-active', n.dataset.page === page));
  $$('.page').forEach(p => p.classList.add('hidden'));
  const el = $('#page-' + page); if (el) el.classList.remove('hidden');
  $('#crumb').textContent = TITLES[page] || page;
  if (page === 'leads') loadLeads();
  if (page === 'platforms') loadPlatforms();
  if (page === 'settings') renderSettings();
  if (page === 'monitor') refreshStats();
}

/* ---------------------------------------------------------------- 配置 */
async function loadConfig() { S.cfg = await api('/api/config'); return S.cfg; }

/* 平台清单：ready=false 的可选但不放行，点了提示暂未开放 */
const PLATFORMS = [
  { key: 'douyin', name: '抖音', ready: true },
  { key: 'xhs', name: '小红书', ready: false },
  { key: 'wechat', name: '视频号', ready: false },
];
const platName = k => (PLATFORMS.find(p => p.key === k) || { name: k }).name;
const platReady = k => !!(PLATFORMS.find(p => p.key === k) || {}).ready;
let cfgPlatform = 'douyin';

function platformPicker() {
  return `<div class="tc-field"><label class="tc-label">采集平台（单次任务只能选一个）</label>
    <div class="plat-pick" id="cf-plat">${PLATFORMS.map(p =>
      `<button type="button" class="pp${cfgPlatform === p.key ? ' is-on' : ''}${p.ready ? '' : ' is-off'}" data-p="${p.key}">${esc(p.name)}${p.ready ? '' : '<i>暂未开放</i>'}</button>`).join('')}</div>
    <div class="cfg-hint">目前仅开放抖音，其余平台的采集中器接入后自动可选。</div></div>`;
}

function configForm(c) {
  return `
    ${platformPicker()}
    <div class="tc-field"><label class="tc-label">监控链接（每行一个，支持分享文案自动提取）</label>
      <textarea class="cfg-links" id="cf-links" placeholder="https://v.douyin.com/xxxx/">${esc((c.links || []).join('\n'))}</textarea>
      <div class="cfg-hint">粘贴抖音分享文案也可以，系统会自动抠出链接。</div></div>
    <div class="tc-field"><label class="tc-label">命中关键词（回车添加，命中任意一个即入选）</label>
      <div class="tc-chips" id="cf-chips">${(c.keywords || []).map(k => `<span class="tc-chip">${esc(k)}<span class="tc-chip-x">×</span></span>`).join('')}<input class="tc-chips-input" id="cf-kw" placeholder="输入后回车"></div></div>
    <div class="cfg-2col">
      <div class="tc-field"><label class="tc-label">时间范围</label>
        <select class="tc-input tc-select" id="cf-range">${['全部', '最近 7 天', '最近 30 天', '最近 90 天'].map(x => `<option ${c.range === x ? 'selected' : ''}>${x}</option>`).join('')}</select></div>
      <div class="tc-field"><label class="tc-label">抓取深度（条/视频）</label>
        <input class="tc-input" id="cf-depth" type="number" min="50" max="5000" step="50" value="${c.max_comments || 3000}"></div>
    </div>
    <div class="switch-row"><div><div class="st">连子回复一起抓</div><div class="ss">更全，但更慢、更容易触发风控</div></div>
      <span class="tc-switch ${c.with_replies ? 'is-on' : ''}" id="cf-replies"></span></div>`;
}

let chips = [];
function bindConfigForm() {
  chips = (S.cfg.keywords || []).slice();
  const box = $('#cf-chips'), inp = $('#cf-kw');
  const paint = () => {
    box.innerHTML = chips.map(k => `<span class="tc-chip">${esc(k)}<span class="tc-chip-x">×</span></span>`).join('') +
      `<input class="tc-chips-input" id="cf-kw" placeholder="输入后回车">`;
    const ni = $('#cf-kw');
    ni.onkeydown = e => {
      if (e.key === 'Enter') { e.preventDefault(); const v = ni.value.trim(); if (v && !chips.includes(v)) chips.push(v); ni.value = ''; paint(); $('#cf-kw').focus(); }
      if (e.key === 'Backspace' && !ni.value && chips.length) { chips.pop(); paint(); $('#cf-kw').focus(); }
    };
    $$('.tc-chip-x', box).forEach((x, i) => x.onclick = () => { chips.splice(i, 1); paint(); });
  };
  paint();
  $('#cf-replies').onclick = e => e.currentTarget.classList.toggle('is-on');
  // 平台选择：未开放的可点，但只提示不切换
  $$('#cf-plat .pp').forEach(b => b.onclick = () => {
    const k = b.dataset.p;
    if (!platReady(k)) { toast(`「${platName(k)}」暂未开放，已保持当前平台`, 'err'); return; }
    cfgPlatform = k;
    $$('#cf-plat .pp').forEach(x => x.classList.toggle('is-on', x.dataset.p === k));
  });
}

function readConfigForm() {
  return {
    platform: cfgPlatform,
    links: $('#cf-links').value.split('\n').map(s => s.trim()).filter(Boolean),
    keywords: chips.slice(),
    range: $('#cf-range').value,
    max_comments: Math.max(50, parseInt($('#cf-depth').value || '3000', 10)),
    with_replies: $('#cf-replies').classList.contains('is-on'),
  };
}

$('#btn-config').onclick = async () => {
  S.cfg = S.cfg || await loadConfig();
  cfgPlatform = platReady(S.cfg.platform) ? S.cfg.platform : 'douyin';
  $('#cfg-body').innerHTML = configForm(S.cfg);
  bindConfigForm();
  $('#ov-config').classList.remove('hidden');
};
$('#cfg-save').onclick = async () => {
  S.cfg = await api('/api/config', { method: 'POST', body: readConfigForm() });
  cfgPlatform = S.cfg.platform;
  syncPlatSeg();
  $('#ov-config').classList.add('hidden'); toast('配置已保存');
};

// 页面右上角的平台切换与配置里的平台保持同一状态
function syncPlatSeg() {
  $$('#plat-seg button').forEach(b => b.classList.toggle('is-on', b.dataset.p === S.cfg.platform));
}
$$('[data-close]').forEach(b => b.onclick = () => b.closest('.tc-overlay').classList.add('hidden'));
$$('.tc-overlay').forEach(o => o.addEventListener('mousedown', e => { if (e.target === o) o.classList.add('hidden'); }));
document.addEventListener('keydown', e => { if (e.key === 'Escape') $$('.tc-overlay').forEach(o => o.classList.add('hidden')); });

/* ---------------------------------------------------------------- 采集 + SSE */
async function startCrawl() {
  if (!S.cfg) await loadConfig();
  const hasLinks = (S.cfg.links || []).length;
  if (!hasLinks) { $('#cfg-body').innerHTML = configForm(S.cfg); bindConfigForm(); $('#ov-config').classList.remove('hidden'); toast('请先填写视频链接', 'err'); return; }
  S.busy = true; paintBusy();
  try {
    const r = await api('/api/crawl/start', { method: 'POST', body: Object.assign({ platform: curPlat() }, S.cfg) });
    if (!r.ok) { toast(r.msg, 'err'); S.busy = false; paintBusy(); }
  } catch (e) { toast(e.message, 'err'); S.busy = false; paintBusy(); }
}
async function stopCrawl() {
  try { await api('/api/crawl/stop', { method: 'POST' }); } catch (e) { toast(e.message, 'err'); }
}
$('#btn-start').onclick = startCrawl;
$('#btn-stop').onclick = stopCrawl;
$('#btn-open-leads').onclick = () => go('leads');

function curPlat() { return (S.cfg && S.cfg.platform) || 'douyin'; }
$$('#plat-seg button').forEach(b => b.onclick = async () => {
  const k = b.dataset.p;
  if (!platReady(k)) { toast(`「${platName(k)}」暂未开放`, 'err'); syncPlatSeg(); return; }
  if (S.cfg) { S.cfg = await api('/api/config', { method: 'POST', body: { platform: k } }); cfgPlatform = k; }
  syncPlatSeg();
});
function paintBusy() {
  $('#btn-start').disabled = S.busy;
  $('#btn-stop').disabled = !S.busy;
  $('#live').classList.toggle('is-running', S.busy);
}

function connectEvents() {
  if (S.es) S.es.close();
  const es = new EventSource('/api/crawl/events');
  S.es = es;
  es.onmessage = e => {
    let d; try { d = JSON.parse(e.data); } catch (err) { return; }
    onEvent(d);
  };
  es.onerror = () => { setTimeout(() => { if (S.es === es) connectEvents(); }, 2500); };
}

function onEvent(d) {
  switch (d.type) {
    case 'hello':
      if (d.job && d.job.state === 'running') { S.busy = true; paintBusy(); }
      break;
    case 'start': S.busy = true; paintBusy(); break;
    case 'progress':
      $('#k-collected').textContent = d.collected; $('#k-hits').textContent = d.hits;
      $('#nav-live').textContent = d.hits;
      $('#live-text').innerHTML = `抓取中 · 已扫 <b>${d.collected}</b> 条 / 命中 <b class="is-brand">${d.hits}</b>`;
      break;
    case 'hit':
      $('#k-hits').textContent = d.hits; $('#k-new').textContent = d.new_leads; $('#nav-live').textContent = d.hits;
      addLiveRow(d.lead);
      // 窗口不在前台时，只给「去重后的新线索」发系统通知，避免刷屏
      if (d.is_new && document.hidden) nativeNotify('新线索 · ' + (d.lead.nickname || ''), (d.lead.comment || '').slice(0, 40));
      break;
    case 'need_login':
      toast(d.msg, 'err'); $('#live-text').textContent = d.msg; break;
    case 'login_result':
      toast(d.msg, d.ok ? 'ok' : 'err'); if (d.ok) loadPlatforms(); break;
    case 'log':
      // 引擎的进度/告警以前被前端丢掉了，导致「抓到 0 条」时完全看不出原因
      $('#live-text').innerHTML = `<span class="muted">${esc(d.msg || '')}</span>`;
      break;
    case 'done':
      S.busy = false; paintBusy();
      if (d.status === 'error') {
        $('#live-text').innerHTML = `<span style="color:var(--tc-brand-ink)">抓取中断：${esc(d.error || '')}</span>`;
        toast(d.error || '抓取出错', 'err');
      } else if (!d.collected) {
        $('#live-text').innerHTML = '<span style="color:var(--tc-brand-ink)">没扫到评论：多为登录态失效或被风控，去「平台账号」重新登录</span>';
        toast('未扫到任何评论，请检查抖音登录态', 'err');
      } else {
        $('#live-text').innerHTML = `本轮结束 · 扫 <b>${d.collected}</b> 条 / 命中 <b class="is-brand">${d.hits}</b> / 新增线索 <b>${d.new_leads}</b>`;
        toast(`抓取结束，新增 ${d.new_leads} 条线索`);
      }
      refreshStats();
      break;
    case 'error': toast(d.msg, 'err'); break;
    case 'auth': if (d.ok) { S.me = null; } break;
  }
}

function addLiveRow(l) {
  $('#live-empty').classList.add('hidden');
  const tb = $('#live-rows');
  const tr = document.createElement('tr');
  tr.className = 'row-in';
  tr.innerHTML = `<td class="tc-num is-muted">${tb.children.length + 1}</td>
    <td><span class="tc-badge tc-badge-${l.platform === 'xhs' ? 'xhs' : 'douyin'}">${PLAT_CN[l.platform] || l.platform}</span></td>
    <td class="cell-nick">${esc(l.nickname)}</td>
    <td class="cell-comment">${hl(l.comment, l.keywords)}</td>
    <td>${(l.keywords || '').split(',').filter(Boolean).map(k => `<span class="tc-pill tc-pill-warning" style="margin-right:4px"><span class="tc-dot"></span>${esc(k)}</span>`).join('') || '<span class="is-muted">—</span>'}</td>
    <td class="is-muted">${esc(l.comment_time || '')}</td>
    <td class="is-right"><a class="tc-btn tc-btn-ghost tc-btn-sm" target="_blank" href="${esc(l.profile_url || '#')}">主页</a></td>`;
  tb.insertBefore(tr, tb.firstChild);
  while (tb.children.length > 200) tb.removeChild(tb.lastChild);
}

function hl(text, kwstr) {
  let out = esc(text);
  (kwstr || '').split(',').filter(Boolean).forEach(k => {
    out = out.split(esc(k)).join(`<mark>${esc(k)}</mark>`);
  });
  return out;
}

/* ---------------------------------------------------------------- 统计与图表 */
async function refreshStats() {
  try {
    const s = await api('/api/stats');
    const L = s.leads || {};
    $('#k-total').textContent = L.total || 0;
    $('#nav-leads').textContent = L.total || 0;
    if (!S.busy) $('#live-text').innerHTML = `线索池 <b>${L.total || 0}</b> 条 · 待联系 <b class="is-brand">${L.new || 0}</b>`;
    drawTrend(L.trend || []);
    drawDonut(L.by_keyword || []);
  } catch (e) { /* 静默 */ }
}

function drawTrend(pts) {
  const svg = $('#chart-trend');
  const W = 640, H = 190, pad = 20;
  const max = Math.max(1, ...pts.map(p => p.leads));
  const x = i => pad + i * ((W - pad * 2) / Math.max(1, pts.length - 1));
  const y = v => H - 30 - (v / max) * (H - 60);
  const line = pts.map((p, i) => `${x(i)},${y(p.leads)}`).join(' ');
  const area = pts.length ? `M${x(0)},${y(pts[0].leads)} ` + pts.slice(1).map((p, i) => `L${x(i + 1)},${y(p.leads)}`).join(' ') + ` L${x(pts.length - 1)},160 L${x(0)},160 Z` : '';
  svg.innerHTML = `
    <g class="tc-grid"><line x1="0" y1="40" x2="640" y2="40"/><line x1="0" y1="90" x2="640" y2="90"/><line x1="0" y1="140" x2="640" y2="140"/></g>
    ${area ? `<path class="tc-area-1" d="${area}"/>` : ''}
    <polyline class="tc-series-1" points="${line}"/>
    ${pts.map((p, i) => `<circle class="tc-node" cx="${x(i)}" cy="${y(p.leads)}" r="4"/>`).join('')}
    <g class="tc-axis" text-anchor="middle">${pts.map((p, i) => `<text x="${x(i)}" y="180">${esc(p.date)}</text>`).join('')}</g>`;
  const sum = pts.reduce((a, b) => a + b.leads, 0);
  $('#trend-sum').textContent = `近 7 天 +${sum}`;
}

function drawDonut(items) {
  const svg = $('#chart-donut'), lg = $('#donut-legend');
  const total = items.reduce((a, b) => a + b.value, 0) || 1;
  const C = 2 * Math.PI * 54, cols = ['var(--tc-chart-1)', 'var(--tc-chart-2)', 'var(--tc-chart-3)', 'var(--tc-amber)', 'var(--tc-teal)'];
  let off = 0;
  svg.innerHTML = `<circle class="tc-donut-track" cx="70" cy="70" r="54"/>` +
    items.map((it, i) => {
      const len = C * (it.value / total);
      const seg = `<circle class="tc-seg-${Math.min(i + 1, 3)}" cx="70" cy="70" r="54" stroke="${cols[i % cols.length]}" stroke-dasharray="${len.toFixed(1)} ${C.toFixed(1)}" stroke-dashoffset="${(-off).toFixed(1)}"/>`;
      off += len; return seg;
    }).join('');
  svg.style.transform = 'rotate(-90deg)';
  lg.innerHTML = items.length ? items.map((it, i) =>
    `<div><i style="display:inline-block;width:9px;height:9px;border-radius:3px;background:${cols[i % cols.length]};margin-right:7px"></i>${esc(it.name)} <b>${it.value}</b></div>`).join('')
    : '<div style="color:var(--tc-ink-3)">暂无命中数据</div>';
}

/* ---------------------------------------------------------------- 线索池 */
async function loadLeads() {
  const p = $('#leads-status').value, q = $('#leads-q').value.trim();
  const d = await api(`/api/leads?platform=all&status=${encodeURIComponent(p)}&q=${encodeURIComponent(q)}&page=${S.leads.page}&size=${S.leads.size}`);
  S.leads = Object.assign(S.leads, d);
  const tb = $('#leads-rows');
  tb.innerHTML = (d.items || []).map(r => `<tr data-id="${r.id}" class="${S.sel.has(r.id) ? 'is-selected' : ''}">
    <td><span class="tc-check ${S.sel.has(r.id) ? 'is-on' : ''}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round"><path d="M20 6 9 17l-5-5"/></svg></span></td>
    <td><span class="tc-badge tc-badge-${r.platform === 'xhs' ? 'xhs' : 'douyin'}">${PLAT_CN[r.platform] || esc(r.platform)}</span></td>
    <td class="cell-nick">${esc(r.nickname)}<div style="font-size:11.5px;color:var(--tc-ink-3);font-weight:400">${esc(r.region || '')}</div></td>
    <td class="cell-comment">${hl(r.comment, r.keywords)}</td>
    <td>${(r.keywords || '').split(',').filter(Boolean).map(k => `<span class="tc-pill tc-pill-warning">${esc(k)}</span>`).join(' ') || '<span class="is-muted">—</span>'}</td>
    <td><span class="tc-pill ${STATUS_CLS[r.status] || 'tc-pill-neutral'}"><span class="tc-dot"></span>${STATUS_CN[r.status] || esc(r.status)}</span></td>
    <td class="is-muted">${esc((r.first_seen || '').slice(0, 16).replace('T', ' '))}</td>
    <td class="is-right"><a class="tc-btn tc-btn-ghost tc-btn-sm" target="_blank" href="${esc(r.profile_url || '#')}">主页</a></td></tr>`).join('');
  $('#leads-empty').classList.toggle('hidden', (d.items || []).length > 0);
  $('#leads-sum').textContent = `共 ${d.total} 条 · 第 ${d.page} 页`;
  renderPager(d.total, d.page, d.size);
  $$('.tc-check', tb).forEach(c => c.onclick = () => {
    const tr = c.closest('tr'), id = +tr.dataset.id;
    if (S.sel.has(id)) S.sel.delete(id); else S.sel.add(id);
    c.classList.toggle('is-on'); tr.classList.toggle('is-selected');
    paintBatch();
  });
  paintBatch();
}
function renderPager(total, page, size) {
  const pages = Math.max(1, Math.ceil(total / size)), box = $('#leads-pager');
  box.innerHTML = '';
  const mk = (label, p, on, dis) => {
    const b = document.createElement('button');
    b.textContent = label; if (on) b.classList.add('is-on'); if (dis) b.disabled = true;
    b.onclick = () => { S.leads.page = p; loadLeads(); }; box.appendChild(b);
  };
  mk('‹', page - 1, false, page <= 1);
  for (let i = 1; i <= pages; i++) {
    if (i === 1 || i === pages || Math.abs(i - page) <= 1) mk(String(i), i, i === page);
    else if (Math.abs(i - page) === 2) mk('…', i);
  }
  mk('›', page + 1, false, page >= pages);
}
function paintBatch() {
  $('#leads-batch').classList.toggle('hidden', S.sel.size === 0);
  $('#batch-count').textContent = `已选 ${S.sel.size} 条`;
  $('#chk-all').classList.toggle('is-on', S.leads.items.length > 0 && S.sel.size === S.leads.items.length);
}
$('#chk-all').onclick = () => {
  const on = !$('#chk-all').classList.contains('is-on');
  S.leads.items.forEach(r => on ? S.sel.add(r.id) : S.sel.delete(r.id));
  loadLeads();
};
$('#leads-q').oninput = debounce(() => { S.leads.page = 1; loadLeads(); }, 300);
$('#leads-status').onchange = () => { S.leads.page = 1; loadLeads(); };
$('#batch-apply').onclick = async () => {
  const st = $('#batch-status').value;
  if (!st) return toast('先选一个状态', 'err');
  const r = await api('/api/leads/status', { method: 'POST', body: { ids: [...S.sel], status: st } });
  toast(`已更新 ${r.updated} 条`); S.sel.clear(); $('#batch-status').value = ''; loadLeads();
};
$('#btn-export').onclick = () => {
  const p = $('#leads-status').value, q = $('#leads-q').value.trim();
  window.location.href = `/api/leads/export?platform=all&status=${encodeURIComponent(p)}&q=${encodeURIComponent(q)}&fmt=xlsx`;
  toast('正在导出 Excel…');
};
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

/* ---------------------------------------------------------------- 平台 */
async function loadPlatforms() {
  const d = await api('/api/platforms');
  $('#plat-grid').innerHTML = d.items.map(p => `
    <div class="plat-card ${p.ready ? '' : 'is-off'}">
      <div class="ph"><div class="pn">${esc(p.name)}</div>
        ${p.ready ? (p.login && p.login.logged_in ? '<span class="tc-pill tc-pill-success"><span class="tc-dot"></span>已登录</span>' : '<span class="tc-pill tc-pill-neutral"><span class="tc-dot"></span>未登录</span>') : '<span class="tc-pill tc-pill-neutral">待接入</span>'}</div>
      <div class="pm">${p.ready ? '采集在你本机执行，登录态只存在这台电脑上，不上传云端。' : esc(p.note || '该平台的采集中器尚未接入')}</div>
      ${p.ready ? `<button class="tc-btn tc-btn-ghost tc-btn-sm" data-login="${p.key}"><svg class="tc-icon"><use href="/assets/icons/sprite.svg#tc-external"/></svg>打开登录窗口</button>` : ''}
    </div>`).join('');
  $$('[data-login]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    try { const r = await api('/api/platforms/login', { method: 'POST', body: { platform: b.dataset.login } }); toast(r.msg); }
    catch (e) { toast(e.message, 'err'); b.disabled = false; }
  });
}

/* ---------------------------------------------------------------- 设置页 */
async function renderSettings() {
  const m = S.me || {}, c = S.cfg || {};
  $('#settings-body').innerHTML = `
    <div class="info-row"><span>授权账号</span><b>${esc(m.username || '—')}</b></div>
    <div class="info-row"><span>到期时间</span><b>${esc((m.expires_at || '—').slice(0, 10))} · ${m.days_left == null ? '永久' : m.days_left + ' 天'}</b></div>
    <div class="info-row"><span>本机设备名</span><b>${esc(m.device_name || '—')}</b></div>
    <div class="info-row"><span>授权服务器</span><b>${esc(m.server_url || '—')}</b></div>
    <div class="info-row"><span>客户端版本</span><b>V${esc(m.version || '—')}</b></div>
    <div class="info-row"><span>出品方</span><b>${BRAND_VENDOR}</b></div>
    <div class="info-row"><span>采集平台</span><b>${esc(platName(c.platform || 'douyin'))}</b></div>
    <div class="tc-field" style="margin-top:18px"><label class="tc-label">抓取配置</label>
      <div class="cfg-hint">平台、链接、关键词、时间范围与抓取深度在弹窗里编辑。</div></div>`;
  const btn = $('#btn-save-config');
  btn.innerHTML = '<svg class="tc-icon"><use href="/assets/icons/sprite.svg#tc-settings"/></svg>编辑抓取配置';
  btn.onclick = () => $('#btn-config').click();
}
/* ---------------------------------------------------------------- 自升级 */
let hostInfo = { appRoot: '', hostPid: 0 };
if (bridge && bridge.platform) bridge.platform().then(i => { hostInfo = i || hostInfo; }).catch(() => {});

function updateModal(title, sub, btnText, onBtn) {
  let ov = $('#ov-update');
  if (!ov) {
    ov = document.createElement('div');
    ov.className = 'tc-overlay hidden';
    ov.id = 'ov-update';
    ov.innerHTML = `<div class="tc-modal" style="max-width:440px">
      <div class="tc-modal-head"><div><div class="tc-modal-title" id="up-t"></div><div class="tc-modal-sub" id="up-s"></div></div></div>
      <div class="tc-modal-body"><div class="tc-progress" style="margin-top:6px"><i id="up-bar" style="width:0%"></i></div></div>
      <div class="tc-modal-foot"><button class="tc-btn tc-btn-ghost" data-close>稍后</button>
      <button class="tc-btn tc-btn-primary" id="up-btn"></button></div></div>`;
    document.body.appendChild(ov);
    ov.addEventListener('mousedown', e => { if (e.target === ov) ov.classList.add('hidden'); });
    ov.querySelector('[data-close]').onclick = () => ov.classList.add('hidden');
  }
  $('#up-t').textContent = title;
  $('#up-s').textContent = sub;
  const b = $('#up-btn');
  b.textContent = btnText || '确定';
  b.style.display = btnText ? '' : 'none';
  b.onclick = onBtn || null;
  $('#up-bar').style.width = '0%';
  ov.classList.remove('hidden');
  return { close: () => ov.classList.add('hidden'), bar: p => { $('#up-bar').style.width = p + '%'; } };
}

async function checkUpdate(manual) {
  let r;
  try { r = await api('/api/update/info'); }
  catch (e) { if (manual) toast(e.message, 'err'); return; }
  if (!r.has_update) { if (manual) toast(`已是最新版本 v${r.current}`); return; }
  updateModal(`发现新版本 ${r.latest}`, r.notes || '正在准备更新…', '立即下载并安装', startUpdate);
}

async function startUpdate() {
  const m = updateModal('正在下载更新', '下载完成后会自动安装并重启', null);
  try {
    await api('/api/update/download', { method: 'POST' });
  } catch (e) { m.close(); toast(e.message, 'err'); return; }
  const timer = setInterval(async () => {
    let s;
    try { s = await api('/api/update/state'); } catch (e) { return; }
    m.bar(s.progress || 0);
    if (s.stage === 'ready') {
      clearInterval(timer);
      m.close();
      updateModal('下载完成', '即将安装并重启听潮，全过程约 10 秒', '立即安装并重启', doInstall);
    } else if (s.stage === 'error') {
      clearInterval(timer); m.close(); toast(s.error || '更新包下载失败', 'err');
    }
  }, 700);
}

async function doInstall() {
  const m = updateModal('正在安装', '应用即将自动重启…', null);
  try {
    await api('/api/update/install', { method: 'POST', body: { app_root: hostInfo.appRoot, host_pid: hostInfo.hostPid } });
    if (bridge && bridge.quitForUpdate) { await bridge.quitForUpdate(); }
    else { toast('请在应用退出后手动重启以完成升级'); m.close(); }
  } catch (e) { m.close(); toast(e.message, 'err'); }
}

/* ---------------------------------------------------------------- 与 Electron 壳桥接 */
const nativeNotify = (title, body) => { if (bridge && bridge.notify) bridge.notify(title, body).catch(() => {}); };

// 标记操作系统：CSS 据此给 macOS 红绿灯留出顶栏位置
// 注意 process.platform 在 mac 上返回的是 "darwin"，不是 "mac"
(function detectOS() {
  const set = p => { document.body.dataset.os = /darwin|mac|iphone|ipad/i.test(String(p)) ? 'darwin' : 'other'; };
  if (bridge && bridge.platform) bridge.platform().then(i => set(i && i.platform)).catch(() => set(''));
  else set(navigator.platform || navigator.userAgent);
})();

if (bridge && bridge.on) {
  bridge.on('menu-config', () => $('#btn-config').click());
  bridge.on('menu-start', startCrawl);
  bridge.on('menu-stop', stopCrawl);
  bridge.on('goto', page => go(page));
}

boot();
// 启动后静默检查一次更新；设置页按钮为手动检查
setTimeout(() => checkUpdate(false), 1500);
document.addEventListener('click', e => {
  if (e.target && e.target.closest && e.target.closest('#btn-check-update')) checkUpdate(true);
});
