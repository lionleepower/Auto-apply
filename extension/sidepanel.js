// 侧边栏界面：连接本地服务、显示当前页面的公司与内推码、执行 flow.js、展示结果。
const API = 'http://127.0.0.1:8765/api';
const $ = (id) => document.getElementById(id);
const STATUS_TEXT = { filled: '已填', kept: '原值一致', uncertain: '待确认', unfilled: '未填', sensitive: '敏感' };
let currentUrl = '';
// 每个标签页一份任务状态：几个网申页可以同时填，切到哪个标签页就显示哪一页的进度和结果
const jobs = new Map();  // tabId → { busy, stop, progress: { text, cls } | null, report }
let activeTabId = null;
function jobOf(tabId) {
  if (!jobs.has(tabId)) jobs.set(tabId, { busy: false, stop: false, progress: null, report: null });
  return jobs.get(tabId);
}
const busyCount = () => [...jobs.values()].filter((j) => j.busy).length;
const EXEC_TIMEOUT_MS = 30000;

// 页面里的一步如果一直不返回（弹了 alert、页面卡住、跳走了），不能让侧边栏永远等下去
function withTimeout(promise, ms, what) {
  let timer;
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${what}超过 ${ms / 1000} 秒没有响应`)), ms); }),
  ]);
}

async function api(path, body) {
  const init = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  };
  const r = await fetch(API + path, init);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (e) { /* 非 JSON */ }
    throw new Error(`${path}：${detail}`);
  }
  return r.json();
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

const attached = new Set();
async function attachDebugger(tabId) {
  if (attached.has(tabId)) return;
  try {
    await chrome.debugger.attach({ tabId }, '1.3');
  } catch (e) {
    if (!/already attached/i.test(e.message || '')) throw e;
  }
  attached.add(tabId);
}
async function detachDebugger(tabId) {
  if (!attached.has(tabId)) return;
  attached.delete(tabId);
  try { await chrome.debugger.detach({ tabId }); } catch (e) { /* 页面已关闭等 */ }
}
chrome.debugger.onDetach.addListener((src) => attached.delete(src.tabId));

function envFor(tabId) {
  return {
    inject: () => withTimeout(
      chrome.scripting.executeScript({ target: { tabId }, world: 'MAIN', files: ['autofill.js'] }), EXEC_TIMEOUT_MS, '注入脚本'),
    exec: async (func, args = []) => {
      const [r] = await withTimeout(
        chrome.scripting.executeScript({ target: { tabId }, world: 'MAIN', func, args }), EXEC_TIMEOUT_MS, '页面操作');
      return r ? r.result : undefined;
    },
    api,
    // 真实点击：经 Edge 调试接口发送鼠标事件，浏览器认作真人点击。填写期间 Edge 顶部会提示「正在调试此浏览器」
    click: async (x, y) => {
      await attachDebugger(tabId);
      const send = (type, extra) => chrome.debugger.sendCommand({ tabId }, 'Input.dispatchMouseEvent',
        { type, x, y, button: 'left', clickCount: 1, ...extra });
      await send('mouseMoved', { button: 'none', clickCount: 0 });
      await send('mousePressed', { buttons: 1 });
      await send('mouseReleased', { buttons: 0 });
    },
    // 真实键盘输入（远程搜索的学校名称等只认这种）
    key: async (key) => {
      await attachDebugger(tabId);
      const codes = { Escape: 27, Enter: 13 };
      for (const type of ['rawKeyDown', 'keyUp']) {
        await chrome.debugger.sendCommand({ tabId }, 'Input.dispatchKeyEvent',
          { type, key, code: key, windowsVirtualKeyCode: codes[key] || 0 });
      }
    },
    type: async (text) => {
      await attachDebugger(tabId);
      await chrome.debugger.sendCommand({ tabId }, 'Input.insertText', { text });
    },
    progress: (t) => setProgress(tabId, t),
  };
}

// ---------- 服务状态与选项 ----------

async function checkServer() {
  const s = $('status');
  try {
    const h = await api('/health');
    s.className = 'status on';
    $('status-text').textContent = h.name ? `已连接 · ${h.name}` : '已连接';
    $('offline').hidden = true;
    await syncResumePick(h);
    const cur = (h.resumes || []).find((r) => r.id === $('resume-pick').value);
    const file = cur ? cur.file : h.resume;
    $('resume-name').textContent = file ? `（${file}）` : '（这份简历还没上传文件）';
    $('opt-upload').disabled = !file;
    const pf = cur ? cur.portfolio : null;
    $('row-portfolio').hidden = !pf;
    $('portfolio-name').textContent = pf ? `（${pf}）` : '';
    $('opt-llm').disabled = !h.llm.length;
    $('llm-name').textContent = h.llm_label || 'LLM（还没配置 AI 接口）';
    $('opt-llm').dataset.provider = h.llm.length ? 'auto' : 'none';
    setButtons(true);
    return true;
  } catch (e) {
    s.className = 'status off';
    $('status-text').textContent = '未连接';
    $('offline').hidden = false;
    setButtons(false);
    return false;
  }
}

// 多份简历：选用哪一份填表（选择记在插件里，下次打开还在）
async function syncResumePick(h) {
  const list = h.resumes || [];
  const sel = $('resume-pick');
  $('resume-row').hidden = list.length < 2;
  const saved = (await chrome.storage.local.get('resume-pick'))['resume-pick'];
  const keep = sel.value || saved;
  sel.replaceChildren(...list.map((r) => {
    const o = document.createElement('option');
    o.value = r.id;
    o.textContent = `${r.name}${r.locale === 'overseas' ? ' · 海外' : ''}${r.id === h.default_resume ? '（默认）' : ''}`;
    return o;
  }));
  sel.value = list.some((r) => r.id === keep) ? keep : h.default_resume || (list[0] && list[0].id) || '';
}
$('resume-pick').addEventListener('change', () => {
  chrome.storage.local.set({ 'resume-pick': $('resume-pick').value });
  checkServer();
});

let serverOn = false;
function setButtons(on = serverOn) {
  serverOn = on;
  const j = jobOf(activeTabId);
  $('fill').disabled = !on || j.busy;
  $('preview').disabled = !on || j.busy;
  $('diag').disabled = !on || j.busy;
  $('stop').hidden = !j.busy;
  $('stop').disabled = j.stop;
  const n = busyCount();
  const others = n - (j.busy ? 1 : 0);
  $('jobs').hidden = !others;
  $('jobs').textContent = `另有 ${others} 个标签页正在填写（切过去可以看进度）`;
}

// 进度记在这个标签页名下；只有它是当前标签页时才显示出来
function setProgress(tabId, text, cls = 'progress') {
  jobOf(tabId).progress = text ? { text, cls } : null;
  if (tabId === activeTabId) showProgress();
}
function showProgress() {
  const pr = jobOf(activeTabId).progress;
  $('progress').hidden = !pr;
  if (pr) { $('progress').className = pr.cls; $('progress').textContent = pr.text; }
}

// 切换标签页：换成那一页的进度 / 结果
function showTab() {
  const j = jobOf(activeTabId);
  showProgress();
  $('notice').hidden = true;
  if (j.report) render(j.report, activeTabId);
  else $('result').hidden = true;
  setButtons();
}

const OPTS = ['opt-upload', 'opt-portfolio', 'opt-overwrite', 'opt-llm', 'opt-save'];
async function loadOptions() {
  const saved = await chrome.storage.local.get(OPTS);
  OPTS.forEach((k) => { if (k in saved) $(k).checked = saved[k]; });
  if (!('opt-llm' in saved)) $('opt-llm').checked = true;
  OPTS.forEach((k) => $(k).addEventListener('change', () => chrome.storage.local.set({ [k]: $(k).checked })));
}

// ---------- 当前页面 ----------

async function refreshContext() {
  const tab = await activeTab();
  if (!tab) return;
  if (tab.id !== activeTabId) {
    activeTabId = tab.id;
    currentUrl = '';
    showTab();
  }
  if (!tab.url || tab.url === currentUrl) return;
  if (currentUrl && !jobOf(tab.id).busy) {  // 同一个标签页换了网址：上一页的结果不再适用
    jobOf(tab.id).report = null;
    showTab();
  }
  currentUrl = tab.url;
  let host = '';
  try { host = new URL(tab.url).host; } catch (e) { /* chrome:// 等 */ }
  $('company').textContent = tab.title || host || '当前页面';
  $('ats').textContent = host;
  $('referral').value = '';
  try {
    const c = await api('/context?url=' + encodeURIComponent(tab.url));
    if (c.company) $('company').textContent = c.company;
    $('ats').textContent = [c.ats || host, c.deadline && `截止 ${c.deadline}`].filter(Boolean).join(' · ');
    $('referral').value = c.referral || '';
    $('referral').placeholder = c.referral ? '' : '投递表里没找到，可以手动填';
  } catch (e) { /* 服务没开时 checkServer 会提示 */ }
}

// ---------- 执行 ----------

async function start(dryRun) {
  const tab = await activeTab();
  if (!tab || !/^https?:|^file:/.test(tab.url || '')) {
    showError('这个页面不能填写（只支持普通网页）');
    return;
  }
  const job = jobOf(tab.id);
  if (job.busy) return;
  job.busy = true;
  job.stop = false;
  job.report = null;
  setButtons();
  $('result').hidden = true;
  $('notice').hidden = true;
  try {
    const report = await AutofillFlow.run(envFor(tab.id), {
      url: tab.url,
      dryRun,
      upload: $('opt-upload').checked && !$('opt-upload').disabled,
      overwrite: $('opt-overwrite').checked,
      llm: $('opt-llm').checked && !$('opt-llm').disabled ? $('opt-llm').dataset.provider : 'none',
      referral: $('referral').value.trim(),
      resume: $('resume-pick').value || null,
      portfolio: !$('row-portfolio').hidden && $('opt-portfolio').checked,
      save: $('opt-save').checked,
      shouldStop: () => job.stop,
    });
    job.report = report;
    setProgress(tab.id, null);
    if (tab.id === activeTabId) render(report, tab.id);
  } catch (e) {
    showError(e.message || String(e), tab.id);
    checkServer();
  } finally {
    await detachDebugger(tab.id);  // 填完立刻断开，「正在调试」的提示条随之消失
    job.busy = false;
    job.stop = false;
    setButtons();
  }
}

// 导出诊断：把下拉 / 日期控件打开后的真实结构存到本地 runs/，用来适配填不上的站点
async function exportDiag() {
  const tab = await activeTab();
  if (!tab || !/^https?:/.test(tab.url || '')) return showError('这个页面不能诊断');
  const job = jobOf(tab.id);
  if (job.busy) return;
  job.busy = true;
  setButtons();
  const env = envFor(tab.id);
  try {
    env.progress('正在逐个打开下拉框和日期控件，记录结构（不会填写任何值）…');
    await env.inject();
    const data = await withTimeout(
      chrome.scripting.executeScript({ target: { tabId: tab.id }, world: 'MAIN', func: () => window.__autofill.diagnose(15) })
        .then(([r]) => r && r.result), 120000, '诊断');
    if (!data || !data.probes) throw new Error('页面没有返回诊断结果（刷新网申页后再试一次）');
    const r = await api('/diag', { data });
    const failed = data.probes.filter((p) => p.error).length;
    setProgress(tab.id, null);
    if (tab.id === activeTabId) notice(`✅ 诊断已导出：记录了 ${r.probes} 个控件${failed ? `（其中 ${failed} 个出错，也一并记下了）` : ''}`, r.file);
  } catch (e) {
    showError(e.message || String(e), tab.id);
  } finally {
    job.busy = false;
    setButtons();
  }
}

// 醒目的结果提示（成功 / 失败都留在面板上，不会一闪而过）
function notice(text, file) {
  const box = $('notice');
  box.replaceChildren();
  box.className = 'notice ok';
  const t = document.createElement('div');
  t.textContent = text;
  box.appendChild(t);
  if (file) {
    const f = document.createElement('div');
    f.className = 'muted';
    f.textContent = file;
    const open = document.createElement('button');
    open.className = 'link';
    open.textContent = '打开所在文件夹';
    open.addEventListener('click', () => api('/reveal', { file }).catch((e) => showError(e.message)));
    box.append(f, open);
  }
  box.hidden = false;
}

function showError(msg, tabId = activeTabId) {
  setProgress(tabId, '出错了：' + msg, 'progress error');
}

function render(report, tabId) {
  const s = report.summary;
  const chips = $('chips');
  chips.replaceChildren();
  const add = (cls, label, n) => {
    if (!n) return;
    const c = document.createElement('span');
    c.className = 'chip ' + cls;
    c.textContent = label;
    const b = document.createElement('b');
    b.textContent = n;
    c.appendChild(b);
    chips.appendChild(c);
  };
  if (report.dry_run) add('filled', '将填写', s.planned);
  add('filled', '已填', s.filled);
  add('filled', '原值一致', s.kept);
  add('uncertain', '待确认', s.uncertain);
  add('unfilled', '未填', s.unfilled);
  add('sensitive', '敏感', s.sensitive);

  $('upload-note').textContent = [report.upload, report.llm_error && `LLM 调用失败：${report.llm_error}`]
    .filter(Boolean).join('；');

  // 分区保存结果：没保存成功的，把网页上的报错原样列出来（多半是资料里缺必填项）
  const secs = $('sections');
  secs.replaceChildren();
  (report.sections || []).filter((x) => x.saved !== null && x.saved !== undefined).forEach((x) => {
    const li = document.createElement('li');
    li.textContent = x.saved ? `✅ 「${x.section}」已保存`
      : `⚠ 「${x.section}」没保存成功：${[...new Set(x.errors)].join('、') || '原因不明'}`;
    secs.appendChild(li);
  });

  // 只列真正要你处理的：不列「不自动填写」的（证书 / 专利 / 星级）、勾选框（至今 / 协议）、
  // 没标签又认不出的小控件（区号、证件类型）；多段经历里同一个字段合并成一条
  const groups = new Map();
  report.items
    .filter((it) => ['uncertain', 'unfilled', 'sensitive'].includes(it.status))
    .filter((it) => it.key !== '__ignore__' && it.kind !== 'checkbox' && (it.label || it.key))
    .forEach((it) => {
      const k = [it.label, it.section_kind, it.status, it.note.replace(/「[^」]*」/g, '')].join('|');
      const g = groups.get(k);
      if (g) g.count += 1;
      else groups.set(k, { ...it, count: 1 });
    });
  const todo = [...groups.values()];
  const ul = $('todo');
  ul.replaceChildren();
  todo.forEach((it) => {
    const li = document.createElement('li');
    const tag = document.createElement('span');
    tag.className = 'tag ' + it.status;
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = `${it.label || '（无标签）'}${it.count > 1 ? ` ×${it.count}` : ''}`;
    const where = document.createElement('span');
    where.className = 'muted';
    where.textContent = it.section ? ` · ${it.section}` : '';
    const note = document.createElement('div');
    note.className = 'note';
    note.textContent = `${STATUS_TEXT[it.status] || it.status}：${it.note || ''}`;
    li.append(tag, label, where, note);
    li.title = '点击跳到这个字段';
    li.addEventListener('click', () => chrome.scripting.executeScript({
      target: { tabId }, world: 'MAIN', args: [it.id],
      func: (id) => {
        const el = document.querySelector('[data-af-id="' + id + '"]');
        if (!el) return;
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        const old = el.style.boxShadow;
        el.style.boxShadow = '0 0 0 4px rgba(37,99,235,.45)';
        setTimeout(() => { el.style.boxShadow = old; }, 1600);
      },
    }));
    ul.appendChild(li);
  });
  $('todo-title').hidden = !todo.length;

  const un = $('unused');
  un.replaceChildren();
  (report.unused || []).forEach((u) => {
    const li = document.createElement('li');
    li.textContent = u;
    un.appendChild(li);
  });
  $('unused-title').hidden = !(report.unused || []).length;
  $('result').hidden = false;
}

// ---------- 启动 ----------

$('fill').addEventListener('click', () => start(false));
$('preview').addEventListener('click', () => start(true));
$('stop').addEventListener('click', () => {
  const j = jobOf(activeTabId);
  if (!j.busy) return;
  j.stop = true;
  setButtons();
  setProgress(activeTabId, '正在停止…');
});
$('diag').addEventListener('click', exportDiag);
$('retry').addEventListener('click', async () => { if (await checkServer()) { currentUrl = ''; refreshContext(); } });
chrome.tabs.onActivated.addListener(() => refreshContext());
chrome.tabs.onUpdated.addListener((_, info, tab) => { if (tab.active && (info.url || info.status === 'complete')) refreshContext(); });
chrome.tabs.onRemoved.addListener((tabId) => {  // 标签页关了：停掉它的任务，忘掉它的结果
  const j = jobs.get(tabId);
  if (j) j.stop = true;
  if (j && !j.busy) jobs.delete(tabId);
  setButtons();
});
// 侧边栏是按窗口开的：另一个窗口里的标签页拖进来、或换了窗口焦点时也要刷新
chrome.windows.onFocusChanged.addListener(() => refreshContext());

(async () => {
  await loadOptions();
  await checkServer();
  await refreshContext();
  setInterval(() => { if (!busyCount()) checkServer(); }, 15000);
})();
