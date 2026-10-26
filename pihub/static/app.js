/* pi-hub UI. Plain JS, no build step. Renders /api/state and edits /api/config. */
'use strict';

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const INFO_SVG = '<svg width="16" height="16" viewBox="0 0 256 256" fill="currentColor" aria-hidden="true"><path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm16-40a8,8,0,0,1-8,8,16,16,0,0,1-16-16V128a8,8,0,0,1,0-16,16,16,0,0,1,16,16v40A8,8,0,0,1,144,176ZM112,84a12,12,0,1,1,12,12A12,12,0,0,1,112,84Z"/></svg>';
const STAR = filled => `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="${filled ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>`;
const MORE_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>';
const host = location.hostname;
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
  $('#tab-host').hidden = !data.hostAgent || !!data.readonly;
}

async function refresh() {
  if (signedOut()) return;
  try {
    const r = await api('api/state');
    if (r.ok) { data = await r.json(); render(); }
  } catch (e) { /* offline or signed out: keep showing the last data */ }
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

/* ── sign-in ── */
let me = null, ticking = false;
const signedOut = () => document.body.classList.contains('signed-out');
const postJSON = (path, body) => fetch(path, { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

// Every API call goes through here: a 401 means the session is gone, so show the sign-in screen.
async function api(path, opts = {}) {
  const r = await fetch(path, { cache: 'no-store', ...opts });
  if (r.status === 401) { showAuth('login'); throw Object.assign(new Error('Sign in required'), { auth: true }); }
  return r;
}

function showAuth(view) {
  const wasOut = signedOut();
  document.body.classList.add('signed-out');
  if (me) me.authenticated = false;
  $('#me').hidden = true;
  selected = null; renderModal(); closeHost();
  $('#view-login').hidden = view !== 'login';
  $('#view-setup').hidden = view !== 'setup';
  if (!wasOut) $(view === 'login' ? '#li-user' : '#su-code').focus();
}

async function boot() {
  try { me = await (await fetch('api/auth', { cache: 'no-store' })).json(); } catch (e) { me = { enabled: false, authenticated: true }; }
  if (me.enabled && !me.authenticated) { showAuth(me.setup ? 'setup' : 'login'); return; }
  document.body.classList.remove('signed-out');
  $('#view-login').hidden = $('#view-setup').hidden = true;
  $('#me').hidden = !(me.enabled && me.authenticated);
  $('#me-name').textContent = me.username || '';
  $('#account').hidden = !me.canChangePassword;
  $('#pw-user').value = me.username || '';
  route(); refresh();
  if (!ticking) {
    ticking = true;
    setInterval(() => { if (!document.hidden && location.hash !== '#settings') refresh(); }, 10000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  }
}

$('#loginform').addEventListener('submit', async e => {
  e.preventDefault();
  $('#limsg').textContent = 'Signing in…';
  try {
    const r = await postJSON('api/login', { username: $('#li-user').value, password: $('#li-pw').value });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) { $('#limsg').textContent = out.error || `Sign-in failed (${r.status})`; $('#li-pw').select(); return; }
    $('#li-pw').value = ''; $('#limsg').textContent = '';
    boot();
  } catch (err) { $('#limsg').textContent = 'Could not reach pi-hub.'; }
});

$('#signout').addEventListener('click', async () => {
  try { await postJSON('api/logout', {}); } catch (e) { /* signed out locally either way */ }
  data = null; cfg = null;
  showAuth('login');
  $('#li-user').focus();
});

$('#setupform').addEventListener('submit', async e => {
  e.preventDefault();
  const msg = $('#sumsg'), pw = $('#su-pw').value;
  if (pw.length < 8) { msg.textContent = 'Use a password of at least 8 characters.'; return; }
  if (pw !== $('#su-pw2').value) { msg.textContent = "Passwords don't match."; return; }
  msg.textContent = 'Creating account…';
  try {
    const r = await postJSON('api/setup', { code: $('#su-code').value.trim(), username: $('#su-user').value, password: pw });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) { msg.textContent = out.error || `Setup failed (${r.status})`; return; }
    $('#setupform').reset(); msg.textContent = '';
    boot();
  } catch (err) { msg.textContent = 'Could not reach pi-hub.'; }
});

$('#pwform').addEventListener('submit', async e => {
  e.preventDefault();
  const msg = $('#pwmsg'), pw = $('#pw-new').value;
  msg.classList.add('err');
  if (pw.length < 8) { msg.textContent = 'Use a password of at least 8 characters.'; return; }
  if (pw !== $('#pw-new2').value) { msg.textContent = "New passwords don't match."; return; }
  msg.classList.remove('err'); msg.textContent = 'Changing…';
  try {
    const r = await api('api/password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ current: $('#pw-cur').value, new: pw }) });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) { msg.classList.add('err'); msg.textContent = out.error || `Could not change the password (${r.status})`; return; }
    $('#pw-cur').value = $('#pw-new').value = $('#pw-new2').value = '';
    msg.textContent = 'Password changed. Other devices are signed out.';
  } catch (err) { if (!err.auth) { msg.classList.add('err'); msg.textContent = 'Could not reach pi-hub.'; } }
});

/* ── settings ── */
let cfg = null, highlight = null, open = new Set();

function showNotices(list, isError) {
  $('#notices').innerHTML = (list || []).map(n => `<div class="notice${isError ? ' err' : ''}">${esc(n)}</div>`).join('');
}

async function loadSettings() {
  $('#msg').textContent = ''; showNotices([]);
  let r;
  try { r = await api('api/config'); } catch (e) { return; }
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
  let r;
  try { r = await api('api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ services }) }); } catch (e) { return; }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) { msg.textContent = ''; showNotices([body.error || 'Could not save'], true); return; }
  cfg = body; cfg.services.forEach(s => { s._key = ''; s._password = ''; });
  renderRows(); showNotices(body.notices); msg.textContent = 'Saved.';
  refresh();
});

/* ── host: restart and Claude sessions (needs the host agent) ── */
let hostState = null, hostDirs = null, hostPath = '', hostMsg = null, hostBusy = false, rebootArmed = null;

async function hostApi(path, body) {
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await api(path, opts);
  const out = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(out.error || `Request failed (${r.status})`);
  return out;
}

async function openHost() {
  $('#hostmodal').hidden = false;
  hostMsg = null;
  await loadHost(hostPath);
  $('#hostmodal [data-hclose]')?.focus();
}

function closeHost() { $('#hostmodal').hidden = true; $('#hostmodal').innerHTML = ''; clearTimeout(rebootArmed); rebootArmed = null; }

async function loadHost(path) {
  try {
    [hostState, hostDirs] = await Promise.all([hostApi('api/host'), hostApi(`api/host/dirs?path=${encodeURIComponent(path)}`)]);
    hostPath = hostDirs.path;
  } catch (e) {
    if (e.auth) return;  // signed out: the sign-in screen has taken over
    if (!hostState) { hostState = null; hostDirs = null; }
    hostMsg = { err: true, text: e.message };
    if (path) return loadHost('');  // folder vanished: fall back to the root
  }
  renderHost();
}

const crumbs = path => [['~', ''], ...path.split('/').filter(Boolean).map((p, i, a) => [p, a.slice(0, i + 1).join('/')])];
const ago = t => { const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`; };

function renderHost() {
  const m = $('#hostmodal');
  if (m.hidden) return;
  const st = hostState, msg = hostMsg ? `<div class="notice${hostMsg.err ? ' err' : ''}">${esc(hostMsg.text)}</div>` : '';
  let body = '';
  if (st) {
    const sessions = st.sessions.length ? st.sessions.map(s => `
      <div class="sess"><div class="ell"><div class="ell">${esc(s.name)}</div><div class="muted ell">~/${esc(s.path)} · ${esc(ago(s.started))}</div></div>
        ${s.url ? `<a class="btn-out" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Open</a>` : ''}
        <button class="btn-ghost" data-hstop="${esc(s.id)}"${hostBusy ? ' disabled' : ''}>Stop</button></div>`).join('')
      : '<div class="note">No sessions started from here.</div>';
    const dirs = hostDirs ? hostDirs.dirs.map(d => `<button class="dir" data-hdir="${esc(hostPath ? hostPath + '/' + d : d)}">${esc(d)}</button>`).join('')
      + (hostDirs.more ? '<div class="note">Showing the first 500 folders.</div>' : '') || '<div class="note">No subfolders.</div>' : '';
    const crumb = crumbs(hostPath).map(([n, p], i, a) => i === a.length - 1 ? `<b>${esc(n)}</b>` : `<button class="lnk" data-hdir="${esc(p)}">${esc(n)}</button>`).join(' / ');
    body = `
      ${st.claude ? `<section><h2 class="label">Claude sessions</h2>${sessions}</section>
      <section><h2 class="label">Start Claude Code in</h2>
        <div class="crumb">${crumb}</div>
        <div class="dirs">${dirs}</div>
        <div class="actions"><button class="btn-out btn-fill" data-hstart${hostBusy ? ' disabled' : ''}>${hostBusy === 'start' ? 'Starting…' : 'Start Claude here'}</button></div>
        <div class="note">Runs <code>claude remote-control</code> — continue from the Claude app or claude.ai/code.</div></section>` : ''}
      ${st.reboot ? `<section class="power"><div><h2 class="label">Power</h2><div class="note">Services go offline for a minute or two.</div></div>
        <button class="btn-out danger${rebootArmed ? ' armed' : ''}" data-hreboot${hostBusy ? ' disabled' : ''}>${rebootArmed ? 'Tap again to restart' : 'Restart'}</button></section>` : ''}`;
  }
  m.innerHTML = `
    <div class="modal host" role="dialog" aria-modal="true" aria-labelledby="hname">
      <div class="hd"><div style="flex:1;min-width:0"><div class="n" id="hname">Host</div><div class="muted" style="font-size:12px">${esc(host)}</div></div>
        <button class="x" data-hclose title="Close" aria-label="Close">×</button></div>
      ${msg}${body}
    </div>`;
}

async function hostDo(kind, fn) {
  hostBusy = kind; hostMsg = null; renderHost();
  try { hostMsg = await fn(); } catch (e) { if (e.auth) return; hostMsg = { err: true, text: e.message }; }
  hostBusy = false;
  if (kind !== 'reboot' || hostMsg?.err) { try { hostState = await hostApi('api/host'); } catch (e) { /* keep last */ } }
  renderHost();
}

// Claude prints the session link a few seconds after start; refresh until the Open button can show.
async function pollSessionLink(id) {
  for (let i = 0; i < 10 && !$('#hostmodal').hidden; i++) {
    await new Promise(r => setTimeout(r, 2000));
    try { hostState = await hostApi('api/host'); } catch (e) { return; }
    const s = hostState.sessions.find(x => x.id === id);
    if (!hostBusy) renderHost();
    if (!s || s.url) return;
  }
}

$('#tab-host').addEventListener('click', e => { e.preventDefault(); openHost(); });
$('#hostmodal').addEventListener('click', e => {
  if (e.target.closest('[data-hclose]') || e.target.id === 'hostmodal') { closeHost(); return; }
  const dir = e.target.closest('[data-hdir]');
  if (dir) { hostMsg = null; loadHost(dir.dataset.hdir); return; }
  if (e.target.closest('[data-hstart]')) {
    hostDo('start', async () => {
      const { session } = await hostApi('api/host/claude', { path: hostPath });
      if (session.url) window.open(session.url, '_blank', 'noopener');
      else pollSessionLink(session.id);
      return { text: `Started “${session.name}”. It's listed under Claude sessions in the Claude app.` };
    });
    return;
  }
  const stop = e.target.closest('[data-hstop]');
  if (stop) { hostDo('stop', async () => { await hostApi('api/host/stop', { id: stop.dataset.hstop }); return { text: 'Session stopped.' }; }); return; }
  if (e.target.closest('[data-hreboot]')) {
    if (!rebootArmed) { rebootArmed = setTimeout(() => { rebootArmed = null; renderHost(); }, 4000); renderHost(); return; }
    clearTimeout(rebootArmed); rebootArmed = null;
    hostDo('reboot', async () => { await hostApi('api/host/reboot', {}); return { text: 'Restarting… this page will show data again once the Pi is back.' }; });
  }
});
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#hostmodal').hidden) closeHost(); });

/* ── routing ── */
function route() {
  const set = location.hash === '#settings';
  $('#view-dash').hidden = set; $('#view-set').hidden = !set;
  $('.search').style.visibility = set ? 'hidden' : '';
  $('#tab-dash').setAttribute('aria-current', set ? 'false' : 'page');
  $('#tab-set').setAttribute('aria-current', set ? 'page' : 'false');
  if (set) loadSettings();
}
window.addEventListener('hashchange', () => { if (!signedOut()) route(); });
boot();
