/* pi-hub UI. Plain JS, no build step. Renders /api/state and edits /api/config. */
'use strict';

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const INFO_SVG = '<svg width="16" height="16" viewBox="0 0 256 256" fill="currentColor" aria-hidden="true"><path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm16-40a8,8,0,0,1-8,8,16,16,0,0,1-16-16V128a8,8,0,0,1,0-16,16,16,0,0,1,16,16v40A8,8,0,0,1,144,176ZM112,84a12,12,0,1,1,12,12A12,12,0,0,1,112,84Z"/></svg>';
const STAR = filled => `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="${filled ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>`;
const MORE_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>';
const host = location.hostname;
const TOKEN_KEY = 'pihub-token';
let data = null, cat = 'All', query = '', selected = null;

/* ── helpers ── */
function urlOf(s) {
  if (s.url) return s.url;
  if (!s.port) return null;
  const defaultPort = (s.scheme === 'https' && s.port === '443') || (s.scheme !== 'https' && s.port === '80');
  return `${s.scheme || 'http'}://${host}${defaultPort ? '' : ':' + s.port}${s.linkPath || ''}`;
}
const portLabel = s => s.port ? ':' + s.port : (s.cron ? 'cron' : '↗');
const dotCls = s => s.cron ? '' : s.up === null ? 'wait' : s.up ? '' : 'down';
const matches = s => { const q = query.trim().toLowerCase(); return !q || `${s.name} ${s.port} ${s.description}`.toLowerCase().includes(q); };
const visible = () => data.services.filter(s => (cat === 'All' || s.category === cat) && matches(s));
const pct = n => Math.max(0, Math.min(100, n)).toFixed(0);

/* ── dashboard ── */
function renderHeader() {
  $('#title').textContent = data.title;
  $('#host').textContent = $('#host2').textContent = host;
  $('#uptime').textContent = data.sys.uptime || '—';
  const down = data.services.filter(s => !s.cron && s.up === false).length;
  $('#summary').textContent = `${data.services.length} services · ${down} offline`;
  $('#sumdot').className = 'dot' + (down ? ' down' : '');
  document.title = down ? `(${down} down) ${data.title}` : data.title;
}

function renderStats() {
  const cards = data.sys.cards || [];
  $('#stats').hidden = !cards.length;
  $('#stats').innerHTML = cards.map(c => `
    <div class="stat${c.warn ? ' warn' : ''}"><div class="label" title="${esc(c.label)}">${esc(c.label)}</div>
      <div class="v"><span>${esc(c.value)}</span><span>${esc(c.sub)}</span></div>
      <div class="meter"><i style="width:${Math.max(2, pct(c.pct))}%"></i></div></div>`).join('');
}

function linkAttrs(s) {
  const u = urlOf(s);
  return u ? `href="${esc(u)}" target="_blank" rel="noopener noreferrer"` : `href="#" data-info="${esc(s.id)}"`;
}

function renderPinned() {
  const pins = data.services.filter(s => s.pinned && matches(s));
  $('#pinned').innerHTML = pins.map(s => `
    <a class="pin${s.up === false ? ' off' : ''}" ${linkAttrs(s)}>
      <div class="h"><span>${esc(s.name)}</span><i class="dot ${dotCls(s)}"></i></div>
      <div class="big">${esc(portLabel(s))}</div>
      <div class="s ell">${esc(s.stat)}</div></a>`).join('');
  $('#pinned-label').hidden = $('#pinned').hidden = !pins.length;
}

function renderCats() {
  $('#cats').innerHTML = ['All', ...data.categories].map(c => {
    const n = c === 'All' ? data.services.length : data.services.filter(s => s.category === c).length;
    return n || c === 'All' ? `<button data-cat="${esc(c)}" aria-pressed="${c === cat}">${esc(c)}<span>${n}</span></button>` : '';
  }).join('');
}

function renderGrid() {
  const list = visible();
  $('#grid').innerHTML = list.map(s => `
    <a class="card${s.up === false ? ' off' : ''}" ${linkAttrs(s)}>
      <div class="r1">
        <span class="mono-badge" aria-hidden="true">${esc(s.icon)}</span>
        <div class="id"><div>${esc(s.name)}<i class="dot pdot ${dotCls(s)}"></i></div><div class="ell desc">${esc(s.description)}</div><div class="phone-stat ell">${esc(s.stat)}</div></div>
        <i class="dot ${dotCls(s)}" title="${esc(s.state)}"></i>
        <button class="info" data-info="${esc(s.id)}" title="Details" aria-label="${esc(s.name)} details">${INFO_SVG}</button>
      </div>
      <div class="r2"><span class="ell">${esc(s.stat)}</span><span class="port">${esc(portLabel(s))}</span></div>
    </a>`).join('');
  $('#empty').hidden = list.length > 0;
  $('#empty').textContent = `No service matches “${query}”.`;
}

function renderSide() {
  const sections = [];
  if (data.playing) {
    const rows = data.playing.length ? data.playing.map(x => `
      <div class="row"><div class="t ell">${esc(x.title)}</div><div class="w ell">${esc(x.who)} · ${esc(x.how)}${x.state === 'paused' ? ' · paused' : ''}</div>
      <div class="thin"><i style="width:${pct(x.pct)}%"></i></div></div>`).join('') : '<div class="note">Nothing playing right now.</div>';
    sections.push(`<section><h2 class="label">Now playing · ${data.playing.length}</h2>${rows}</section>`);
  }
  if (data.downloads) {
    const d = data.downloads;
    const rows = d.items.length ? d.items.map(x => `
      <div class="q"><div class="h"><span class="ell" title="${esc(x.name)}">${esc(x.name)}</span><span class="muted">${pct(x.pct)}%</span></div>
      <div class="thin"><i style="width:${pct(x.pct)}%"></i></div></div>`).join('')
      + (d.total > d.items.length ? `<div class="note">+ ${d.total - d.items.length} more in queue</div>` : '')
      : '<div class="note">Queue empty.</div>';
    sections.push(`<section><h2 class="label">Downloads · ${esc(d.speed)}</h2>${rows}</section>`);
  }
  if (data.schedule.length) {
    sections.push(`<section><h2 class="label">Scheduled</h2>${data.schedule.map(x =>
      `<div class="sched"${x.note ? ` title="${esc(x.note)}"` : ''}><span>${esc(x.name)}</span><span class="muted">${esc(x.when)}</span></div>`).join('')}</section>`);
  }
  $('#side').hidden = !sections.length;
  $('#side').innerHTML = sections.join('');
  document.querySelector('.dash').classList.toggle('no-side', !sections.length);
}

function renderModal() {
  const m = $('#modal'), s = selected && data.services.find(x => x.id === selected);
  if (!s) { m.hidden = true; m.innerHTML = ''; return; }
  const u = urlOf(s), max = Math.max(1, ...s.bars.filter(b => b.ms != null).map(b => b.ms));
  const bars = s.bars.map(b => `<i class="${b.state}" style="height:${b.ms != null ? Math.max(8, b.ms / max * 100) : b.state === 'down' ? 100 : 6}%" title="${b.ms != null ? b.ms + ' ms' : b.state === 'down' ? 'offline' : 'no data'}"></i>`).join('');
  const job = data.schedule.find(x => x.service === s.id || x.name === s.name);
  const keepFocus = document.activeElement?.dataset?.copy !== undefined;
  m.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="mname">
      <div class="hd"><span class="mono-badge" aria-hidden="true">${esc(s.icon)}</span>
        <div style="flex:1;min-width:0"><div class="n" id="mname">${esc(s.name)}</div><div class="muted" style="font-size:12px">${esc(s.description)}</div></div>
        <span class="st"><i class="dot ${dotCls(s)}"></i>${esc(s.state)}</span>
        <button class="x" data-close title="Close" aria-label="Close">×</button></div>
      ${u ? `<div class="url"><span class="ell">${esc(u)}</span><button data-copy="${esc(u)}">Copy</button></div>` : ''}
      <div class="kv">
        <div><span class="label">${s.cron ? 'Next run' : s.up === false ? 'Down for' : 'Uptime'}</span><b>${esc(s.cron ? (job?.when || '—') : s.since || '—')}</b></div>
        <div><span class="label">Response</span><b>${s.ms != null ? s.ms + ' ms' : '—'}</b></div>
        <div><span class="label">Version</span><b>${esc(s.version || '—')}</b></div></div>
      ${s.bars.length ? `<div><span class="label">Response time · 24h</span><div class="bars">${bars}</div></div>` : ''}
      <div style="font-size:13px;color:var(--n400)">${esc(s.stat)}</div>
      <div class="actions">${data.readonly ? '' : `<button class="btn-ghost" data-edit="${esc(s.id)}">Edit in settings</button>`}
        ${u ? `<a class="btn-out" href="${esc(u)}" target="_blank" rel="noopener noreferrer">Open ${esc(s.name)}</a>` : ''}</div>
    </div>`;
  const wasHidden = m.hidden;
  m.hidden = false;
  if (wasHidden) m.querySelector('[data-close]').focus();
  else if (keepFocus) m.querySelector('[data-copy]')?.focus();
}

function render() {
  if (!data) return;
  renderHeader(); renderStats(); renderPinned(); renderCats(); renderGrid(); renderSide(); renderModal();
  $('#tab-set').hidden = !!data.readonly;
}

async function refresh() {
  try {
    const r = await fetch('api/state', { cache: 'no-store' });
    if (r.ok) { data = await r.json(); render(); }
  } catch (e) { /* offline: keep showing the last data */ }
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) {
    const t = Object.assign(document.createElement('textarea'), { value: text });
    document.body.append(t); t.select();
    const ok = document.execCommand('copy'); t.remove(); return ok;
  }
}

/* ── dashboard events ── */
$('#q').addEventListener('input', e => { query = e.target.value; renderPinned(); renderGrid(); });
$('#q').addEventListener('keydown', e => {
  if (e.key === 'Enter') { const first = visible().find(urlOf); if (first) window.open(urlOf(first), '_blank', 'noopener'); }
  if (e.key === 'Escape') { e.target.value = query = ''; renderPinned(); renderGrid(); }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && selected) { selected = null; renderModal(); }
  if (e.key === '/' && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); location.hash = ''; $('#q').focus(); }
});
$('#cats').addEventListener('click', e => { const b = e.target.closest('[data-cat]'); if (b) { cat = b.dataset.cat; renderCats(); renderGrid(); } });
document.addEventListener('click', e => {
  const info = e.target.closest('[data-info]');
  if (info) { e.preventDefault(); e.stopPropagation(); selected = info.dataset.info; renderModal(); return; }
  if (e.target.closest('[data-close]') || e.target.id === 'modal') { selected = null; renderModal(); return; }
  const cp = e.target.closest('[data-copy]');
  if (cp) { copy(cp.dataset.copy).then(ok => { cp.textContent = ok ? 'Copied' : 'Copy failed'; setTimeout(() => { cp.textContent = 'Copy'; }, 1500); }); return; }
  const ed = e.target.closest('[data-edit]');
  if (ed) { selected = null; renderModal(); highlight = ed.dataset.edit; location.hash = 'settings'; }
});

/* ── settings ── */
let cfg = null, highlight = null, open = new Set();
const token = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; } };
const setToken = v => { try { v ? sessionStorage.setItem(TOKEN_KEY, v) : sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* storage blocked */ } };
const authHeaders = () => (token() ? { Authorization: `Bearer ${token()}` } : {});

function showNotices(list, isError) {
  $('#notices').innerHTML = (list || []).map(n => `<div class="notice${isError ? ' err' : ''}">${esc(n)}</div>`).join('');
}

async function loadSettings() {
  $('#msg').textContent = ''; showNotices([]);
  const r = await fetch('api/config', { cache: 'no-store', headers: authHeaders() });
  if (r.status === 401) { setToken(''); $('#lock').hidden = false; $('#editor').hidden = true; $('#pw').focus(); return; }
  $('#lock').hidden = true; $('#editor').hidden = false;
  cfg = await r.json();
  cfg.services.forEach(s => { s._key = ''; s._password = ''; });
  open = new Set();
  renderRows();
  if (highlight) {
    const row = document.querySelector(`.trow[data-id="${CSS.escape(highlight)}"]`);
    if (row) { row.scrollIntoView({ block: 'center' }); row.classList.add('flash'); row.querySelector('input').focus(); }
    highlight = null;
  }
}

function secretPlaceholder(s, field) {
  if (s[field + 'Ref']) return s[field + 'Ref'];
  return s[field + 'Set'] ? '•••••••• saved' : 'Not set';
}

function advanced(s, i) {
  const kinds = Object.entries(cfg.kinds).map(([k, v]) => `<option value="${esc(k)}"${k === s.kind ? ' selected' : ''}>${esc(v.label)}</option>`).join('');
  const hint = cfg.kinds[s.kind]?.keyHint || '';
  return `<div class="adv" data-i="${i}">
    <label>Kind<select data-f="kind">${kinds}</select></label>
    <label>Host<input data-f="host" class="m" value="${esc(s.host)}" placeholder="127.0.0.1"></label>
    <label>Scheme<select data-f="scheme"><option${s.scheme !== 'https' ? ' selected' : ''}>http</option><option${s.scheme === 'https' ? ' selected' : ''}>https</option></select></label>
    <label>Base path (URL base)<input data-f="basePath" class="m" value="${esc(s.basePath)}" placeholder="/radarr"></label>
    <label>Link URL override<input data-f="url" class="m" value="${esc(s.url)}" placeholder="https://radarr.example.com"></label>
    <label>Description<input data-f="description" value="${esc(s.description)}"></label>
    <label>Icon (1–2 chars)<input data-f="icon" value="${esc(s.icon)}" maxlength="2"></label>
    ${s.kind === 'qbittorrent' ? `<label>Username<input data-f="username" value="${esc(s.username || '')}" autocomplete="off"></label>
    <label>Password<input data-f="_password" type="password" autocomplete="new-password" placeholder="${esc(secretPlaceholder(s, 'password'))}"></label>` : ''}
    ${hint ? `<span class="hint">API key: ${esc(hint)}</span>` : ''}
    ${s.apiKeySet ? '<button class="clear" data-clear="apiKey">Remove saved API key</button>' : ''}
  </div>`;
}

function renderRows() {
  $('#rows').innerHTML = cfg.services.map((s, i) => `
    <div class="trow" data-i="${i}" data-id="${esc(s.id || '')}">
      <span class="mono-badge" aria-hidden="true">${esc(s.icon || (s.name || '?')[0])}</span>
      <input data-f="name" value="${esc(s.name)}" aria-label="Name" placeholder="Name">
      <input data-f="port" class="m p" value="${esc(s.port)}" placeholder="—" inputmode="numeric" aria-label="Port">
      <input data-f="linkPath" class="m pa" value="${esc(s.linkPath)}" placeholder="/" aria-label="Link path">
      <select data-f="category" aria-label="Category">${cfg.categories.map(c => `<option${c === s.category ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>
      <input data-f="_key" class="m k" type="password" autocomplete="new-password" value="${esc(s._key)}" placeholder="${esc(secretPlaceholder(s, 'apiKey'))}" aria-label="API key">
      <button class="star" data-pin aria-pressed="${!!s.pinned}" title="Pin to top" aria-label="Pin ${esc(s.name)}">${STAR(s.pinned)}</button>
      <button class="more" data-more aria-expanded="${open.has(i)}" title="More options" aria-label="More options for ${esc(s.name)}">${MORE_SVG}</button>
      <button class="rm" data-rm title="Remove" aria-label="Remove ${esc(s.name)}">×</button>
    </div>${open.has(i) ? advanced(s, i) : ''}`).join('');
}

$('#rows').addEventListener('input', e => {
  const holder = e.target.closest('[data-i]'), f = e.target.dataset.f;
  if (!holder || !f) return;
  const s = cfg.services[holder.dataset.i];
  s[f] = e.target.value;
  if (f === 'kind') {
    const k = cfg.kinds[s.kind];
    if (k && !s.port && k.defaultPort) s.port = k.defaultPort;
    renderRows();
  }
});
$('#rows').addEventListener('click', e => {
  const holder = e.target.closest('[data-i]'); if (!holder) return;
  const i = Number(holder.dataset.i), s = cfg.services[i];
  if (e.target.closest('[data-pin]')) { s.pinned = !s.pinned; renderRows(); }
  if (e.target.closest('[data-more]')) { open.has(i) ? open.delete(i) : open.add(i); renderRows(); }
  if (e.target.closest('[data-clear]')) { s.clearApiKey = true; s.apiKeySet = false; s.apiKeyRef = ''; renderRows(); $('#msg').textContent = 'API key will be removed when you save.'; }
  if (e.target.closest('[data-rm]')) {
    cfg.services.splice(i, 1);
    open = new Set([...open].filter(x => x !== i).map(x => (x > i ? x - 1 : x)));
    renderRows(); $('#msg').textContent = 'Removed — save to apply.';
  }
});
$('#add').addEventListener('click', () => {
  cfg.services.push({ name: '', kind: 'generic', port: '', linkPath: '', category: cfg.categories[cfg.categories.length - 1], host: '127.0.0.1', scheme: 'http', basePath: '', url: '', description: '', icon: '', pinned: false, _key: '', _password: '' });
  open.add(cfg.services.length - 1);
  renderRows();
  const rows = document.querySelectorAll('.trow[data-i]'); rows[rows.length - 1].querySelector('input').focus();
});
$('#cancel').addEventListener('click', () => { location.hash = ''; });
$('#save').addEventListener('click', async () => {
  const msg = $('#msg');
  const services = cfg.services.map(({ _key, _password, ...s }) => ({ ...s, apiKey: _key || '', password: _password || '' }));
  const r = await fetch('api/config', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ services }) });
  const body = await r.json().catch(() => ({}));
  if (r.status === 401) { loadSettings(); return; }
  if (!r.ok) { msg.textContent = ''; showNotices([body.error || 'Could not save'], true); return; }
  cfg = body; cfg.services.forEach(s => { s._key = ''; s._password = ''; });
  renderRows(); showNotices(body.notices); msg.textContent = 'Saved.';
  refresh();
});
$('#unlock').addEventListener('click', () => { setToken($('#pw').value); $('#pw').value = ''; $('#lockmsg').textContent = 'Checking…'; loadSettings().then(() => { $('#lockmsg').textContent = $('#lock').hidden ? '' : 'Wrong password.'; }); });
$('#pw').addEventListener('keydown', e => { if (e.key === 'Enter') $('#unlock').click(); });

/* ── routing ── */
function route() {
  const set = location.hash === '#settings';
  $('#view-dash').hidden = set; $('#view-set').hidden = !set;
  $('.search').style.visibility = set ? 'hidden' : '';
  $('#tab-dash').setAttribute('aria-current', set ? 'false' : 'page');
  $('#tab-set').setAttribute('aria-current', set ? 'page' : 'false');
  if (set) loadSettings();
}
window.addEventListener('hashchange', route);
route();
refresh();
setInterval(() => { if (!document.hidden && location.hash !== '#settings') refresh(); }, 10000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
