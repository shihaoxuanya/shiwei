import { metricsView } from './metrics.js';
const view = document.querySelector('#view'), message = document.querySelector('#message');
const modal = document.querySelector('#confirmation'), confirmForm = document.querySelector('#confirm-form');
const logout = document.querySelector('#logout');
let csrf = '', listing, pending = null, selected = null;
let section = 'metrics', viewEpoch = 0, metricDays = 7, metricsLoading = false;
function startView(name, title) {
  section = name; viewEpoch++; document.querySelector('#page-title').textContent = title;
  document.querySelector('nav').hidden = !csrf;
  for (const key of ['metrics', 'releases']) {
    const b = document.querySelector(`#nav-${key}`); b.classList.toggle('nav-active', key === name);
    if (key === name) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  }
  return viewEpoch;
}
const labels = { DRAFT:'待构建', READY:'待发布', ROLLOUT:'灰度发布', PUBLISHED:'正式发布', PAUSED:'已暂停', REVOKED:'已撤销' };
function el(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; }
function button(text, fn, cls) { const b = el('button', text, cls); b.type = 'button'; b.addEventListener('click', () => Promise.resolve(fn()).catch(showError)); return b; }
function showError(error) { message.textContent = error.message || '请求失败，请稍后重试'; }
function date(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12:false }) : '尚未发布'; }
function badge(status) { return el('span', labels[status] || status, `badge ${status === 'PAUSED' ? 'warn' : status === 'REVOKED' ? 'bad' : ''}`); }
async function api(path, body) {
  const r = await fetch(path, { method: body === undefined ? 'GET' : 'POST', credentials:'same-origin', redirect:'error', headers:{ 'Content-Type':'application/json', ...(csrf ? { 'X-CSRF-Token':csrf } : {}) }, ...(body === undefined ? {} : { body:JSON.stringify(body) }) });
  const data = await r.json();
  if (!r.ok) { if (r.status === 401) { csrf = ''; renderLogin(); } throw new Error(data.detail || '请求失败'); }
  return data;
}
function field(parent, title, name, value = '', type = 'text') {
  const label = el('label', title), input = el(type === 'textarea' ? 'textarea' : 'input');
  input.name = name; if (type !== 'textarea') input.type = type; else input.rows = 4;
  input.value = value; label.append(input); parent.append(label); return input;
}
function link(value, title) {
  try { const url = new URL(value); if (url.protocol !== 'https:' || url.username || url.password) throw new Error();
    const a = el('a', title || value); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
  } catch { return el('span', '地址不可用'); }
}
function renderLogin() {
  startView('login', '管理后台'); document.querySelector('nav').hidden = true;
  selected = null; logout.hidden = true; modal.close(); pending = null;
  const form = el('form', undefined, 'panel login');
  form.append(el('h2','登录管理后台'),el('p','仅供维护者使用。用户无需注册拾微账号。','muted'));
  const email = field(form,'管理员邮箱','email','','email'); email.required = true; email.autocomplete = 'username';
  const password = field(form,'密码','password','','password'); password.required = true; password.autocomplete = 'current-password';
  const submit = el('button','登录','primary'); submit.type = 'submit'; form.append(submit);
  form.addEventListener('submit', async e => { e.preventDefault(); submit.disabled = true; message.textContent = ''; try {
    const result = await api('/api/auth/login', { email:email.value, password:password.value }); csrf = result.csrf; password.value = ''; await renderMetrics();
  } catch (error) { showError(error); } finally { submit.disabled = false; } });
  view.replaceChildren(form);
}
async function renderList() {
  const epoch = startView('releases', '版本管理');
  const next = await api('/api/admin/releases'); if (epoch !== viewEpoch || !csrf) return;
  listing = next; selected = null; logout.hidden = false; message.textContent = '';
  const current = listing.releases.find(r => r.id === listing.target_id), stats = el('section', undefined, 'stats');
  for (const [title, value, detail] of [['当前发布目标',current ? `v${current.version}` : '暂无',current ? labels[current.status] : '等待构建并验证安装包'],['发布比例',current ? `${current.rollout_percentage}%` : '—','按安装标识稳定分组'],['已登记版本',String(listing.releases.length),'使用情况请查看数据概览']]) {
    const s = el('div',undefined,'stat'); s.append(el('span',title,'muted'),el('strong',value),el('span',detail,'muted')); stats.append(s);
  }
  if (listing.analytics_dashboard) stats.lastChild.append(el('br'),link(listing.analytics_dashboard,'查看已配置的统计面板'));
  const bar = el('div',undefined,'toolbar'); bar.append(el('h2','Stable 版本'),button('创建待构建版本',renderDraft,'primary'));
  const table = el('table'), head = el('tr'); ['版本','状态','发布比例','更新方式','发布时间','操作'].forEach(x => head.append(el('th',x)));
  const thead = el('thead'); thead.append(head); table.append(thead); const body = el('tbody');
  for (const r of listing.releases) {
    const row = el('tr'), status = el('td'), action = el('td'); status.append(badge(r.status)); action.append(button('查看详情',() => renderDetail(r.version)));
    row.append(el('td',`v${r.version}${r.id === listing.target_id ? ' · 当前' : ''}`),status,el('td',`${r.rollout_percentage}%`),el('td',r.mandatory ? '必要更新' : '普通更新'),el('td',date(r.published_at)),action); body.append(row);
  }
  table.append(body); const wrap = el('div',undefined,'table-scroll'); wrap.append(table);
  view.replaceChildren(stats,bar,listing.releases.length ? wrap : el('p','还没有版本。CI 完成构建、上传与校验后，版本将出现在这里。','panel muted'));
}
function renderDraft() {
  startView('releases', '版本管理');
  const form = el('form',undefined,'panel'); form.append(el('h2','创建待构建版本'),el('p','这里只登记版本。安装包必须由 CI 构建、签名并验证，不能在后台上传源码或修改签名。','muted'));
  const version = field(form,'版本号（例如 0.3.1）','version'); version.required = true;
  const notes = field(form,'更新说明','release_notes','','textarea'); notes.maxLength = 8000;
  const save = el('button','保存草稿','primary'); save.type = 'submit'; form.append(save,button('返回',renderList));
  form.addEventListener('submit',async e => { e.preventDefault(); save.disabled = true; try { const r = await api('/api/admin/releases',{ version:version.value, release_notes:notes.value }); await renderDetail(r.version); } catch(e) { showError(e); } finally { save.disabled = false; } }); view.replaceChildren(form);
}
function confirm(row, action, title, options = {}) {
  pending = { row, action, options }; confirmForm.reset();
  document.querySelector('#confirm-title').textContent = title;
  document.querySelector('#confirm-summary').textContent = `版本 v${row.version} · ${labels[row.status]} · 当前 ${row.rollout_percentage}%${options.rollout_percentage ? ` → ${options.rollout_percentage}%` : ''}${options.target_version ? ` → 安全目标 v${options.target_version}（已升级客户端不降级）` : ''}${action === 'policy' ? `；必要更新：${options.mandatory ? '开启' : '关闭'}，最低支持：${options.minimum_supported_version || '未设置'}，最高适用：${options.maximum_supported_version || '未设置'}` : ''}。此操作将写入审计记录。`;
  document.querySelector('#confirm-error').textContent = ''; modal.showModal(); confirmForm.elements.confirm_version.focus();
}
async function renderDetail(version) {
  const epoch = startView('releases', '版本管理');
  const data = await api(`/api/admin/releases/${encodeURIComponent(version)}`), r = data.release;
  if (epoch !== viewEpoch || !csrf) return;
  selected = version; message.textContent = ''; const bar = el('div',undefined,'toolbar'); bar.append(button('返回版本列表',renderList),button('刷新',() => renderDetail(version)));
  const grid = el('div',undefined,'grid'), left = el('section',undefined,'panel'), right = el('section',undefined,'panel');
  left.append(el('h2',`v${r.version}`),badge(r.status),el('h3','更新说明'),el('p',r.release_notes || '暂无更新说明','notes'));
  const dl = el('dl');
  for (const [name,value] of [['创建时间',date(r.created_at)],['发布时间',date(r.published_at)],['资源验证时间',r.verified_at ? date(r.verified_at) : '未验证'],['安装包大小',r.artifact_size ? `${(r.artifact_size / 1048576).toFixed(1)} MB` : '—']]) dl.append(el('dt',name),el('dd',value));
  dl.append(el('dt','Installer URL')); const url = el('dd'); url.append(r.artifact_url ? link(r.artifact_url) : el('span','等待 CI')); dl.append(url,el('dt','SHA256'),el('dd',r.artifact_sha256 || '等待 CI')); left.append(dl);
  const sig = el('details'); sig.append(el('summary',r.signature ? 'Updater 签名 · 已接收（只读）' : 'Updater 签名 · 未接收'),el('code',r.signature || '')); left.append(sig);
  right.append(el('h2','发布策略'),el('p',`当前发布比例 ${r.rollout_percentage}%`,'muted'));
  const actions = el('div',undefined,'actions');
  if (r.status === 'READY') actions.append(button('开始 5% 灰度',() => confirm(r,'start','开始灰度发布',{rollout_percentage:5}),'primary'));
  if (r.status === 'ROLLOUT') for (const n of [20,50]) if (n > r.rollout_percentage) actions.append(button(`扩大到 ${n}%`,() => confirm(r,'rollout','扩大灰度范围',{rollout_percentage:n})));
  if (['READY','ROLLOUT'].includes(r.status)) actions.append(button('正式全量发布',() => confirm(r,'publish','确认面向全部目标用户发布',{rollout_percentage:100})));
  if (['ROLLOUT','PUBLISHED'].includes(r.status)) actions.append(button('暂停发布',() => confirm(r,'pause','暂停新更新检查')));
  if (r.status === 'PAUSED') actions.append(button('恢复发布',() => confirm(r,'resume','恢复原有发布范围')));
  if (['READY','ROLLOUT','PUBLISHED','PAUSED'].includes(r.status)) actions.append(button('撤销版本',() => confirm(r,'revoke','撤销版本，不再分发'),'danger'));
  right.append(actions);
  if (r.status === 'DRAFT') right.append(el('p','等待 CI 提交并通过官方签名校验，当前不能发布。','muted'));
  if (r.status === 'REVOKED') right.append(el('p','此版本不能恢复发布。请构建更高版本的修复包，或回滚到已验证的安全发布目标。','muted'));
  if (['READY','ROLLOUT','PUBLISHED','PAUSED'].includes(r.status)) {
    const form = el('form'), mandatory = field(form,'仅用于严重安全或兼容性问题：必要更新','mandatory','','checkbox'); mandatory.checked = Boolean(r.mandatory);
    const min = field(form,'最低支持版本（留空不限制）','minimum',r.minimum_supported_version || '');
    const max = field(form,'最高适用旧版本（留空不限制）','maximum',r.maximum_supported_version || '');
    const save = el('button','确认修改策略'); save.type = 'submit'; form.append(save);
    form.addEventListener('submit',e => { e.preventDefault(); confirm(r,'policy','修改更新策略',{ mandatory:mandatory.checked, minimum_supported_version:min.value.trim() || null, maximum_supported_version:max.value.trim() || null }); }); right.append(form);
  }
  if (listing?.target_id === r.id) {
    const safe = listing.releases.filter(x => x.id !== r.id && x.status === 'PUBLISHED');
    if (safe.length) { const label = el('label','回滚发布目标'), select = el('select'); safe.forEach(x => { const o=el('option',`v${x.version}`); o.value=x.version; select.append(o); }); label.append(select); right.append(label,button('回滚到安全目标',() => confirm(r,'rollback','停止当前版本并回滚发布目标',{target_version:select.value}),'danger')); }
  }
  grid.append(left,right); const audits = el('section',undefined,'panel'); audits.append(el('h2','操作记录'));
  for (const a of data.audit) { const block = el('details'); block.append(el('summary',`${date(a.timestamp)} · ${a.action} · ${a.actor}`),el('p',a.reason),el('pre',`${a.old_value}\n→\n${a.new_value}`,'audit')); audits.append(block); }
  view.replaceChildren(bar,grid,audits);
}
document.querySelector('#cancel').addEventListener('click',() => modal.close());
confirmForm.addEventListener('submit',async e => {
  e.preventDefault(); if (!pending) return; const {row,action,options} = pending;
  const error = document.querySelector('#confirm-error');
  if (confirmForm.elements.confirm_version.value !== row.version) { error.textContent = '版本号不匹配，请输入不含 v 前缀的版本号。'; return; }
  const controls = [...confirmForm.querySelectorAll('button')]; controls.forEach(b => b.disabled=true);
  try { await api(`/api/admin/releases/${encodeURIComponent(row.version)}/actions`,{ action,revision:row.revision,confirm_version:row.version,reason:confirmForm.elements.reason.value,...options }); modal.close(); pending=null; listing=await api('/api/admin/releases'); await renderDetail(row.version); }
  catch(e) { error.textContent=e.message; } finally { controls.forEach(b => b.disabled=false); }
});
logout.addEventListener('click',async () => { try { await api('/api/admin/logout',{}); csrf=''; renderLogin(); } catch(e) { showError(e); } });
async function renderMetrics(quiet = false) {
  const epoch = startView('metrics', '数据概览'); logout.hidden = false;
  if (!quiet) view.replaceChildren(el('p', '正在汇总使用数据…', 'panel muted'));
  metricsLoading = true;
  try {
    const data = await api(`/api/admin/metrics?days=${metricDays}`);
    if (epoch !== viewEpoch || !csrf) return;
    message.textContent = '';
    view.replaceChildren(metricsView(data, { el, button, daysChanged: days => { metricDays = days; void renderMetrics(); }, refresh: () => renderMetrics() }));
  } catch (error) {
    if (epoch !== viewEpoch || !csrf) return;
    showError(error);
    if (!quiet) { const block = el('section', undefined, 'panel'); block.append(el('h2', '暂时无法读取统计'), el('p', '不会将服务错误显示为零数据。版本管理仍可单独使用。', 'muted'), button('重试', () => renderMetrics())); view.replaceChildren(block); }
  } finally { if (epoch === viewEpoch) metricsLoading = false; }
}
document.querySelector('#nav-metrics').addEventListener('click', () => void renderMetrics());
document.querySelector('#nav-releases').addEventListener('click', () => void renderList().catch(showError));
setInterval(() => { if (csrf && section === 'metrics' && !metricsLoading && !document.hidden) void renderMetrics(true); }, 60_000);
try { const s = await api('/api/admin/session'); csrf=s.csrf; await renderMetrics(); } catch { renderLogin(); }
