const state = {
  view: 'feed',
  page: 1,
  size: 30,
  job: '',
  q: '',
  unread: false,
  silent: false,
  msg: '',
  anchorPending: false,
  until: null,
  stats: {},
  health: {},
  jobs: [],
  apps: [],
  lastContent: -1,
  loading: false,
  reqSeq: 0,
  focusIdx: -1,
  drawerMode: '',
  drawerUrl: '',
  lightboxCtx: null,
  ctxTarget: null,
  deletedOpen: false,
  syncing: false,
  confirmJob: '',
};

const $ = (id) => document.getElementById(id);

let boardPagerData = null;
const THEME_KEY = 'hub_theme';

function applyTheme(name, save) {
  const theme = name === 'gray' ? 'gray' : 'brand';
  document.documentElement.dataset.theme = theme;
  if (save) {
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* 隐私模式忽略 */ }
  }
  const btn = $('themeBtn');
  if (btn) {
    btn.textContent = theme === 'gray' ? '紫色' : '灰色';
    btn.title = theme === 'gray' ? '切换到紫色主题' : '切换到灰色主题';
    btn.setAttribute('aria-label', btn.title);
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = theme === 'gray' ? '#dcdcdf' : '#c9b8ff';
}

const STATUS = {
  ok: { label: '正常', dot: 'ok', cls: 'ok' },
  error: { label: '故障', dot: 'bad', cls: 'bad' },
  stalled: { label: '停摆', dot: 'bad', cls: 'bad' },
  quiet: { label: '久无产出', dot: 'warn', cls: 'warn' },
  paused: { label: '已暂停', dot: '', cls: '' },
  nofile: { label: '无留档', dot: '', cls: '' },
  orphan: { label: '已删除', dot: '', cls: '' },
};

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ cache: 'no-store' }, opts));
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) throw new Error((data && data.error) || ('请求失败 ' + res.status));
  return data;
}

function toast(text) {
  const el = $('toast');
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 2200);
}

/* ---------- markdown ---------- */

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function inline(s) {
  const codes = [];
  let t = esc(s).replace(/`([^`]+)`/g, (m, p) => {
    codes.push(p);
    return '\u0000' + (codes.length - 1) + '\u0000';
  });
  t = t.replace(/\[(.+?)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/(^|\s)\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
  t = t.replace(/\u0000(\d+)\u0000/g, (m, i) => '<code>' + codes[Number(i)] + '</code>');
  return t;
}

function isBlockStart(line) {
  return /^(#{1,4})\s/.test(line) || /^```/.test(line) || /^\s*[-*]\s+/.test(line) ||
    /^\s*\d+[.、]\s*/.test(line) || /^>\s?/.test(line) || /^(-{3,}|\*{3,})$/.test(line.trim());
}

function renderMarkdown(src) {
  const lines = src.split('\n');
  let html = '';
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    if (/^```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      html += '<pre><code>' + esc(buf.join('\n')) + '</code></pre>';
      continue;
    }

    const m = line.match(/^(#{1,4})\s+(.*)$/);
    if (m) {
      const lv = m[1].length;
      html += `<h${lv}>${inline(m[2])}</h${lv}>`;
      i++;
      continue;
    }

    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { html += '<hr>'; i++; continue; }

    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length) {
        if (/^\s*[-*]\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
          i++;
        } else if (items.length && lines[i].trim() && !isBlockStart(lines[i])) {
          items[items.length - 1] += ' ' + lines[i].trim();
          i++;
        } else {
          break;
        }
      }
      html += '<ul>' + items.map((t) => `<li>${inline(t)}</li>`).join('') + '</ul>';
      continue;
    }

    if (/^\s*\d+[.、]\s*/.test(line)) {
      const items = [];
      while (i < lines.length) {
        if (/^\s*\d+[.、]\s*/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*\d+[.、]\s*/, ''));
          i++;
        } else if (items.length && lines[i].trim() && !isBlockStart(lines[i])) {
          items[items.length - 1] += ' ' + lines[i].trim();
          i++;
        } else {
          break;
        }
      }
      html += '<ol>' + items.map((t) => `<li>${inline(t)}</li>`).join('') + '</ol>';
      continue;
    }

    if (/^>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, ''));
        i++;
      }
      html += '<blockquote>' + inline(buf.join(' ')) + '</blockquote>';
      continue;
    }

    const buf = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) {
      buf.push(lines[i]);
      i++;
    }
    html += '<p>' + buf.map(inline).join('<br>') + '</p>';
  }
  return html;
}

/* ---------- 时间 ---------- */

function fmtClock(runAt) {
  return (runAt || '').slice(11, 16);
}

function dayKey(runAt) {
  return (runAt || '').slice(0, 10);
}

function dayLabel(key) {
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const y = new Date(now.getTime() - 86400000);
  const yest = `${y.getFullYear()}-${String(y.getMonth() + 1).padStart(2, '0')}-${String(y.getDate()).padStart(2, '0')}`;
  if (key === today) return '今天';
  if (key === yest) return '昨天';
  const [year, m, d] = key.split('-');
  if (String(now.getFullYear()) !== year) return `${year} 年 ${Number(m)} 月 ${Number(d)} 日`;
  return `${Number(m)} 月 ${Number(d)} 日`;
}

function rel(ts) {
  if (!ts) return '无记录';
  const diff = Date.now() / 1000 - Number(ts);
  if (diff < 0) {
    const ahead = -diff;
    if (ahead < 60) return '即将运行';
    if (ahead < 3600) return `${Math.round(ahead / 60)} 分钟后`;
    if (ahead < 86400) return `${Math.round(ahead / 3600)} 小时后`;
    return `${Math.round(ahead / 86400)} 天后`;
  }
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.round(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 小时前`;
  return `${Math.round(diff / 86400)} 天前`;
}

function tsOf(text) {
  if (!text) return 0;
  return new Date(String(text).replace(' ', 'T')).getTime() / 1000;
}

function fmtPlan(ts) {
  if (!ts) return '无记录';
  const d = new Date(Number(ts) * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function deliverText(d) {
  if (!d) return '不投递';
  if (d === 'wecom') return '企业微信';
  if (d === 'local') return '仅本地';
  if (d.startsWith('discord')) return 'Discord';
  return d;
}

/* ---------- URL ---------- */

function readUrl() {
  const p = new URLSearchParams(location.hash.slice(1));
  const view = p.get('view');
  state.view = ['feed', 'board', 'jobs', 'apps'].includes(view) ? view : 'feed';
  state.job = p.get('job') || '';
  state.q = p.get('q') || '';
  state.unread = p.get('unread') === '1';
  state.silent = p.get('silent') === '1';
  state.page = Math.max(1, Number(p.get('page')) || 1);
  state.msg = p.get('msg') || '';
  state.anchorPending = Boolean(state.msg);
  state.until = null;
}

function writeUrl(mode) {
  const p = new URLSearchParams();
  if (state.view !== 'feed') p.set('view', state.view);
  if (state.job) p.set('job', state.job);
  if (state.q) p.set('q', state.q);
  if (state.unread) p.set('unread', '1');
  if (state.silent) p.set('silent', '1');
  if (state.page > 1) p.set('page', String(state.page));
  if (state.msg) p.set('msg', state.msg);
  const qs = p.toString();
  const url = location.pathname + (qs ? '#' + qs : '');
  if (mode === 'push') history.pushState(null, '', url);
  else history.replaceState(null, '', url);
}

function syncControls() {
  document.querySelectorAll('.tab').forEach((t) => {
    const on = t.dataset.view === state.view;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  $('chipUnread').classList.toggle('on', state.unread);
  $('chipSilent').classList.toggle('on', state.silent);

  const job = state.jobs.find((j) => j.id === state.job);
  const chip = $('chipJob');
  chip.classList.toggle('hidden', !state.job);
  chip.textContent = job ? job.name : state.job;
}

/* ---------- 概览 ---------- */

function renderJobs() {
  const el = $('jobList');
  const active = state.jobs.filter((j) => j.status !== 'orphan');
  const deleted = state.jobs.filter((j) => j.status === 'orphan');
  const sig = state.jobs.map((j) => [j.id, j.name, j.status, j.deleted ? 1 : 0].join(':')).join('|') +
    '|' + (state.deletedOpen ? 1 : 0) + '|' + state.job;
  if (sig === state.jobsSig) return;
  state.jobsSig = sig;

  const row = (job, extra) => {
    const meta = STATUS[job.status] || STATUS.ok;
    const unread = job.unread || 0;
    return `<li><button class="job-item${state.job === job.id ? ' active' : ''}${extra || ''}" data-job="${esc(job.id)}">
      <span class="status-dot ${meta.dot}"></span>
      <span class="jname">${esc(job.name)}</span>
      <span class="jnum${unread ? ' has' : ''}">${unread}</span>
    </button></li>`;
  };

  let html = `<li><button class="job-item${state.job === '' ? ' active' : ''}" data-job="">
    <span class="status-dot"></span><span class="jname">全部任务</span>
    <span class="jnum${state.stats.unread ? ' has' : ''}">${state.stats.unread || 0}</span>
  </button></li>`;
  html += active.map((j) => row(j)).join('');
  if (deleted.length) {
    html += `<li><button class="job-group" data-deleted-toggle="1" aria-expanded="${state.deletedOpen}">
      <span>已删除任务</span><span class="jnum">${deleted.length}</span>
    </button></li>`;
    if (state.deletedOpen) html += deleted.map((j) => row(j, ' is-deleted')).join('');
  }
  el.innerHTML = html;
  refreshJobCounts();
}

function refreshJobCounts() {
  const all = document.querySelector('#jobList .job-item[data-job=""] .jnum');
  if (all) {
    all.textContent = state.stats.unread || 0;
    all.classList.toggle('has', Boolean(state.stats.unread));
  }
  document.querySelectorAll('#jobList .job-item[data-job]').forEach((btn) => {
    const job = state.jobs.find((j) => j.id === btn.dataset.job);
    const num = btn.querySelector('.jnum');
    if (!job || !num) return;
    num.textContent = job.unread || 0;
    num.classList.toggle('has', Boolean(job.unread));
  });
}

function renderStats() {
  const s = state.stats;
  $('statContent').textContent = s.content || 0;
  $('statUnread').textContent = s.unread || 0;
  $('statAttention').textContent = state.health.attention || 0;

  const pill = $('unreadPill');
  pill.textContent = s.unread || 0;
  pill.classList.toggle('zero', !s.unread);

  const job = state.jobs.find((j) => j.id === state.job);
  const count = state.job ? (job ? job.unread || 0 : 0) : (s.unread || 0);
  $('fabCnt').textContent = count;
  $('fab').disabled = !count;
  $('fab').classList.toggle('hidden', state.view !== 'feed' && state.view !== 'board');
  $('fab').firstChild.nodeValue = state.job ? '把这个任务标为已读 ' : '全部标为已读 ';

  if (s.first_at && s.newest_at) {
    const a = tsOf(s.first_at);
    const b = tsOf(s.newest_at);
    $('statSpan').textContent = Math.max(1, Math.ceil((b - a) / 86400) + 1);
  }
  refreshJobCounts();
  renderSync(s);
}

function renderSync(s) {
  const btn = $('syncBtn');
  const dot = btn.querySelector('.sync-dot');
  const label = btn.querySelector('.sync-text');
  const last = s.last_sync || '';
  let cls = 'ok';
  let text = '未同步';
  if (last) {
    const age = Math.max(0, Date.now() / 1000 - tsOf(last));
    if (age <= 300) text = '同步 ' + last.slice(11, 16);
    else if (age <= 1800) { cls = 'warn'; text = Math.round(age / 60) + ' 分钟前'; }
    else { cls = 'bad'; text = Math.round(age / 3600) + ' 小时前'; }
  } else {
    cls = 'bad';
  }
  dot.className = 'sync-dot ' + cls;
  label.textContent = text;
  btn.title = last ? '上次同步 ' + last : '尚未同步';
}

function renderTasks() {
  const grid = $('jobsGrid');
  const h = state.health || {};
  const faults = (h.error || 0) + (h.stalled || 0);
  const paused = (h.paused || 0) + (h.nofile || 0);
  const summary = [
    `<span><b>${h.total || 0}</b> 个任务</span>`,
    `<span class="sep"></span>`,
    `<span><b class="${h.ok ? 'ok' : ''}">${h.ok || 0}</b> 正常</span>`,
    `<span><b class="${faults ? 'bad' : ''}">${faults}</b> 故障或停摆</span>`,
    `<span><b class="${h.quiet ? 'warn' : ''}">${h.quiet || 0}</b> 久无产出</span>`,
    h.orphan ? `<span><b>${h.orphan}</b> 已删除</span>` : '',
    paused ? `<span><b>${paused}</b> 暂停或无留档</span>` : '',
  ].join('');
  $('jobsSummary').innerHTML = summary;

  grid.innerHTML = state.jobs.map((j) => {
    const meta = STATUS[j.status] || STATUS.ok;
    const lastContent = tsOf(j.last_content_at);
    const err = j.last_error ? `<p class="jc-err">${esc(j.last_error)}</p>` : '';
    const deleted = j.status === 'orphan';
    const mode = deleted ? '' : (j.no_agent ? '脚本' : '智能体');
    const metaLine = deleted
      ? '已删除任务，无调度信息'
      : [j.schedule || '无调度', deliverText(j.deliver), mode,
        j.completed ? '已跑 ' + j.completed + ' 次' : ''].filter(Boolean).map(esc).join(' · ');
    const nextRun = j.next_run_ts || 0;
    const nextLine = deleted
      ? '不再调度'
      : j.status === 'stalled'
        ? (nextRun && nextRun * 1000 < Date.now()
          ? `计划 ${fmtPlan(nextRun)}，已逾期`
          : '已超过 2.5 个调度周期未运行')
        : `下次运行 ${rel(nextRun)}`;
    const path = j.script ? `<p class="jc-path">${esc(j.script)}</p>` : '';
    return `<article class="job-card${meta.cls ? ' ' + meta.cls : ''}">
      <div class="jc-top">
        <span class="status-dot ${meta.dot}"></span>
        <h3>${esc(j.name)}</h3>
        <span class="jc-badge ${meta.cls}">${meta.label}</span>
      </div>
      <p class="jc-line">${metaLine}</p>
      <div class="jc-metrics">
        <span><b>${j.runs24 || 0}</b>24 小时运行</span>
        <span><b>${j.content24 || 0}</b>24 小时产出</span>
        <span><b>${j.archived || 0}</b>累计产出</span>
      </div>
      <p class="jc-line">最近运行 ${deleted ? '无记录' : rel(j.last_run_ts)} · 最近产出 ${lastContent ? rel(lastContent) : (j.archived ? '仅静默轮次' : '无留档')}</p>
      <p class="jc-line">${nextLine}</p>
      ${path}
      ${err}
      <div class="jc-actions">
        <button class="btn-sm" data-job="${esc(j.id)}">查看消息</button>
      </div>
    </article>`;
  }).join('');
}

function renderApps() {
  const grid = $('appsGrid');
  const sig = state.apps.map((a) => [a.key, a.state, a.online, a.file_count, a.latest, a.error, a.health_code].join(':')).join('|');
  if (sig === state.appsSig) return;
  state.appsSig = sig;
  grid.innerHTML = '';

  for (const app of state.apps) {
    const card = document.createElement('article');
    card.className = 'app-card';
    let badge = '内容';
    let badgeCls = '';
    let meta = '';
    let metaCls = '';
    let actions = '';

    if (app.type === 'service') {
      if (app.state === 'degraded') { badge = '接口异常'; badgeCls = 'bad'; }
      else if (app.state === 'online' || app.online) { badge = '在线'; badgeCls = 'on'; }
      else { badge = '未启动'; }
      if (app.state === 'degraded') {
        meta = app.health_code ? '健康检查 HTTP ' + app.health_code : '健康检查无响应';
        metaCls = ' bad-text';
      }
      if (app.state === 'offline') {
        actions = `<button class="btn-sm" data-hint="${esc(app.key)}">启动命令</button>
          <code class="app-hint hidden" id="hint-${esc(app.key)}">${esc(app.start_hint || '')}</code>`;
      } else {
        actions = `<button class="btn-sm primary" data-open="${esc(app.key)}">打开</button>`;
      }
    } else {
      const count = app.file_count || 0;
      if (app.exists === false) {
        meta = '目录不存在';
        metaCls = ' bad-text';
        badgeCls = 'bad';
        badge = '路径失效';
      } else if (!count) {
        meta = '空目录';
      } else {
        meta = count + ' 个文件' + (app.latest ? ' · 最近更新 ' + app.latest.slice(5) : '');
        actions = `<button class="btn-sm primary" data-files="${esc(app.key)}">浏览</button>`;
      }
    }

    card.innerHTML = `
      <div class="app-top">
        <span class="app-name">${esc(app.name)}</span>
        <span class="app-badge ${badgeCls}">${badge}</span>
      </div>
      <p class="app-desc">${esc(app.desc || '')}</p>
      ${meta ? `<p class="app-meta${metaCls}">${esc(meta)}</p>` : ''}
      <div class="app-actions">${actions}</div>`;
    grid.appendChild(card);
  }

  grid.querySelectorAll('[data-open]').forEach((btn) => {
    btn.onclick = () => {
      const app = state.apps.find((a) => a.key === btn.dataset.open);
      if (app) openIframe(app.url, app.name);
    };
  });
  grid.querySelectorAll('[data-hint]').forEach((btn) => {
    btn.onclick = () => {
      const hint = $('hint-' + btn.dataset.hint);
      if (hint) hint.classList.toggle('hidden');
    };
  });
  grid.querySelectorAll('[data-files]').forEach((btn) => {
    btn.onclick = () => {
      const app = state.apps.find((a) => a.key === btn.dataset.files);
      if (app) openFiles(app);
    };
  });
}

async function loadOverview() {
  const data = await api('/api/overview');
  state.stats = data.stats;
  state.jobs = data.jobs;
  state.health = data.health;
  state.apps = data.apps;
  renderStats();
  renderJobs();
  syncControls();
  if (state.view === 'jobs') renderTasks();
  if (state.view === 'apps') renderApps();

  if (state.lastContent >= 0 && data.stats.content > state.lastContent) {
    const diff = data.stats.content - state.lastContent;
    if (state.page === 1 && !state.q && !state.job && !state.unread) {
      $('newBarBtn').textContent = `${diff} 条新消息`;
      $('newBar').classList.remove('hidden');
    }
  }
  state.lastContent = data.stats.content;
}

/* ---------- 图片工具 ---------- */

function extractLinks(body) {
  const out = [];
  const re = /\[(\d+)[.、]\s*([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
  let m;
  while ((m = re.exec(body || '')) !== null) {
    out.push({ title: m[2].trim(), url: m[3] });
  }
  return out;
}

function saveImage(path) {
  const name = path.split('/').pop();
  const a = document.createElement('a');
  a.href = '/media?path=' + encodeURIComponent(path);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast('已开始保存 ' + name);
}

function shareToX(ctx) {
  if (!ctx) return;
  const params = new URLSearchParams();
  if (ctx.title) params.set('text', ctx.title);
  if (ctx.url) params.set('url', ctx.url);
  if (!ctx.title && !ctx.url) return;
  window.open('https://x.com/intent/tweet?' + params.toString(), '_blank', 'noopener');
}

function openCtxMenu(x, y, ctx) {
  const menu = $('ctxMenu');
  state.ctxTarget = ctx;
  const hasShare = Boolean(ctx && (ctx.url || ctx.title));
  $('ctxShare').classList.toggle('hidden', !hasShare);
  $('ctxSave').classList.toggle('hidden', !ctx || !ctx.ok);
  menu.classList.remove('hidden');
  const w = menu.offsetWidth || 150;
  const h = menu.offsetHeight || 76;
  menu.style.left = Math.min(x, window.innerWidth - w - 8) + 'px';
  menu.style.top = Math.min(y, window.innerHeight - h - 8) + 'px';
  $('ctxSave').focus();
}

function closeCtxMenu() {
  $('ctxMenu').classList.add('hidden');
  state.ctxTarget = null;
}

/* ---------- 消息流 ---------- */

const IMAGE_RE = /\.(jpe?g|png|gif|webp)$/i;

function imageCtx(m, idx) {
  const links = extractLinks(m.body);
  const hit = links[idx] || {};
  const missing = (m.media_missing || [])[idx];
  return { path: m.media[idx], title: hit.title || '', url: hit.url || '', ok: !missing };
}

function ctxAttrs(m, idx) {
  const p = m.media[idx];
  const ctx = imageCtx(m, idx);
  return `data-ctx="${esc(JSON.stringify(ctx))}"`;
}

function figHtml(m, idx, cls) {
  const p = m.media[idx];
  const ctx = imageCtx(m, idx);
  if (!ctx.ok) {
    return `<figure class="${cls} miss" ${ctxAttrs(m, idx)}></figure>`;
  }
  return `<figure class="${cls}" ${ctxAttrs(m, idx)}>
    <img loading="lazy" decoding="async" src="/media?path=${encodeURIComponent(p)}" alt="">
  </figure>`;
}

function mediaHtml(media, m) {
  if (!media || !media.length) return '';
  const imgs = media.map((p, i) => ({ p, i })).filter((x) => IMAGE_RE.test(x.p));
  const docs = media.filter((p) => !IMAGE_RE.test(p));
  const figs = imgs.map((x) => figHtml(m, x.i, 'thumb')).join('');
  const chips = docs.map((p) => {
    const name = p.split('/').pop();
    return `<button class="media-doc" data-path="${encodeURIComponent(p)}" data-name="${esc(name)}"
      title="${esc(name)}"><span class="doc-tag">附件</span><span class="doc-name">${esc(name)}</span></button>`;
  }).join('');
  return `${figs ? `<div class="media-grid">${figs}</div>` : ''}${chips ? `<div class="media-docs">${chips}</div>` : ''}`;
}

let boardItems = [];

function boardColumns() {
  const byWidth = Math.floor((window.innerWidth - 64) / 240);
  return Math.max(2, Math.min(6, byWidth));
}

function renderBoard(items) {
  boardItems = items;
  renderBoardCols();
  renderPager(boardPagerData);
}

function renderBoardCols() {
  const el = $('timeline');
  const cols = boardColumns();
  const buckets = Array.from({ length: cols }, () => '');
  let n = 0;
  for (const m of boardItems) {
    m.media.forEach((p, idx) => {
      if (!IMAGE_RE.test(p)) return;
      const ctx = imageCtx(m, idx);
      const dataCtx = esc(JSON.stringify({ path: p, title: ctx.title, url: ctx.url }));
      const img = ctx.ok
        ? `<img loading="lazy" decoding="async" src="/media?path=${encodeURIComponent(p)}" alt="">`
        : '';
      buckets[n % cols] += `<figure class="pin${m.unread ? ' unread' : ''}${ctx.ok ? '' : ' miss'}" ${ctxAttrs(m, idx)}>
        ${img}
        <figcaption>
          <span class="pin-title">${esc(ctx.title || m.job_name)}</span>
          <span class="pin-time">${fmtClock(m.run_at)}</span>
        </figcaption>
      </figure>`;
      n += 1;
    });
  }
  el.innerHTML = '<div class="board">' +
    buckets.map((h) => `<div class="board-col">${h}</div>`).join('') + '</div>';
  el.querySelectorAll('.pin img').forEach((img) => {
    const fig = img.closest('.pin');
    const ctx = JSON.parse(fig.dataset.ctx);
    img.addEventListener('error', () => {
      fig.classList.add('miss');
      img.remove();
    });
    img.addEventListener('click', () => openLightbox(img.src, ctx));
  });
  renderPager(boardPagerData);
}

function renderTimeline(data) {
  const el = $('timeline');
  if (state.view === 'board') {
    boardPagerData = data;
    renderBoard(data.items);
    return;
  }
  if (!data.items.length) {
    if (data.silent_total > 0 && !state.silent) {
      el.innerHTML = `<div class="empty"><span class="big">这个任务有 ${data.silent_total} 条静默轮次</span>
        <button class="btn-sm" id="showSilent">查看静默轮次</button></div>`;
      const btn = $('showSilent');
      if (btn) btn.onclick = () => {
        state.silent = true;
        state.page = 1;
        state.until = null;
        syncControls();
        writeUrl('replace');
        loadMessages();
      };
    } else {
      el.innerHTML = `<div class="empty"><span class="big">没有消息</span></div>`;
    }
    $('pager').innerHTML = '';
    return;
  }
  let html = '';
  let lastDay = '';
  for (const m of data.items) {
    const key = dayKey(m.run_at);
    if (key !== lastDay) {
      html += `<div class="day-mark">${dayLabel(key)}</div>`;
      lastDay = key;
    }
    const cls = ['msg'];
    if (m.unread) cls.push('unread');
    if (m.silent) cls.push('silent');
    const body = m.body ? renderMarkdown(m.body) : '<p class="none">本轮无输出</p>';
    html += `
      <article class="${cls.join(' ')}" data-id="${m.id}" id="msg-${m.id}">
        <div class="msg-head">
          <span class="msg-job">${esc(m.job_name)}</span>
          ${m.mode && m.mode.includes('no_agent') ? '<span class="msg-tag">脚本</span>' : ''}
          <a class="msg-time" href="#msg=${m.id}" data-msg="${m.id}" title="复制链接">${fmtClock(m.run_at)}</a>
          <button class="msg-copy" title="复制正文">复制</button>
        </div>
        <div class="msg-body">${body}${mediaHtml(m.media, m)}</div>
      </article>`;
  }
  el.innerHTML = html;
  state.focusIdx = -1;
  decorateTimeline();
  renderPager(data);
}

function decorateTimeline() {
  const el = $('timeline');
  el.querySelectorAll('.msg-body').forEach((body) => {
    const text = body.textContent.trim();
    if (text.length > 700 || body.scrollHeight > 460) {
      body.classList.add('clamped');
      const btn = document.createElement('button');
      btn.className = 'more-btn';
      btn.textContent = `展开全文（${text.length} 字）`;
      btn.onclick = () => {
        const on = body.classList.toggle('clamped');
        btn.textContent = on ? `展开全文（${text.length} 字）` : '收起';
      };
      body.after(btn);
    }
    body.querySelectorAll('img').forEach((img) => {
      const fail = () => {
        img.parentNode.classList.add('miss');
        img.remove();
      };
      img.addEventListener('error', fail);
      if (img.complete && img.naturalWidth === 0) fail();
      img.addEventListener('click', () => {
        const fig = img.closest('[data-ctx]');
        const ctx = fig ? JSON.parse(fig.dataset.ctx) : null;
        openLightbox(img.src, ctx);
      });
    });
  });
  highlightQuery();
}

function highlightQuery() {
  const q = state.q.trim();
  const root = $('timeline');
  if (!q || !root) return;
  const needle = q.toLowerCase();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const targets = [];
  let node;
  while ((node = walker.nextNode())) {
    const tag = node.parentElement ? node.parentElement.tagName : '';
    if (['MARK', 'SCRIPT', 'STYLE', 'PRE', 'CODE'].includes(tag)) continue;
    if (node.nodeValue.toLowerCase().includes(needle)) targets.push(node);
  }
  for (const t of targets) {
    const frag = document.createDocumentFragment();
    let rest = t.nodeValue;
    let low = rest.toLowerCase();
    let idx;
    while ((idx = low.indexOf(needle)) !== -1) {
      if (idx) frag.appendChild(document.createTextNode(rest.slice(0, idx)));
      const mark = document.createElement('mark');
      mark.textContent = rest.slice(idx, idx + needle.length);
      frag.appendChild(mark);
      rest = rest.slice(idx + needle.length);
      low = rest.toLowerCase();
    }
    if (rest) frag.appendChild(document.createTextNode(rest));
    t.parentNode.replaceChild(frag, t);
  }
}

function pageNumbers(page, pages) {
  const out = [];
  const push = (n) => { if (!out.includes(n)) out.push(n); };
  push(1);
  for (let n = page - 1; n <= page + 1; n++) if (n > 1 && n < pages) push(n);
  if (pages > 1) push(pages);
  out.sort((a, b) => a - b);
  const withGaps = [];
  let prev = 0;
  for (const n of out) {
    if (prev && n - prev > 1) withGaps.push('…');
    withGaps.push(n);
    prev = n;
  }
  return withGaps;
}

function renderPager(data) {
  const pager = $('pager');
  if (!data.total) { pager.innerHTML = ''; return; }
  const btns = pageNumbers(data.page, data.pages).map((n) => {
    if (n === '…') return '<span class="pg-gap">…</span>';
    return `<button class="pg ${n === data.page ? 'on' : ''}" data-page="${n}">${n}</button>`;
  }).join('');
  pager.innerHTML = `
    <span>共 ${data.total} 条 · 第 ${data.page} / ${data.pages} 页</span>
    <div class="pager-btns">
      <button class="pg" data-page="${data.page - 1}" ${data.page <= 1 ? 'disabled' : ''}>上一页</button>
      ${btns}
      <button class="pg" data-page="${data.page + 1}" ${data.page >= data.pages ? 'disabled' : ''}>下一页</button>
    </div>`;
  pager.querySelectorAll('[data-page]').forEach((btn) => {
    btn.onclick = () => {
      const p = Number(btn.dataset.page);
      if (!p || p === data.page) return;
      state.page = p;
      state.msg = '';
      writeUrl('push');
      loadMessages().then(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    };
  });
}

async function loadMessages() {
  const seq = ++state.reqSeq;
  state.loading = true;
  if (state.page !== 1 || state.job || state.q || state.unread) {
    $('newBar').classList.add('hidden');
  }
  const el = $('timeline');
  if (!el.querySelector('.msg, .empty, .skeleton')) {
    el.innerHTML = '<div class="skeleton"><div class="sk"></div><div class="sk"></div><div class="sk"></div></div>';
  }
  el.setAttribute('aria-busy', 'true');
  const qs = new URLSearchParams({
    page: state.page, size: state.size, job: state.job, q: state.q,
    unread: state.unread ? '1' : '0', silent: state.silent ? '1' : '0',
  });
  if (state.until) qs.set('until', String(state.until));
  if (state.msg && state.anchorPending) qs.set('anchor', state.msg);
  try {
    const data = await api('/api/messages?' + qs.toString());
    if (seq !== state.reqSeq) return;
    const anchored = state.msg && state.anchorPending;
    if (anchored) {
      state.page = data.page;
      state.anchorPending = false;
      writeUrl('replace');
    }
    if (!state.until && data.page === 1 && data.items.length) {
      state.until = data.items[0].run_ts;
    }
    state.stats = data.stats;
    renderStats();
    syncControls();
    renderTimeline(data);
    if (anchored) {
      const target = $('msg-' + state.msg);
      if (target) {
        target.classList.add('focus');
        target.scrollIntoView({ block: 'center' });
      }
    }
  } catch (err) {
    if (seq !== state.reqSeq) return;
    el.innerHTML = `<div class="empty"><span class="big">读取失败</span>${esc(err.message)}</div>`;
    $('pager').innerHTML = '';
  } finally {
    if (seq === state.reqSeq) {
      state.loading = false;
      el.removeAttribute('aria-busy');
    }
  }
}

/* ---------- 抽屉 ---------- */

function closeDrawer() {
  $('drawer').classList.remove('open');
  $('mask').classList.remove('open');
  setTimeout(() => { $('drawerBody').innerHTML = ''; }, 260);
  state.drawerUrl = '';
}

function openIframe(url, title) {
  state.drawerMode = 'iframe';
  state.drawerUrl = url;
  $('drawerTitle').textContent = title;
  $('drawerOpen').classList.remove('hidden');
  const body = $('drawerBody');
  body.className = 'drawer-body';
  body.innerHTML = `<iframe src="${url}" title="${esc(title)}"></iframe>`;
  $('drawer').classList.add('open');
  $('mask').classList.add('open');
  $('drawerClose').focus();
}

async function openFiles(app) {
  state.drawerMode = 'files';
  state.drawerUrl = '';
  $('drawerTitle').textContent = app.name;
  $('drawerOpen').classList.add('hidden');
  const body = $('drawerBody');
  body.className = 'drawer-body';
  body.innerHTML = '<div class="skeleton"><div class="sk"></div><div class="sk"></div></div>';
  $('drawer').classList.add('open');
  $('mask').classList.add('open');
  try {
    const data = await api(`/api/apps/${encodeURIComponent(app.key)}/files`);
    if (!data.files || !data.files.length) {
      body.innerHTML = `<div class="empty"><span class="big">没有可读文件</span>${esc(data.error || '')}</div>`;
      return;
    }
    const items = data.files.map((f) =>
      `<li><button class="file-row" data-path="${encodeURIComponent(f.path)}" data-name="${esc(f.rel || f.name)}">
        <span class="fname">${esc(f.rel || f.name)}</span>
        <span class="fmeta">${Math.max(1, Math.round(f.size / 1024))} KB · ${f.mtime.slice(5)}</span>
      </button></li>`).join('');
    body.innerHTML = `<ul class="file-list">${items}</ul>`;
    body.querySelectorAll('.file-row').forEach((row) => {
      row.onclick = () => openReader(decodeURIComponent(row.dataset.path), row.dataset.name);
    });
  } catch (err) {
    body.innerHTML = `<div class="empty"><span class="big">读取失败</span>${esc(err.message)}</div>`;
  }
}

async function openReader(path, name) {
  state.drawerMode = 'reader';
  $('drawerTitle').textContent = name;
  $('drawerOpen').classList.add('hidden');
  const body = $('drawerBody');
  body.className = 'drawer-body reading';
  body.innerHTML = '<div class="skeleton"><div class="sk"></div><div class="sk"></div><div class="sk"></div></div>';
  try {
    const data = await api('/api/file?path=' + encodeURIComponent(path));
    const isMd = /\.md$/i.test(data.name);
    body.innerHTML = `<div class="doc">${isMd ? renderMarkdown(data.content) : `<pre>${esc(data.content)}</pre>`}</div>`;
    body.scrollTop = 0;
  } catch (err) {
    body.innerHTML = `<div class="empty"><span class="big">读取失败</span>${esc(err.message)}</div>`;
  }
}

/* ---------- 灯箱 ---------- */

function openLightbox(src, ctx) {
  const box = $('lightbox');
  box.querySelector('img').src = src;
  state.lightboxCtx = ctx || null;
  $('lbShare').classList.toggle('hidden', !(ctx && (ctx.url || ctx.title)));
  $('lbSave').classList.toggle('hidden', !ctx || !ctx.ok);
  box.classList.remove('hidden');
}

function closeLightbox() {
  const box = $('lightbox');
  box.classList.add('hidden');
  box.querySelector('img').removeAttribute('src');
  state.lightboxCtx = null;
}

/* ---------- 已读确认 ---------- */

function openConfirm() {
  const job = state.jobs.find((j) => j.id === state.job);
  const count = state.job ? (job ? job.unread || 0 : 0) : (state.stats.unread || 0);
  if (!count) return;
  state.confirmJob = state.job;
  $('modalText').textContent = state.job
    ? `${job ? job.name : '这个任务'}的 ${count} 条消息将不再显示未读标记。`
    : `${count} 条消息将不再显示未读标记。`;
  $('modalMask').classList.remove('hidden');
  $('modalCancel').focus();
}

function closeConfirm() {
  $('modalMask').classList.add('hidden');
  state.confirmJob = '';
}

async function doRead() {
  const job = state.confirmJob;
  closeConfirm();
  try {
    const data = await api('/api/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job }),
    });
    toast(job ? '这个任务已全部标为已读' : `已标为已读，剩余 ${data.total_unread} 条未读`);
    state.until = null;
    $('newBar').classList.add('hidden');
    await loadOverview();
    await loadMessages();
  } catch (err) {
    toast('标记失败：' + err.message);
  }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  toast('已复制正文');
}

/* ---------- 视图 ---------- */

function showView() {
  document.body.dataset.view = state.view;
  document.body.classList.toggle('board', state.view === 'board');
  $('viewFeed').classList.toggle('hidden', state.view !== 'feed' && state.view !== 'board');
  $('viewJobs').classList.toggle('hidden', state.view !== 'jobs');
  $('viewApps').classList.toggle('hidden', state.view !== 'apps');
  $('fab').classList.toggle('hidden', state.view !== 'feed' && state.view !== 'board');
}

function goView(view, push) {
  if (view === state.view) return;
  state.view = view;
  if (view !== 'feed') {
    state.page = 1;
    state.msg = '';
  } else {
    state.until = null;
  }
  showView();
  syncControls();
  writeUrl(push ? 'push' : 'replace');
  if (view === 'feed' || view === 'board') loadMessages();
  if (view === 'jobs') renderTasks();
  if (view === 'apps') renderApps();
  window.scrollTo({ top: 0 });
}

/* ---------- 事件 ---------- */

function bind() {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.onclick = () => goView(tab.dataset.view, true);
  });

  $('jobList').addEventListener('click', (e) => {
    const group = e.target.closest('[data-deleted-toggle]');
    if (group) {
      state.deletedOpen = !state.deletedOpen;
      renderJobs();
      return;
    }
    const btn = e.target.closest('.job-item');
    if (!btn) return;
    state.job = btn.dataset.job;
    state.page = 1;
    state.msg = '';
    state.until = null;
    renderJobs();
    writeUrl('push');
    loadMessages();
  });

  let timer;
  $('searchInput').oninput = (e) => {
    clearTimeout(timer);
    const v = e.target.value.trim();
    timer = setTimeout(() => {
      state.q = v;
      state.page = 1;
      state.msg = '';
      state.until = null;
      writeUrl('replace');
      loadMessages();
    }, 260);
  };

  $('chipUnread').onclick = () => {
    state.unread = !state.unread;
    state.page = 1;
    state.until = null;
    syncControls();
    writeUrl('replace');
    loadMessages();
  };

  $('chipSilent').onclick = () => {
    state.silent = !state.silent;
    state.page = 1;
    state.until = null;
    syncControls();
    writeUrl('replace');
    loadMessages();
  };

  $('chipJob').onclick = () => {
    state.job = '';
    state.page = 1;
    state.until = null;
    renderJobs();
    writeUrl('replace');
    loadMessages();
  };

  $('newBarBtn').onclick = () => {
    $('newBar').classList.add('hidden');
    state.until = null;
    state.page = 1;
    state.msg = '';
    writeUrl('replace');
    loadOverview().then(loadMessages);
  };

  $('themeBtn').onclick = () => {
    applyTheme(document.documentElement.dataset.theme === 'gray' ? 'brand' : 'gray', true);
  };
  $('syncBtn').onclick = manualSync;
  $('fab').onclick = openConfirm;
  $('modalCancel').onclick = closeConfirm;
  $('modalOk').onclick = doRead;
  $('modalMask').onclick = (e) => { if (e.target === $('modalMask')) closeConfirm(); };

  $('drawerClose').onclick = closeDrawer;
  $('mask').onclick = closeDrawer;
  $('drawerOpen').onclick = () => {
    if (state.drawerMode === 'iframe' && state.drawerUrl) window.open(state.drawerUrl, '_blank');
  };
  $('lightbox').onclick = closeLightbox;
  $('lightboxClose').onclick = (e) => { e.stopPropagation(); closeLightbox(); };
  $('lbSave').onclick = (e) => {
    e.stopPropagation();
    if (state.lightboxCtx) saveImage(state.lightboxCtx.path);
  };
  $('lbShare').onclick = (e) => {
    e.stopPropagation();
    shareToX(state.lightboxCtx);
  };

  $('ctxSave').onclick = () => {
    if (state.ctxTarget) saveImage(state.ctxTarget.path);
    closeCtxMenu();
  };
  $('ctxShare').onclick = () => {
    shareToX(state.ctxTarget);
    closeCtxMenu();
  };
  document.addEventListener('click', (e) => {
    if (!$('ctxMenu').classList.contains('hidden') && !e.target.closest('#ctxMenu')) {
      closeCtxMenu();
    }
  });

  $('jobsGrid').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-job]');
    if (!btn) return;
    state.job = btn.dataset.job;
    state.page = 1;
    state.msg = '';
    state.until = null;
    state.view = 'feed';
    showView();
    renderJobs();
    syncControls();
    writeUrl('push');
    loadMessages();
    window.scrollTo({ top: 0 });
  });

  $('timeline').addEventListener('contextmenu', (e) => {
    const fig = e.target.closest('[data-ctx]');
    if (!fig) return;
    e.preventDefault();
    try {
      openCtxMenu(e.clientX, e.clientY, JSON.parse(fig.dataset.ctx));
    } catch (err) {
      openCtxMenu(e.clientX, e.clientY, null);
    }
  });

  $('timeline').addEventListener('click', (e) => {
    const doc = e.target.closest('.media-doc');
    if (doc) {
      openReader(decodeURIComponent(doc.dataset.path), doc.dataset.name);
      return;
    }
    const copy = e.target.closest('.msg-copy');
    if (copy) {
      const card = copy.closest('.msg');
      const body = card && card.querySelector('.msg-body');
      if (body) copyText(body.innerText.trim());
      return;
    }
    const time = e.target.closest('.msg-time');
    if (time) {
      e.preventDefault();
      state.msg = time.dataset.msg;
      state.anchorPending = false;
      writeUrl('push');
      const card = time.closest('.msg');
      if (card) {
        document.querySelectorAll('.msg.focus').forEach((m) => m.classList.remove('focus'));
        card.classList.add('focus');
      }
    }
  });

  document.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'textarea' || e.target.isContentEditable;
    if (e.key === 'Escape') {
      if (!$('ctxMenu').classList.contains('hidden')) return closeCtxMenu();
      if (!$('modalMask').classList.contains('hidden')) return closeConfirm();
      if (!$('lightbox').classList.contains('hidden')) return closeLightbox();
      if ($('drawer').classList.contains('open')) return closeDrawer();
      return;
    }
    if (typing) return;
    if (e.key === '/') {
      e.preventDefault();
      $('searchInput').focus();
      return;
    }
    if (e.key === 'r') {
      state.until = null;
      loadOverview().then(loadMessages);
      return;
    }
    if (e.key === 'j' || e.key === 'k') {
      const items = Array.from(document.querySelectorAll('.msg'));
      if (!items.length) return;
      const dir = e.key === 'j' ? 1 : -1;
      state.focusIdx = Math.max(0, Math.min(items.length - 1, state.focusIdx + dir));
      items.forEach((it) => it.classList.remove('focus'));
      items[state.focusIdx].classList.add('focus');
      items[state.focusIdx].scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  });

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (state.view === 'board') renderBoardCols();
    }, 200);
  });

  window.addEventListener('popstate', () => {
    readUrl();
    $('searchInput').value = state.q;
    showView();
    syncControls();
    if (state.view === 'feed' || state.view === 'board') loadMessages();
    if (state.view === 'jobs') renderTasks();
    if (state.view === 'apps') renderApps();
  });
}

async function manualSync() {
  if (state.syncing) return;
  state.syncing = true;
  $('syncBtn').classList.add('busy');
  try {
    const data = await api('/api/sync', { method: 'POST' });
    toast(data.added ? `新增 ${data.added} 条消息` : '没有新消息');
    state.until = null;
    $('newBar').classList.add('hidden');
    await loadOverview();
    if (state.view === 'feed' || state.view === 'board') await loadMessages();
  } catch (err) {
    toast('同步失败：' + err.message);
  } finally {
    state.syncing = false;
    $('syncBtn').classList.remove('busy');
  }
}

async function boot() {
  applyTheme(document.documentElement.dataset.theme || 'brand', false);
  bind();
  readUrl();
  $('searchInput').value = state.q;
  showView();
  syncControls();
  try {
    await loadOverview();
    if (state.view === 'feed' || state.view === 'board') await loadMessages();
  } catch (err) {
    const box = state.view === 'apps' ? $('appsGrid') : state.view === 'jobs' ? $('jobsGrid') : $('timeline');
    box.innerHTML = `<div class="empty"><span class="big">读取失败</span>${esc(err.message)}<button class="btn-sm" id="retryBoot">重试</button></div>`;
    const retry = $('retryBoot');
    if (retry) retry.onclick = () => location.reload();
  }
  setInterval(async () => {
    if (document.hidden) return;
    try { await loadOverview(); } catch (e) { /* 轮询失败不打扰 */ }
  }, 30000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) loadOverview().catch(() => {});
  });
}

boot();
