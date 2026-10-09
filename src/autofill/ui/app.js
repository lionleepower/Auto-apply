'use strict';
const $ = (id) => document.getElementById(id);
const S = { schema: { basic_groups: [], locales: {} }, profile: {}, missing: [], answers: [], resumes: [], llm: { providers: [], presets: {} } };
let dirty = false;

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, '');
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
}

async function api(method, path, body) {
  const r = await fetch('/api/ui' + path, {
    method, headers: body ? { 'content-type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${r.status}）`);
  return data;
}

let toastTimer;
function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3200);
}

// ---------- 标签页 ----------
function go(tab) {
  document.querySelectorAll('.tab').forEach((s) => s.classList.toggle('on', s.id === 'tab-' + tab));
  document.querySelectorAll('.nav').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  try { localStorage.setItem('tab', tab); } catch { /* 无痕等场景存不了，忽略 */ }
}
document.querySelectorAll('.nav').forEach((b) => b.addEventListener('click', () => go(b.dataset.tab)));
document.querySelectorAll('[data-go]').forEach((b) => b.addEventListener('click', () => go(b.dataset.go)));

// ---------- 加载 ----------
function applyState(st) {
  S.profile = st.profile; S.missing = st.missing; S.llm = st.llm;
  S.answers = st.answers; S.id_autofill = st.id_autofill;
  S.resumes = st.resumes; S.current = st.current; S.default = st.default; S.locale = st.locale;
  if (st.schema) S.schema = st.schema;
  S.completeness = st.completeness; S.resume = st.resume; S.ai_ready = st.ai_ready;
  dirty = false;
  try { localStorage.setItem('resume', S.current); } catch { /* ignore */ }
}

async function load(rid) {
  const st = await api('GET', '/state' + (rid ? `?resume=${encodeURIComponent(rid)}` : ''));
  applyState(st);
  $('notes').value = st.notes || '';
  $('ingest-result').hidden = true;
  const setup = await api('GET', '/setup');
  $('ext-dir').textContent = setup.extension_dir;
  S.extDir = setup.extension_dir;
  llmDraft = S.llm.providers.map((p) => ({ ...p, api_key: '' }));
  renderAll();
}

function renderAll() {
  renderResumes(); renderMissing(); renderForm(); renderProviders(); renderStart(); renderHint();
  const cur = S.resumes.find((r) => r.id === S.current);
  $('kb-for').textContent = cur ? `· 整理到「${cur.name}」` : '';
  $('resume-name').textContent = S.resume ? `这份简历已保存文件：${S.resume}（网页上传简历时会用它）` : '';
}

// ---------- 多份简历 ----------
const localeName = (l) => (S.schema.locales || {})[l] || l;

function renderResumes() {
  const box = $('resume-chips'); box.replaceChildren();
  for (const r of S.resumes) {
    box.append(el('span', { class: 'rchip' + (r.id === S.current ? ' on' : ''), onclick: () => switchResume(r.id) },
      r.name,
      r.locale === 'overseas' ? el('small', {}, '海外') : null,
      r.id === S.default ? el('small', {}, '★默认') : null));
  }
  box.append(el('button', { onclick: () => { $('new-resume').hidden = false; $('nr-name').focus(); } }, '＋ 新建简历'));
  const cur = S.resumes.find((r) => r.id === S.current);
  $('rt-name').value = cur ? cur.name : '';
  fillSelect($('rt-locale'), S.schema.locales, cur ? cur.locale : 'cn');
  fillSelect($('nr-locale'), S.schema.locales, 'cn');
  fillSelect($('nr-copy'), { '': '空白简历', ...Object.fromEntries(S.resumes.map((r) => [r.id, `复制「${r.name}」的内容`])) }, '');
  $('rt-default').disabled = S.current === S.default;
  $('rt-delete').disabled = S.resumes.length < 2;
}

function fillSelect(sel, map, value) {
  sel.replaceChildren(...Object.entries(map).map(([k, v]) => el('option', { value: k }, v)));
  sel.value = value;
}

function confirmLeave() {
  return !dirty || confirm('当前简历有未保存的修改，切换后会丢失。继续吗？');
}

async function switchResume(id) {
  if (id === S.current || !confirmLeave()) return;
  try { await load(id); } catch (e) { toast(e.message); }
}

async function resumeCall(method, path, body, okMsg) {
  try {
    const st = await api(method, path, body);
    applyState(st); $('notes').value = st.notes || ''; renderAll();
    if (okMsg) toast(okMsg);
  } catch (e) { toast(e.message); }
}

$('nr-cancel').addEventListener('click', () => { $('new-resume').hidden = true; });
$('nr-create').addEventListener('click', async () => {
  if (!confirmLeave()) return;
  const name = $('nr-name').value.trim() || '新简历';
  $('new-resume').hidden = true; $('nr-name').value = '';
  await resumeCall('POST', '/resumes', { name, locale: $('nr-locale').value, copy_from: $('nr-copy').value || null }, `已创建「${name}」`);
});
$('rt-name').addEventListener('change', () => resumeCall('PATCH', `/resumes/${S.current}`, { name: $('rt-name').value }, '已改名'));
$('rt-locale').addEventListener('change', () => resumeCall('PATCH', `/resumes/${S.current}`, { locale: $('rt-locale').value }, '已修改类型'));
$('rt-default').addEventListener('click', () => resumeCall('PATCH', `/resumes/${S.current}`, { make_default: true }, '已设为默认简历'));
$('rt-copy').addEventListener('click', async () => {
  if (!confirmLeave()) return;
  const cur = S.resumes.find((r) => r.id === S.current);
  await resumeCall('POST', '/resumes', { name: `${cur.name} 副本`, locale: cur.locale, copy_from: S.current }, '已复制');
});
$('rt-delete').addEventListener('click', async () => {
  const cur = S.resumes.find((r) => r.id === S.current);
  if (!confirm(`删除简历「${cur.name}」？它的教育 / 经历 / 项目内容会一起删除，基本信息不受影响。`)) return;
  await resumeCall('DELETE', `/resumes/${S.current}`, null, '已删除');
});

// ---------- 缺失项 ----------
const pathOf = (key) => (key.startsWith('education.') ? key.replace('education.', 'education.0.') : key);

function renderMissing() {
  const box = $('missing'); box.replaceChildren();
  const pct = S.completeness ?? 0;
  $('bar-fill').style.width = pct + '%';
  $('pct').textContent = `完整度 ${pct}%（按「${localeName(S.locale)}」简历的常见要求计算）`;
  const req = S.missing.filter((m) => m.level === 'required');
  const badge = $('nav-badge'); badge.hidden = !req.length; badge.textContent = req.length;
  if (!S.missing.length) { box.append(el('div', { class: 'ok-line' }, '✓ 常见表单需要的信息都有了')); return; }
  for (const [level, title] of [['required', '必填（几乎每张表都要）'], ['recommended', '建议补充']]) {
    const items = S.missing.filter((m) => m.level === level);
    if (!items.length) continue;
    box.append(el('div', { class: 'muted small' }, title));
    box.append(el('div', {}, items.map((m) => el('span', { class: 'chip ' + level, onclick: () => focusField(m) }, m.label))));
  }
}

function focusField(m) {
  const target = document.querySelector(`[data-path="${pathOf(m.key)}"]`) || document.querySelector(`[data-add="${m.key}"]`);
  if (!target) return;
  const acc = target.closest('details'); if (acc) { acc.open = true; OPEN[acc.dataset.acc] = true; }
  target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  target.focus({ preventScroll: true });
}

// ---------- 资料表单（可折叠分区） ----------
const OPEN = {};
function isOpen(id, dflt) {
  if (id in OPEN) return OPEN[id];
  try { const v = localStorage.getItem('acc-' + id); if (v !== null) return v === '1'; } catch { /* ignore */ }
  return dflt;
}

function accordion(id, title, { badges = [], dflt = false, hint } = {}, ...body) {
  const d = el('details', { class: 'acc', 'data-acc': id },
    el('summary', {}, title, badges.map((b) => el('span', { class: 'sb ' + (b.cls || '') }, b.text))),
    el('div', { class: 'acc-body' }, hint ? el('p', { class: 'muted small' }, hint) : null, ...body));
  d.open = isOpen(id, dflt);
  d.addEventListener('toggle', () => {
    OPEN[id] = d.open;
    try { localStorage.setItem('acc-' + id, d.open ? '1' : '0'); } catch { /* ignore */ }
  });
  return d;
}

function getAt(path) { return path.split('.').reduce((o, k) => (o == null ? o : o[k]), S.profile); }
function setAt(path, value) {
  const keys = path.split('.'); const last = keys.pop();
  const obj = keys.reduce((o, k) => o[k], S.profile);
  obj[last] = value === '' ? null : value;
  markDirty();
}
function markDirty() { dirty = true; $('save-state').textContent = '有未保存的修改'; }

function field(spec, path) {
  const missing = S.missing.some((m) => pathOf(m.key) === path);
  const cur = getAt(path);
  const common = { 'data-path': path, class: missing ? 'miss' : '' };
  let input;
  if (spec.type === 'select') {
    input = el('select', common, el('option', { value: '' }, '—'), spec.options.map((o) => el('option', { value: o }, o)));
    input.value = cur ?? '';
  } else if (spec.type === 'tribool') {
    input = el('select', common, el('option', { value: '' }, '—'), el('option', { value: 'true' }, '是'), el('option', { value: 'false' }, '否'));
    input.value = cur === true ? 'true' : cur === false ? 'false' : '';
  } else if (spec.type === 'textarea') {
    input = el('textarea', common); input.value = cur ?? '';
  } else {
    input = el('input', { ...common, type: 'text', placeholder: spec.placeholder || '' }); input.value = cur ?? '';
  }
  input.addEventListener('input', () => {
    input.classList.remove('miss');
    setAt(path, spec.type === 'tribool' ? (input.value === '' ? null : input.value === 'true') : input.value);
  });
  return el('label', { class: spec.type === 'textarea' ? 'wide' : '' }, el('span', {}, spec.label), input);
}

const LIST_TITLES = { education: '教育经历', experience: '实习 / 工作 / 科研经历', projects: '项目经历', papers: '论文' };
const missCount = (section) => S.missing.filter((m) => m.section === section && m.level === 'required').length;

function renderForm() {
  const form = $('form'); form.replaceChildren();
  const overseas = S.locale === 'overseas';

  // 共用部分
  for (const g of S.schema.basic_groups) {
    const body = [el('div', { class: 'grid' }, g.fields.map((f) => field(f, 'basic.' + f.key)))];
    if (g.id === 'identity') {
      const cb = el('input', { type: 'checkbox' }); cb.checked = !!S.id_autofill;
      cb.addEventListener('change', () => { S.id_autofill = cb.checked; markDirty(); warn.hidden = !cb.checked; });
      const warn = el('div', { class: 'warnbox' }, '已开启：遇到「身份证号 / 证件号」输入框时，助手会直接填入。请确认你信任当前网站，并在提交前核对。');
      warn.hidden = !S.id_autofill;
      body.push(el('label', { class: 'check', style: 'margin-top:10px' }, cb, '允许助手自动填写证件号（默认关闭，关闭时只会在页面上标紫提醒你手动填）'), warn);
    }
    const idMiss = S.missing.filter((m) => m.level === 'required' && g.fields.some((f) => 'basic.' + f.key === m.key)).length;
    form.append(accordion(g.id, g.title, {
      dflt: g.open || (g.id === 'overseas' && overseas), hint: g.hint,
      badges: [{ text: '共用', cls: 'share' }, ...(idMiss ? [{ text: `缺 ${idMiss}`, cls: 'red' }] : [])],
    }, ...body));
  }

  // 当前简历
  const cur = S.resumes.find((r) => r.id === S.current);
  form.append(el('div', { class: 'divider' }, `当前简历：${cur ? cur.name : ''}（${localeName(S.locale)}）`));
  for (const sec of ['education', 'experience', 'projects', 'papers']) {
    const list = S.profile[sec] || (S.profile[sec] = []);
    const add = el('button', { 'data-add': sec, onclick: () => { list.push({}); markDirty(); OPEN[sec] = true; renderForm(); } }, '＋ 添加一段');
    const m = missCount(sec);
    form.append(accordion(sec, LIST_TITLES[sec], {
      dflt: sec === 'education' || list.length > 0 && sec === 'experience',
      badges: [{ text: `${list.length} 段` }, ...(m ? [{ text: `缺 ${m}`, cls: 'red' }] : [])],
    }, list.length ? null : el('div', { class: 'muted small', style: 'margin-bottom:8px' }, '暂无'),
    list.map((item, i) => el('div', { class: 'item' },
      el('div', { class: 'item-h' }, el('span', {}, `第 ${i + 1} 段`),
        el('button', { class: 'mini danger', onclick: () => { list.splice(i, 1); markDirty(); renderForm(); } }, '删除')),
      el('div', { class: 'grid' }, S.schema[sec].map((f) => field(f, `${sec}.${i}.${f.key}`))))), add));
  }
  form.append(accordion('extra', '技能与自我评价', { dflt: true, badges: [{ text: '本简历' }] },
    el('div', { class: 'grid' }, S.schema.extra.map((f) => field(f, f.key)))));

  // 常用问答（共用）
  form.append(el('div', { class: 'divider' }, '所有简历共用'));
  form.append(qaSection());
}

function qaSection() {
  const list = S.answers;
  const box = el('div', {});
  const draw = () => {
    box.replaceChildren(...list.map((qa, i) => {
      const p = el('input', { placeholder: '问题里出现的词，如：亲属|是否.*关系', 'data-path': `qa.${i}.p` }); p.value = qa.pattern;
      const a = el('input', { placeholder: '固定答案，如：否' }); a.value = qa.answer;
      p.addEventListener('input', () => { qa.pattern = p.value; markDirty(); });
      a.addEventListener('input', () => { qa.answer = a.value; markDirty(); });
      return el('div', { class: 'qa' }, p, a, el('button', { class: 'mini danger', onclick: () => { list.splice(i, 1); markDirty(); draw(); } }, '删除'));
    }), el('button', { 'data-add': 'qa', onclick: () => { list.push({ pattern: '', answer: '' }); markDirty(); draw(); } }, '＋ 添加一条'));
  };
  draw();
  return accordion('qa', '常用问答', {
    badges: [{ text: `${list.length} 条`, cls: '' }, { text: '共用', cls: 'share' }],
    hint: '每家都问、答案固定的问题（如「是否有亲属在本公司任职」→「否」）。左边写问题里的关键词（可用 | 分隔多个，支持正则），右边写答案；匹配到的字段会优先填这里的答案。',
  }, box);
}

$('expand-all').addEventListener('click', () => document.querySelectorAll('#form details').forEach((d) => (d.open = true)));
$('collapse-all').addEventListener('click', () => document.querySelectorAll('#form details').forEach((d) => (d.open = false)));

$('save').addEventListener('click', async () => {
  try {
    applyState(await api('PUT', '/profile', {
      resume: S.current, profile: S.profile, answers: S.answers, id_autofill: S.id_autofill,
    }));
    $('save-state').textContent = ''; renderAll(); toast('资料已保存');
  } catch (e) { toast(e.message); }
});
window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

// ---------- 知识库：文本 + 简历 + AI 整理 ----------
let notesTimer;
$('notes').addEventListener('input', () => {
  $('notes-saved').textContent = '…';
  clearTimeout(notesTimer);
  const rid = S.current;
  notesTimer = setTimeout(async () => {
    try { await api('PUT', '/notes', { text: $('notes').value, resume: rid }); $('notes-saved').textContent = '已自动保存'; }
    catch { $('notes-saved').textContent = '保存失败'; }
  }, 700);
});

function renderHint() {
  const names = S.llm.providers.filter((p) => p.usable).map((p) => p.label);
  $('ingest-hint').textContent = names.length
    ? `点击后文字会发送给 ${names[0]}${names.length > 1 ? `（失败自动换 ${names[1]}…）` : ''}；证件号会先打码`
    : '还没配 AI 接口：只能识别手机号 / 邮箱 / GitHub 等固定格式';
}

function showResult(kind, msg, lines = []) {
  const box = $('ingest-result'); box.hidden = false; box.className = 'notice ' + kind;
  box.replaceChildren(msg, ...(lines.length ? [el('ul', {}, lines.slice(0, 12).map((l) => el('li', {}, l)))] : []));
}

async function ingest(text) {
  if (dirty && !confirm('表单里有未保存的修改，AI 整理会基于已保存的版本，未保存的修改会丢失。继续吗？')) return;
  const btn = $('ingest'); btn.disabled = true; btn.textContent = '整理中…';
  try {
    const mode = S.ai_ready ? 'ai' : 'quick';
    const r = await api('POST', '/ingest', { text, mode, overwrite: $('overwrite').checked, resume: S.current });
    applyState(r); renderAll();
    const n = r.changes.length;
    showResult(r.warning ? 'warn' : '',
      `${r.used ? `已由 ${r.used} 整理：` : ''}${n ? `更新了 ${n} 项` : '没有发现新的内容'}。${r.warning || ''}`
      + (r.missing.length ? ` 还缺 ${r.missing.length} 项，见右侧。` : ' 资料已齐全。'), r.changes);
  } catch (e) { showResult('bad', e.message); }
  finally { btn.disabled = false; btn.textContent = 'AI 整理进资料'; }
}
$('ingest').addEventListener('click', () => {
  const text = $('notes').value.trim();
  if (!text) return toast('知识库是空的：先写点内容或上传简历');
  ingest(text);
});

async function uploadResume(file) {
  if (!file) return;
  if (file.size > 15 * 1024 * 1024) return toast('文件超过 15MB');
  const b64 = await new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result).split(',')[1]); fr.onerror = rej; fr.readAsDataURL(file);
  });
  try {
    showResult('', `正在读取 ${file.name}…`);
    const r = await api('POST', '/resume', { name: file.name, b64, resume: S.current });
    const box = $('notes');
    box.value = (box.value.trim() ? box.value.trim() + '\n\n' : '') + `—— 简历：${r.name} ——\n${r.text}`;
    box.dispatchEvent(new Event('input'));
    S.resume = r.name; $('resume-name').textContent = `这份简历已保存文件：${r.name}`;
    await ingest(r.text);
  } catch (e) { showResult('bad', e.message); }
}
const drop = $('drop');
$('pick').addEventListener('click', () => $('file').click());
$('file').addEventListener('change', (e) => { uploadResume(e.target.files[0]); e.target.value = ''; });
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => uploadResume(e.dataTransfer.files[0]));

// ---------- AI 接口 ----------
let llmDraft = [];

function renderProviders() {
  const box = $('providers'); box.replaceChildren();
  const sel = $('add-preset');
  if (!sel.options.length) {
    for (const [id, p] of Object.entries(S.llm.presets)) sel.append(el('option', { value: id }, p.name));
  }
  if (!llmDraft.length) box.append(el('div', { class: 'card muted' }, '还没有 AI 接口。从下面选一家厂商添加，填上 API Key 即可。'));
  llmDraft.forEach((p, i) => box.append(providerCard(p, i)));
}

function providerCard(p, i) {
  const preset = S.llm.presets[p.preset] || {};
  const bind = (key, attrs = {}) => {
    const inp = el('input', { type: attrs.type || 'text', placeholder: attrs.placeholder || '', list: attrs.list, autocomplete: 'off' });
    inp.value = p[key] ?? '';
    inp.addEventListener('input', () => { p[key] = inp.value; });
    return inp;
  };
  const proto = el('select', {}, Object.entries(S.llm.protocols).map(([k, v]) => el('option', { value: k }, v)));
  proto.value = p.protocol; proto.addEventListener('change', () => (p.protocol = proto.value));
  const dl = el('datalist', { id: `models-${i}` }, (preset.models || []).map((m) => el('option', { value: m })));
  const enabled = el('input', { type: 'checkbox' }); enabled.checked = p.enabled !== false;
  enabled.addEventListener('change', () => (p.enabled = enabled.checked));
  const res = el('span', { class: 'test-res' });
  const testBtn = el('button', { onclick: async () => {
    testBtn.disabled = true; res.className = 'test-res'; res.textContent = '测试中…';
    try {
      const r = await api('POST', '/llm/test', { id: p.id, preset: p.preset, protocol: p.protocol, base_url: p.base_url, model: p.model, api_key: p.api_key });
      res.className = 'test-res ' + (r.ok ? 'ok' : 'bad');
      res.textContent = r.ok ? `✓ 连接成功（${r.ms} ms）` : '✗ ' + r.error;
    } catch (e) { res.className = 'test-res bad'; res.textContent = '✗ ' + e.message; }
    testBtn.disabled = false;
  } }, '测试连接');
  const move = (d) => { const j = i + d; if (j < 0 || j >= llmDraft.length) return; [llmDraft[i], llmDraft[j]] = [llmDraft[j], llmDraft[i]]; renderProviders(); };
  const keyInput = bind('api_key', { type: 'password', placeholder: p.key_mask ? `已保存 ${p.key_mask}（不改就留空）` : preset.no_key ? '本地模型无需 Key' : '粘贴 API Key' });
  return el('div', { class: 'card' },
    el('div', { class: 'prov-h' },
      el('b', {}, p.label || preset.name || p.preset),
      el('span', { class: 'tag ' + (i === 0 ? 'primary' : '') }, i === 0 ? '首选' : `备用 ${i}`),
      p.from_env ? el('span', { class: 'tag' }, '来自 .env') : null,
      el('span', { class: 'spacer' }),
      el('label', { class: 'check' }, enabled, '启用'),
      el('button', { class: 'mini', onclick: () => move(-1), disabled: i === 0 }, '↑'),
      el('button', { class: 'mini', onclick: () => move(1), disabled: i === llmDraft.length - 1 }, '↓'),
      el('button', { class: 'mini danger', onclick: () => { llmDraft.splice(i, 1); renderProviders(); } }, '删除')),
    el('div', { class: 'prov' },
      el('label', { class: 'wide' }, el('span', {}, 'API Key'), keyInput),
      el('label', {}, el('span', {}, '模型'), bind('model', { list: `models-${i}` }), dl),
      el('label', {}, el('span', {}, '协议'), proto),
      el('label', { class: 'wide' }, el('span', {}, '接口地址'), bind('base_url')),
      preset.hint ? el('div', { class: 'wide muted small' }, preset.hint) : null,
      preset.key_url ? el('div', { class: 'wide muted small' }, '没有 Key？到 ', el('a', { href: preset.key_url, target: '_blank', rel: 'noreferrer' }, '这里申请')) : null),
    el('div', { class: 'row' }, testBtn, res));
}

$('add-provider').addEventListener('click', () => {
  const id = $('add-preset').value; const pr = S.llm.presets[id];
  llmDraft.push({ id: null, preset: id, label: pr.name, protocol: pr.protocol, base_url: pr.base_url, model: pr.model, enabled: true, api_key: '' });
  renderProviders();
});

$('save-llm').addEventListener('click', async () => {
  try {
    const r = await api('PUT', '/llm', { providers: llmDraft.map(({ id, preset, protocol, base_url, model, api_key, enabled }) => ({ id, preset, protocol, base_url, model, api_key, enabled })) });
    S.llm = r; llmDraft = r.providers.map((p) => ({ ...p, api_key: '' }));
    S.ai_ready = r.providers.some((p) => p.usable);
    renderProviders(); renderHint(); renderStart(); toast('AI 接口已保存');
  } catch (e) { toast(e.message); }
});

// ---------- 上手向导 ----------
function renderStart() {
  const req = S.missing.filter((m) => m.level === 'required').length;
  $('step-kb').classList.toggle('done', req === 0 && (S.completeness ?? 0) > 0);
  $('step-kb-text').textContent = req === 0 && S.completeness > 0
    ? `资料完整度 ${S.completeness}%，必填项都有了。`
    : `完整度 ${S.completeness ?? 0}%，还有 ${req} 项必填没填。把简历拖进来，或直接粘贴文字就行。`;
  const n = S.llm.providers.filter((p) => p.usable).length;
  $('step-ai').classList.toggle('done', n > 0);
  $('step-ai-text').textContent = n
    ? `已配置 ${n} 家，规则认不出的字段会交给 AI，也能一键整理简历。`
    : '不配也能用（只靠规则填写）；配上后能自动整理简历，并处理规则认不出的字段。支持 DeepSeek、千问、Kimi、Claude、Gemini、OpenAI 等。';
  let done = false;
  try { done = localStorage.getItem('ext-done') === '1'; } catch { /* ignore */ }
  $('ext-done').checked = done;
  $('step-ext').classList.toggle('done', done);
  $('step-go').classList.toggle('done', false);
}
$('ext-done').addEventListener('change', (e) => {
  try { localStorage.setItem('ext-done', e.target.checked ? '1' : '0'); } catch { /* ignore */ }
  renderStart();
});
$('open-ext').addEventListener('click', () => api('POST', '/open', { what: 'extensions' }).catch((e) => toast(e.message)));
$('open-ext-folder').addEventListener('click', () => api('POST', '/open', { what: 'folder' }).catch((e) => toast(e.message)));
$('copy-ext').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(S.extDir); toast('路径已复制'); } catch { toast('复制失败，请手动选中路径'); }
});

// ---------- 启动 ----------
let initial = 'start';
try { initial = localStorage.getItem('tab') || 'start'; } catch { /* ignore */ }
go(initial);
let lastResume = null;
try { lastResume = localStorage.getItem('resume'); } catch { /* ignore */ }
load(lastResume).catch(() => load(null)).catch((e) => toast('加载失败：' + e.message));
