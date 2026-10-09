// 页面内运行的核心逻辑：字段抽取、填写、高亮。
// 不依赖 Playwright，也不依赖任何框架，后续 Chrome 插件的 content script 可直接复用本文件。
// 约定：本文件从不点击提交类按钮。
(() => {
  // 同一版本已注入就跳过；版本不同（插件更新过）就整个替换掉旧代码。
  // 以前只判断「有没有」，结果插件更新后，开着的网申页里跑的还是旧代码。
  const VERSION = '5c0bde8a671b';
  if (window.__autofill && window.__autofill.version === VERSION) return;

  const ATTR = 'data-af-id';
  const ADD_ATTR = 'data-af-add';
  const SKIP_INPUT = new Set(['hidden', 'submit', 'button', 'reset', 'image', 'password', 'search']);
  // 自动补全输入框（讯飞学校名称：phoenix-auto-complete-container）按可搜索下拉处理：输入关键词再从建议里选
  const CUSTOM_SELECT_SEL = '.ant-select, .ant-cascader-picker, .el-select, .el-cascader, [role="combobox"], [class*="auto-complete-container"], [class*="autocomplete-container"]';
  const CUSTOM_SELECT_CLASS = /(^|\s)[a-z]+(-[a-z]+)*-select(\s|$)/i;
  // 北森 phoenix 的选项是 li.phoenix-selectList__listItem，地区选择器是 .area-text-label（2026-10-07 讯飞诊断）
  const OPTION_SEL = '[role="option"], .ant-select-item-option, .el-select-dropdown__item, [class*="option"], [class*="select-item"], '
    + '[class*="dropdown"] li, [class*="menu-item"], [class*="listItem"], [class*="list-item"], [class*="area-text-label"], '
    + '.ud__select__list__item';  // 飞书招聘。精确类名：选项里还嵌着 ud__select__list__item__check，宽松匹配会让选项被当成「含子选项」而排除（2026-10-09 得物诊断）
  const HEADING_SEL = 'h1, h2, h3, h4, h5, h6, legend, [class*="title"], [class*="Title"], [class*="header"], [class*="Header"]';
  const PICKER_SEL = '.ant-picker, .el-date-editor, [class*="date-picker"], [class*="datepicker"], [class*="DatePicker"], [class*="month-picker"]';
  const PANEL_SEL = '.ant-picker-dropdown, .el-picker-panel, [class*="picker-panel"], [class*="picker-dropdown"], [class*="calendar"], [class*="datepicker"], [class*="date-picker"]';
  const DATE_PH_RE = /日期|时间|年月|年份|月份|date|month|yyyy/i;
  const ADD_RE = /^[+＋]?\s*(添加|新增|增加)/;
  const DANGER_RE = /提交|投递|申请|确认|保存|submit|apply|confirm/i;

  // id 从页面上已有的最大编号往后接，替换旧版本代码后也不会和旧 id 撞车
  let seq = 1 + Math.max(-1, ...[...document.querySelectorAll('[data-af-id], [data-af-add]')]
    .map((e) => parseInt((e.getAttribute('data-af-id') || e.getAttribute('data-af-add') || '').slice(1), 10))
    .filter((n) => !Number.isNaN(n)));
  // window.__autofillDebug = true 时把面板导航过程打到 console，排查真实站点用
  const log = (...a) => { if (window.__autofillDebug) console.log('[autofill]', ...a); };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function visible(el) {
    if (!el || !el.getClientRects().length) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  }

  function clean(t) {
    return (t || '').replace(/\s+/g, ' ').replace(/^[\s*＊:：]+|[\s*＊:：]+$/g, '').trim();
  }

  // 「221 / 2000」字数计数器、「请输入」「选填」这类占位文字不是标签（讯飞、OPPO 实测）
  // 字数提示（「超出 267 个字符」「还能输入 20 字」）也不是标签（得物 2026-10-09：超限后题目被它顶替了）
  const NOT_LABEL = /^(\d+\s*\/\s*\d+|请输入|请填写|请选择|选填|必填|请输入内容|超出\s*\d+\s*个?(字|字符)|还(能|可)输入\s*\d+\s*个?(字|字符)?)$/;
  function cleanLabel(t) {
    return clean(clean(t).replace(/\d+\s*\/\s*\d+\s*$/, '').replace(/\s*(必填|选填)$/, ''));
  }

  function norm(t) {
    return (t || '').toLowerCase().replace(/[\s\-_.·・()（）【】\[\]:：,，/]/g, '');
  }

  // ---------- 控件识别 ----------

  function isCustomSelectRoot(el) {
    if (el.matches(CUSTOM_SELECT_SEL)) return true;
    return typeof el.className === 'string' && CUSTOM_SELECT_CLASS.test(el.className);
  }

  function outermostCustomSelect(el) {
    let root = null;
    for (let a = el; a && a !== document.body; a = a.parentElement) {
      // 名字像下拉、里面却装着好几个下拉的，是分组容器（OPPO 的 cascade-select：国家 / 省 / 市三个下拉）
      if (isCustomSelectRoot(a) && a.querySelectorAll(CUSTOM_SELECT_SEL).length <= 1) root = a;
    }
    return root;
  }

  // div 画的单选组（北森 phoenix-radio-group：没有 <input type=radio>，性别 / 是否为最高学历 都是这种）
  const DIV_RADIO_ITEM = '[role="radio"], [class*="radioItem"], [class*="radio-item"], [class*="radio_item"]';
  function divRadioItems(group) {
    const items = [...group.querySelectorAll(DIV_RADIO_ITEM)].filter((e) => visible(e) && clean(e.innerText));
    return items.filter((e) => !items.some((o) => o !== e && e.contains(o)));
  }
  function divRadioOn(item) {
    if (item.getAttribute('aria-checked') === 'true') return true;
    return [item, ...item.querySelectorAll('*')].some((e) =>
      /(^|[\s_-])(is-)?(checked|active|selected)(\s|$)|--(checked|active|selected)|-checked(\s|$)/i
        .test(typeof e.className === 'string' ? e.className : ''));
  }

  // 在弹层里（下拉 / 弹出框）。不算 body / html：Element Plus 开着对话框时给 body 加 el-popup-parent--hidden
  function inLayer(el, sel) {
    const layer = el.closest(sel);
    return !!layer && layer !== document.body && layer !== document.documentElement;
  }

  function collectControls() {
    const out = [];
    const seen = new Set();
    const radioGroups = new Map();
    const inSeen = (el) => [...seen].some((r) => r.contains(el));

    // 日期选择器（含范围选择器）：取最外层的 picker 容器
    document.querySelectorAll(PICKER_SEL).forEach((el) => {
      if (el.closest('.ant-picker-dropdown, .el-picker-panel, [class*="picker-panel"], [class*="picker-dropdown"]')) return;
      let root = el;
      for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) if (a.matches(PICKER_SEL)) root = a;
      if (seen.has(root) || inSeen(root) || !visible(root) || !root.querySelector('input')) return;
      seen.add(root);
      out.push({ el: root, kind: root.querySelectorAll('input').length >= 2 ? 'date_range' : 'date_picker' });
    });

    // 自定义下拉
    document.querySelectorAll(CUSTOM_SELECT_SEL + ', [class*="-select"]').forEach((el) => {
      if (!isCustomSelectRoot(el) || el.tagName === 'SELECT') return;
      const root = outermostCustomSelect(el);
      if (!root || seen.has(root) || inSeen(root) || !visible(root)) return;
      // 下拉弹层里的东西不算控件
      if (inLayer(root, '[class*="dropdown"], [class*="popup"], [class*="popper"]')) return;
      seen.add(root);
      out.push({ el: root, kind: /cascader/i.test(root.className) ? 'cascader' : 'custom_select' });
    });

    document.querySelectorAll('input, textarea, select').forEach((el) => {
      if (el.closest('[' + 'data-af-panel' + ']')) return;
      if ([...seen].some((r) => r.contains(el))) return;
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (tag === 'input' && SKIP_INPUT.has(type)) return;
      // 下拉弹层里的勾选框 / 单选框是选项，不是表单字段（讯飞多选「其他开发语言」）
      if ((type === 'checkbox' || type === 'radio')
        && inLayer(el, '[class*="selectList"], [class*="dropdown"], [class*="popper"], [class*="popup"], [class*="unmodeled-layer"]')) return;
      if (type === 'radio') {
        // AntD 的单选常常没有 name，按 radio-group 容器分组
        const groupEl = el.closest('[role="radiogroup"], [class*="radio-group"], [class*="RadioGroup"]');
        if (groupEl && !groupEl.hasAttribute('data-af-rg')) groupEl.setAttribute('data-af-rg', 'g' + seq++);
        const key = el.name || (groupEl ? 'rg:' + groupEl.getAttribute('data-af-rg') : '__noname__' + seq++);
        if (!radioGroups.has(key)) radioGroups.set(key, []);
        radioGroups.get(key).push(el);
        return;
      }
      if (type !== 'file' && !visible(el)) return;
      // 下拉弹层里自带的「搜索」框不是表单字段（北森：弹层没关时会被当成一堆「搜索」字段）
      if (/^(搜索|请搜索|search)$/i.test((el.getAttribute('placeholder') || '').trim())) return;
      let kind = 'text';
      if (tag === 'textarea') kind = 'textarea';
      else if (tag === 'select') kind = 'select';
      else if (type === 'file') kind = 'file';
      else if (type === 'checkbox') kind = 'checkbox';
      else if (type === 'date' || type === 'month') kind = type;
      else if (el.readOnly && DATE_PH_RE.test(el.getAttribute('placeholder') || '')) kind = 'date_picker';
      out.push({ el, kind });
    });

    document.querySelectorAll('[role="radiogroup"], [class*="radio-group"], [class*="RadioGroup"]').forEach((g) => {
      if (g.querySelector('input[type="radio"]') || seen.has(g) || inSeen(g) || !visible(g)) return;
      if (g.parentElement && g.parentElement.closest('[role="radiogroup"], [class*="radio-group"]')) return;  // 只要最外层
      if (divRadioItems(g).length < 2) return;
      seen.add(g);
      out.push({ el: g, kind: 'radio', divRadio: true });
    });

    radioGroups.forEach((radios) => {
      if (!radios.some((r) => visible(r) || visible(r.closest('label') || r.parentElement))) return;
      let root = radios[0].closest('[data-af-rg]') || radios[0].parentElement;
      while (root && !radios.every((r) => root.contains(r))) root = root.parentElement;
      if (root) out.push({ el: root, kind: 'radio', radios });
    });

    // 按文档顺序排列
    out.sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
    return out;
  }

  // ---------- 标签与分区 ----------

  function textExcluding(container, exclude) {
    let s = '';
    const w = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) {
      const n = w.currentNode;
      if (exclude.contains(n)) continue;
      if (n.parentElement.closest('select, textarea, script, style, button, [' + ATTR + '], [' + ADD_ATTR + ']')) continue;
      s += ' ' + n.nodeValue;
    }
    return clean(s);
  }

  // 同一表单项里多个控件、其中一个前面紧挨着自己的小标签（OPPO「年级成绩排名」：下拉 + 「GPA/CGPA」输入框）
  function inlineLabel(root) {
    const n = (e) => e.querySelectorAll('input, textarea, select').length || 1;
    for (let w = root, i = 0; w && w.parentElement && i < 4; w = w.parentElement, i++) {
      if (w !== root && clean(w.innerText)) return '';  // 到了带文字的整行（标签 + 输入框），交给常规规则
      if (n(w.parentElement) <= n(w)) continue;
      const prev = w.previousElementSibling;
      if (prev && !prev.querySelector('input, textarea, select') && /name|label|title/i.test(String(prev.className))) {
        const t = cleanLabel(prev.innerText);
        if (t && t.length <= 20 && !NOT_LABEL.test(t)) return t;
      }
      return '';
    }
    return '';
  }

  function findLabel(root) {
    const own = inlineLabel(root);
    if (own) return own;
    // aria-labelledby 可能挂在外层容器上（OPPO：证件类型、联系电话的标签是 div，靠它关联）。
    // 只看包住不超过 3 个控件的容器，免得拿到整个分区 / 标签页的名字
    for (let a = root, i = 0; a && i < 6; a = a.parentElement, i++) {
      if (a !== root && a.querySelectorAll('[' + ATTR + ']').length > 3) break;
      const ids = a.getAttribute && a.getAttribute('aria-labelledby');
      if (!ids) continue;
      const t = cleanLabel(ids.split(/\s+/).map((id) => document.getElementById(id)?.innerText || '').join(' '));
      if (t && !NOT_LABEL.test(t)) return t.slice(0, 80);
    }
    const by = root.getAttribute('aria-labelledby');
    if (by) {
      const t = clean(by.split(/\s+/).map((id) => document.getElementById(id)?.innerText || '').join(' '));
      if (t) return t;
    }
    // AntD 的 label[for] 指向控件内部 input 的 id。有的站点 id 会重复（小红书「论文」和「竞赛」都叫
    // resumePaperInfo_0_name），所以从近到远在祖先里找，取离控件最近的那个 label
    for (const node of [root, ...root.querySelectorAll('input, textarea, select')]) {
      if (!node.id) continue;
      const sel = 'label[for="' + CSS.escape(node.id) + '"]';
      for (let a = root.parentElement; a; a = a.parentElement) {
        const l = a.querySelector(sel);
        if (l) {
          if (clean(l.innerText)) return clean(l.innerText);
          break;
        }
      }
    }
    // 下拉控件（飞书 combobox：隐藏的只读 input + 旁边显示选中值的节点）：整个控件都不能算进标签文字，
    // 否则选过之后再填一次，标签就变成了选中的值（2026-10-09 得物二次填写：性别 → 男、学历 → 硕士）
    let ctl = root;
    for (let c = root.parentElement, i = 0; c && i < 6; c = c.parentElement, i++) {
      if (c.querySelectorAll('[' + ATTR + ']').length > 1) break;
      if (/select|picker|cascader|combobox/i.test(typeof c.className === 'string' ? c.className : '')) ctl = c;
    }
    let a = root.parentElement;
    // 往上最多找 9 层（讯飞的自动补全输入框离表单项标签有 7 层）；遇到包住多个控件的容器就停
    for (let i = 0; a && i < 9; i++, a = a.parentElement) {
      if (a.querySelectorAll('[' + ATTR + ']').length > 1) {
        // 几个控件共用一个标签（「起止时间」下面两个日期框）：取同一表单项里的 label
        for (const host of [a, a.parentElement]) {
          const l = host && [...host.children].find((c) => (c.tagName === 'LABEL'
            || /label/i.test(typeof c.className === 'string' ? c.className : '')) && !c.querySelector('[' + ATTR + ']'));
          const t = l && cleanLabel(l.innerText);
          if (t && !NOT_LABEL.test(t)) return t.slice(0, 80);
        }
        break;
      }
      if (ctl.contains(a)) continue;  // 还在下拉控件自己的容器里：里面是「已选的值」，不是标签
      const t = cleanLabel(textExcluding(a, ctl));
      if (t && !NOT_LABEL.test(t)) return t.slice(0, 80);
    }
    const fallback = cleanLabel(root.getAttribute('aria-label') || root.getAttribute('placeholder') || '');
    return NOT_LABEL.test(fallback) ? '' : fallback;
  }

  function ownText(el) {
    return clean([...el.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.nodeValue).join(''));
  }

  // Tailwind 之类的站点，分区标题 class 里没有 title/header，只能看字重
  function looksLikeHeading(el) {
    const t = ownText(el);
    if (t.length < 2 || t.length > 20) return false;
    const s = getComputedStyle(el);
    return (parseInt(s.fontWeight, 10) || 400) >= 500 && parseFloat(s.fontSize) >= 14;
  }

  function headingCandidates() {
    const styled = [...document.querySelectorAll('div, span, p, strong, b')].filter(looksLikeHeading);
    const styledSet = new Set(styled);
    const all = [...new Set([...document.querySelectorAll(HEADING_SEL), ...styled])];
    all.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
    return all.filter((h) => {
      if (!visible(h) || h.closest('[data-af-panel]')) return false;
      if (h.closest('label, button, a, [role="button"], [class*="form-item-label"], [class*="dropdown"], [class*="select"], [' + ADD_ATTR + ']')) return false;
      if (h.querySelector('input, textarea, select, [' + ATTR + ']')) return false;
      const t = clean(h.innerText);
      if (t.length < 2 || t.length > 20) return false;
      if (/^(必填|选填|必选|可选|非必填)$/.test(t)) return false;  // 标题旁的「必填」角标不是分区名（OPPO）
      // 表单项自己的 label（祖父级只包住一个控件）不算分区标题；按字重识别出的标题不受此限
      // （「是否服从调剂」这种分区下只有一个控件）
      if (styledSet.has(h) && !h.querySelector('*:not(div)')) return true;
      let a = h.parentElement;
      for (let i = 0; a && i < 2; i++, a = a.parentElement) {
        if (a.querySelectorAll('[' + ATTR + ']').length === 1) return false;
      }
      return true;
    });
  }

  // 返回在 el 之前的分区标题，最近的在前
  function sectionTrail(el, headings) {
    const before = [];
    for (const h of headings) {
      if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) before.push(clean(h.innerText));
      else break;
    }
    return before.reverse().slice(0, 5);
  }

  function sectionOf(el, headings) {
    return sectionTrail(el, headings)[0] || '';
  }

  function radioLabel(r) {
    const l = r.closest('label') || (r.id && document.querySelector('label[for="' + CSS.escape(r.id) + '"]'));
    if (l) return clean(l.innerText);
    return clean(r.nextSibling?.textContent || r.parentElement?.innerText || r.value);
  }

  function currentValue(c) {
    const el = c.el;
    if (c.kind === 'radio' && c.divRadio) {
      const on = divRadioItems(el).find(divRadioOn);
      return on ? clean(on.innerText) : '';
    }
    if (c.kind === 'radio') return clean((c.radios.find((r) => r.checked) && radioLabel(c.radios.find((r) => r.checked))) || '');
    if (c.kind === 'checkbox') return el.checked ? 'true' : '';
    if (c.kind === 'date_range') return [...el.querySelectorAll('input')].map((i) => i.value).filter(Boolean).join(' ~ ');
    if (c.kind === 'date_picker' && el.tagName !== 'INPUT') return el.querySelector('input')?.value || '';
    if (c.kind === 'custom_select' || c.kind === 'cascader') {
      const item = el.querySelector('[class*="selection-item"], [class*="selected"], [class*="value"]');
      if (item) {
        const t = clean(item.innerText);
        const isPlaceholder = /placeholder|empty/i.test(item.className) || /^(请选择|请输入|select|choose)/i.test(t);
        return isPlaceholder ? '' : t;
      }
      const inp = el.querySelector('input');
      return clean(inp && inp.value);
    }
    if (c.kind === 'select') {
      const o = el.options[el.selectedIndex];
      return o && o.value !== '' ? clean(o.text) : '';
    }
    return el.value || '';
  }

  // ---------- 对外 API ----------

  function extract() {
    const controls = collectControls();
    const used = new Set();
    controls.forEach((c) => {
      let id = c.el.getAttribute(ATTR);
      // 克隆出来的段落会带着旧 id，重新分配
      if (!id || used.has(id)) {
        id = 'f' + seq++;
        c.el.setAttribute(ATTR, id);
      }
      used.add(id);
      c.id = id;
    });
    const headings = headingCandidates();
    const fields = controls.map((c) => {
      const el = c.el;
      let options = [];
      if (c.kind === 'select') options = [...el.options].filter((o) => o.value !== '').map((o) => clean(o.text));
      if (c.kind === 'radio') options = c.divRadio ? divRadioItems(el).map((e) => clean(e.innerText)) : c.radios.map(radioLabel);
      const inner = ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) ? el : el.querySelector('input');
      return {
        id: c.id,
        kind: c.kind,
        label: findLabel(el),
        placeholder: clean((inner && inner.getAttribute('placeholder')) || (/select|cascader/.test(c.kind) ? el.innerText : '')),
        section: sectionOf(el, headings),
        sectionTrail: sectionTrail(el, headings),
        // 结构提示：AntD 之类会把表单路径写进 id，如 resumeVo_resumeEducationInfo_0_startDate
        hint: (inner && (inner.id || inner.name)) || el.id || el.getAttribute('name') || '',
        multiple: /select-multiple/.test(typeof el.className === 'string' ? el.className : ''),
        options,
        required: !!(inner && inner.required) || /[*＊]/.test(el.closest('div')?.parentElement?.innerText?.slice(0, 3) || ''),
        readonly: !!(inner && inner.readOnly && !/select|cascader/.test(c.kind)),
        // 网站锁住的框（OPPO：选了学校后「国家」自动带出且不可改）
        disabled: !!(inner && inner.disabled) || /(^|[\s_-])(is-)?disabled/.test(typeof el.className === 'string' ? el.className : ''),
        value: currentValue(c),
      };
    });

    const addButtons = [];
    document.querySelectorAll('button, a, [role="button"], span, div').forEach((el) => {
      if (!visible(el) || el.closest('[data-af-panel]')) return;
      const t = clean(el.innerText);
      if (!t || t.length > 14 || !ADD_RE.test(t) || DANGER_RE.test(t)) return;
      // 只要最内层的那个元素
      if ([...el.children].some((ch) => ADD_RE.test(clean(ch.innerText)))) return;
      let id = el.getAttribute(ADD_ATTR);
      if (!id) {
        id = 'a' + seq++;
        el.setAttribute(ADD_ATTR, id);
      }
      addButtons.push({ id, text: t, section: sectionOf(el, headings), sectionTrail: sectionTrail(el, headings) });
    });
    return { fields, addButtons };
  }

  // 选项打分：完全相同 > 以候选开头 > 包含候选（多出来的字越少越好）> 候选包含选项。
  // 只按「包含」会把「利物浦大学」选成排在前面的「西交利物浦大学」（2026-10-07 OPPO 实测）
  function pickOption(texts, candidates) {
    const opts = texts.map((t) => norm(t));
    for (const cand of candidates) {
      const c = norm(cand);
      if (!c) continue;
      let best = -1;
      let bestScore = -1;
      opts.forEach((o, i) => {
        if (!o) return;
        let score = -1;
        if (c.length <= 2 && /^[a-z0-9+#.]+$/.test(c)) {  // 只对「C」「R」这类英文短词；「北京」「上海」照常
          if (o === c || o.split(/[^a-z0-9+#一-龥]+/).includes(c)) score = 1000;
          if (score > bestScore) { bestScore = score; best = i; }
          return;
        }
        if (o === c) score = 1000;
        else if (o.startsWith(c)) score = 800 - (o.length - c.length);
        else if (o.includes(c)) score = 600 - (o.length - c.length) - o.indexOf(c) * 5;
        else if (o.length >= 2 && c.includes(o)) score = 300 + o.length;
        if (score > bestScore) { bestScore = score; best = i; }
      });
      if (best >= 0) return best;
    }
    return -1;
  }


  function setNativeValue(el, v) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function press(el) {
    // 完整的「指针 + 鼠标」事件序列，带元素中心坐标：有的组件库只听 pointerdown，有的要坐标判断点在哪
    const r = el.getBoundingClientRect();
    const o = { bubbles: true, cancelable: true, view: window, button: 0, buttons: 1,
      clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
    const p = { ...o, pointerId: 1, pointerType: 'mouse', isPrimary: true };
    el.dispatchEvent(new PointerEvent('pointerover', p));
    el.dispatchEvent(new PointerEvent('pointerenter', p));
    el.dispatchEvent(new MouseEvent('mouseover', o));
    el.dispatchEvent(new PointerEvent('pointerdown', p));
    el.dispatchEvent(new MouseEvent('mousedown', o));
    if (typeof el.focus === 'function') el.focus({ preventScroll: true });
    el.dispatchEvent(new PointerEvent('pointerup', { ...p, buttons: 0 }));
    el.dispatchEvent(new MouseEvent('mouseup', { ...o, buttons: 0 }));
    el.dispatchEvent(new MouseEvent('click', { ...o, buttons: 0 }));
  }

  function visibleOptions() {
    return [...document.querySelectorAll(OPTION_SEL)].filter((o) =>
      visible(o) && !o.closest('[data-af-panel]') && clean(o.innerText) && !o.querySelector(OPTION_SEL));
  }

  function dismiss(trigger) {
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    // 有驱动时再补一个真实 Esc（北森 phoenix 不认模拟按键，弹层会一直开着）
    if (window.__afClickQueueEnabled) window.__afClickQueue.push({ key: 'Escape', res: () => {} });
  }

  async function waitFor(fn, ms) {
    for (let t = 0; t < ms; t += 80) {
      const v = fn();
      if (v && (!Array.isArray(v) || v.length)) return v;
      await sleep(80);
    }
    return fn();
  }

  // 真实点击：驱动（Playwright / 插件）开启点击队列时，把坐标交给它，用浏览器认可的方式点；
  // 没有驱动时退回模拟点击。Element Plus 等对脚本模拟的点击不响应（2026-10-07 OPPO 诊断：15 个控件 12 个打不开）
  async function tap(el) {
    if (!window.__afClickQueueEnabled) { press(el); return; }
    let x;
    let y;
    let hit = null;
    // 弹层刚打开时还在做展开动画（尺寸从 0 长大、位置在变），这时滚动 / 量坐标都会错位（插件的真实点击回得快，常碰上）。
    // 先等元素站稳：尺寸不为 0、连续两次测量位置不变，再滚到视野里点
    let prev = null;
    for (let i = 0; i < 10 && el.isConnected; i++) {
      const r = el.getBoundingClientRect();
      const k = [r.left, r.top, r.width, r.height].map(Math.round).join();
      if (r.width && r.height && k === prev) break;
      prev = k;
      await sleep(50);
    }
    for (const block of ['nearest', 'center']) {
      el.scrollIntoView({ block, inline: 'nearest' });
      await sleep(40);
      const r = el.getBoundingClientRect();
      x = r.left + r.width / 2;
      y = r.top + r.height / 2;
      hit = r.width && r.height ? document.elementFromPoint(x, y) : null;
      log('tap', block, clean(el.innerText).slice(0, 10), Math.round(x), Math.round(y), hit && hit.className);
      if (hit && (hit === el || el.contains(hit))) break;
      hit = null;
    }
    if (!hit) { press(el); return; }  // 滚到中间还被挡住，就别乱点
    await new Promise((res) => window.__afClickQueue.push({ x, y, res }));
    await sleep(80);
  }

  // 真实键盘输入：驱动开启队列时交给它（Playwright keyboard / 插件调试接口），否则直接设值
  async function typeText(input, text) {
    input.focus();
    setNativeValue(input, '');
    if (window.__afClickQueueEnabled) {
      await new Promise((res) => window.__afClickQueue.push({ text, res }));
      await sleep(100);
    } else {
      setNativeValue(input, text);
    }
  }

  const visibleInputs = () => [...document.querySelectorAll('input')].filter((i) => visible(i) && !i.disabled);

  // 依次尝试几种打开方式，打开了就停：外层包装 → 先聚焦再按输入框 → 箭头 / 图标 → 根元素。
  // Element Plus 的日期框：按在没聚焦的 <input> 上故意不打开，要按外层包装（2026-10-07 OPPO 诊断）
  async function openPopup(root, isOpen, ms) {
    const inner = root.tagName === 'INPUT' ? root : root.querySelector('input');
    const wrapper = root.querySelector('[class*="wrapper"], [class*="selector"], [class*="trigger"], [class*="selection"]');
    const icon = root.querySelector('[class*="arrow"], [class*="suffix"], [class*="icon"]');
    const tries = [wrapper || root, inner, icon, root].filter((x, i, arr) => x && arr.indexOf(x) === i);
    for (const t of tries) {
      if (t === inner && inner.focus) inner.focus();
      await tap(t);
      const ok = await waitFor(isOpen, ms || 700);
      if (ok && (!Array.isArray(ok) || ok.length)) return t;
    }
    return null;
  }

  // 控件本身是个只读输入框时（飞书：role=combobox 的 input），选中的值显示在它旁边的元素里：往外找到整个下拉控件
  function displayRoot(root) {
    if (root.tagName !== 'INPUT') return root;
    let best = root;
    for (let a = root.parentElement, i = 0; a && i < 6; a = a.parentElement, i++) {
      if (a.querySelectorAll('input').length > 1) break;  // 出了这个控件，到了别的字段
      if (/select/i.test(String(a.className))) best = a;  // 取最外层的 select 容器：值显示在 selector 的兄弟节点里
    }
    return best;
  }

  function shows(root, text) {
    root = displayRoot(root);
    const shown = norm(root.innerText + ' ' + [...root.querySelectorAll('input')].map((i) => i.value).join(' '));
    const t = norm(text);
    return !!t && (shown.includes(t) || (shown.length >= 2 && t.includes(shown)));
  }

  // 点选项并核对：真实点击没生效（比如点的瞬间弹层关了）就补一次模拟点击；还不行就如实说没看到
  // 从弹层里的某个元素往外逐层找「确定」，出了弹层（到了表单里）就停：不会碰到表单的提交按钮
  function findConfirm(from, root) {
    for (let a = from && from.parentElement, i = 0; a && i < 12; a = a.parentElement, i++) {
      if (a.contains(root)) break;
      const btn = [...a.querySelectorAll('button, [class*="button"], a')].find((b) =>
        visible(b) && /^(确定|确认|ok|完成)$/i.test(clean(b.innerText)) && !b.querySelector('[class*="button"]'));
      if (btn) return btn;
    }
    return null;
  }

  async function choose(root, opt, text) {
    // 点选项里的文字本身，而不是整行的中心：北森的选项行很宽、文字很短，中心常落在不响应的空白处
    const textEl = [...opt.querySelectorAll('*')].reverse().find((e) => !e.children.length && clean(e.innerText) === text);
    await tap(textEl || opt);
    await sleep(150);
    log('choose', text, 'textEl', !!textEl, 'shows', shows(root, text));
    if (shows(root, text)) return true;
    // 带勾选框的多选：勾上就算选中，关掉弹层后控件上才显示；千万别再补点（会把勾取消掉）
    const holder = opt.closest('li, [class*="listItem"], [class*="option"]') || opt;
    const box = holder.querySelector('input[type="checkbox"]');
    if (box) {
      // 北森的多选：勾完要点弹层底部的「确定」才生效（勾选框自身的状态不会变，不能拿来判断）。
      // 只在这个弹层里找「确定」，不会碰到表单的提交按钮
      // 从选项往外逐层找，直到某层里有「确定」（不能按 class 找弹层：选项行自己就叫 phoenix-selectList__listItem）
      const confirm = findConfirm(holder, root);
      log('choose box', !!box, 'confirm', !!confirm);
      if (confirm) {
        await tap(confirm);
        await sleep(400);
        return shows(root, text);
      }
      dismiss(root.querySelector('input') || root);
      await sleep(300);
      return box.checked || shows(root, text);
    }
    if (opt.isConnected) {
      press(opt);
      await sleep(150);
    }
    return shows(root, text);
  }

  async function fillCustomSelect(root, candidates, meta) {
    meta = meta || {};
    // 网站锁住的下拉（OPPO：选了学校后「国家」自动带出且禁用）：等一下看它会不会解锁 / 被带出值
    const locked = () => !!root.querySelector('input:disabled') || /(^|[\s_-])(is-)?disabled/.test(String(root.className));
    if (locked() && !await waitFor(() => !locked() || null, 1500)) {
      const v = clean(root.innerText) || ((root.querySelector('input') || {}).value || '');
      const hit = candidates.find((c) => shows(root, c));
      if (hit) return { ok: true, chosen: v || hit, note: '网站自动带出' };
      return { ok: false, reason: v ? `网站锁定了这个框，当前是「${v}」` : '网站锁定了这个框（通常要先选前面的选项）' };
    }
    const trigger = root.querySelector('[class*="selector"], [class*="trigger"], input') || root;
    const ownInput = [...root.querySelectorAll('input')].find((i) => !i.readOnly && !i.disabled);
    const beforeInputs = new Set(visibleInputs());
    // 弹层里自带的搜索框（北森 phoenix：长列表是虚拟滚动，只渲染看得见的几项，要先搜）
    const popupSearch = () => visibleInputs().find((i) => !beforeInputs.has(i) && !root.contains(i)
      && /搜索|search|输入|关键/i.test(i.getAttribute('placeholder') || ''));
    const before = new Set(visibleOptions());
    const scoped = () => {
      const opts = visibleOptions();
      // 优先用控件内部的选项，其次是新弹出的选项，避免误点页面上别的列表
      const inside = opts.filter((o) => root.contains(o));
      const fresh = opts.filter((o) => !before.has(o));
      return inside.length ? inside : fresh;
    };
    // 只认本控件内部或这次新弹出的选项；上一个下拉没关干净时，它的旧选项不能拿来匹配
    const freshOnly = () => {
      const opts = visibleOptions();
      const inside = opts.filter((o) => root.contains(o));
      return inside.length ? inside : opts.filter((o) => !before.has(o));
    };
    // 没有新弹出的选项就是没打开：不能退回去用页面上已有的「像选项」的元素（OPPO 实测会拿到顶部导航菜单）
    // 远程搜索的下拉（OPPO 学校名称）在输入之前本来就没有选项：输入框获得焦点、变成可输入，就算已经打开，
    // 别再接着点（再点就把它关了）
    const editableFocused = () => {
      const a = document.activeElement;
      return a && a.tagName === 'INPUT' && root.contains(a) && !a.readOnly && !a.disabled;
    };
    await openPopup(root, () => freshOnly().length || popupSearch() || editableFocused(), ownInput ? 600 : 900);
    let opts = await waitFor(freshOnly, 400);
    const firstSeen = opts.map((o) => clean(o.innerText));  // 打开时看到的选项，失败时如实报告
    const search = popupSearch() || ownInput
      || [...root.querySelectorAll('input')].find((i) => !i.readOnly && !i.disabled);
    const cls = typeof root.className === 'string' ? root.className : '';

    // 逐级点选（籍贯 / 现居地：省 → 市）。meta.paths 是几条备选路径（「新加坡 / 苏州」），第一层就选不到才换下一条
    const paths = meta.paths || (meta.path ? [meta.path] : null);
    if (paths && paths.length) {
      let lastChosen = [];
      for (const path of paths) {
        let segs = [...path];
        const chosen = [];
        let prevTexts = null;
        let leaf = false;
        let lastTapped = null;
        for (let level = 0; level < 5 && segs.length; level++) {
          const o = await waitFor(freshOnly, 900);
          const texts = o.map((e) => clean(e.innerText));
          // 点完上一级，列表没变：这个网站到这一级就到底了（讯飞地区只到市，弹层也不自动关）
          if (prevTexts && texts.join('|') === prevTexts.join('|')) { leaf = true; break; }
          prevTexts = texts;
          let hit = -1;
          let used = -1;
          for (let j = 0; j < segs.length && hit < 0; j++) {
            hit = pickOption(texts, [segs[j], segs[j].replace(REGION_SUFFIX, '')]);
            used = j;
          }
          log('path', path.join('>'), 'level', level, 'n', texts.length, 'hit', hit, hit >= 0 ? texts[hit] : '');
          if (hit < 0) break;
          chosen.push(texts[hit]);
          lastTapped = o[hit];
          segs = segs.slice(used + 1);
          // 最后一级：有单独的选择图标就点图标（选中它本身）。点名字的话，有下级的项会展开下级而不是选中
          // （讯飞现居住地：「苏州市」下面还有「工业园区」）
          const item = o[hit].closest('[class*="item-container"], [class*="item"], li') || o[hit].parentElement;
          const pickIcon = !segs.length && item && item.querySelector('[class*="icon"], [class*="radio"], [class*="check"]');
          if (pickIcon && !pickIcon.contains(o[hit]) && !o[hit].contains(pickIcon)) {
            await tap(pickIcon);
            await sleep(300);
            const confirm = findConfirm(item, root);
            if (confirm) { await tap(confirm); await sleep(500); }
            break;
          }
          await tap(o[hit]);
          // 等列表真正刷新成下一级（讯飞：点完「南京市」要一会儿才出区县）；弹层关了也算刷新
          const joined = texts.join('|');
          await waitFor(() => (freshOnly().map((e) => clean(e.innerText)).join('|') !== joined) || null, 1500);
        }
        // 网站的地区只到市一级（弹层关了，或列表不再变化），值也显示了：剩下的「浦口区」是网站没有的，不算失败
        // 到底了弹层还开着：讯飞的地区要点这一项的圆形图标、再点弹层底部「确定」才算选上（点名字只是展开）
        if ((leaf || !segs.length) && lastTapped && lastTapped.isConnected && freshOnly().length) {
          const item = lastTapped.closest('[class*="item-container"], [class*="item"], li') || lastTapped.parentElement;
          const icon = item && item.querySelector('[class*="icon"], [class*="radio"], [class*="check"]');
          if (icon && !icon.contains(lastTapped)) { await tap(icon); await sleep(300); }
          const confirm = findConfirm(item, root);
          log('leaf', leaf, 'item', item && item.className, 'icon', icon && icon.className, 'confirm', !!confirm);
          if (confirm) { await tap(confirm); await sleep(500); }
        }
        // 核对等久一点：标签页在后台时（同时填几个页面）浏览器把定时器限到约 1 秒一次，1 秒的窗口只够查一次
        if (!segs.length && await waitFor(() => (!freshOnly().length && shows(root, chosen[chosen.length - 1])) || null, 3000)) {
          return { ok: true, chosen: chosen.join(' / ') };
        }
        if (leaf && freshOnly().length) { dismiss(trigger); await sleep(300); }
        if (chosen.length && !freshOnly().length
            && await waitFor(() => shows(root, chosen[chosen.length - 1]) || null, 3000)) {  // 显示有延迟
          return { ok: true, chosen: chosen.join(' / '), note: `网站只到「${chosen[chosen.length - 1]}」这一级` };
        }
        lastChosen = chosen;
        if (chosen.length) break;  // 已经点进下一级了，不能再换备选（列表已经变了）
      }
      if (freshOnly().length) dismiss(trigger);
      return { ok: false, reason: lastChosen.length ? `只选到「${lastChosen.join(' / ')}」` : 'no matching option',
        options: freshOnly().map((o) => clean(o.innerText)).slice(0, 30) };
    }

    // 多选（意向城市等）：每个候选值都选上
    if (/select-multiple/.test(cls)) {
      const chosen = [];
      for (const cand of candidates) {
        opts = scoped();
        let k = pickOption(opts.map((o) => clean(o.innerText)), [cand]);
        if (k < 0 && search) {
          await typeText(search, cand);
          opts = await waitFor(() => scoped().filter((o) => pickOption([clean(o.innerText)], [cand]) >= 0), 1500);
          k = pickOption(opts.map((o) => clean(o.innerText)), [cand]);
        }
        if (k < 0) continue;
        const t = clean(opts[k].innerText);
        if (!chosen.includes(t)) { await tap(opts[k]); await sleep(120); chosen.push(t); }
        // 不手动清空搜索框：清空本身会触发一次（可能是远程的）延迟搜索，回来时会弹出新的下拉层
        await sleep(150);
      }
      // 多选的下拉选完不会自己关，必须关干净，否则下一个下拉会拿到这里的选项
      const mine = scoped();
      dismiss(trigger);
      if (search) { search.dispatchEvent(new Event('blur', { bubbles: true })); search.dispatchEvent(new FocusEvent('focusout', { bubbles: true })); search.blur(); }
      await waitFor(() => mine.every((o) => !visible(o)) || null, 1000);
      const unseen = chosen.filter((t) => !shows(root, t));
      if (chosen.length && unseen.length) return { ok: true, chosen: chosen.join('、'), unverified: true };
      return chosen.length ? { ok: true, chosen: chosen.join('、') }
        : { ok: false, reason: 'no matching option', options: opts.map((o) => clean(o.innerText)).slice(0, 30) };
    }

    let i = pickOption(opts.map((o) => clean(o.innerText)), candidates);
    // 可搜索下拉（学校、专业这类远程搜索）：逐个候选值输入关键词再选
    for (let k = 0; i < 0 && search && k < candidates.length; k++) {
      await typeText(search, candidates[k]);
      await sleep(150);
      opts = await waitFor(() => scoped().filter((o) => pickOption([clean(o.innerText)], [candidates[k]]) >= 0), 2000);
      i = pickOption(opts.map((o) => clean(o.innerText)), [candidates[k]]);
    }
    // AutoComplete：输入框本身就是值，没有匹配的建议项时保留输入的文字
    if (i < 0 && search && /auto-?complete/i.test(cls)) {
      setNativeValue(search, candidates[0]);
      await sleep(100);
      search.dispatchEvent(new Event('blur', { bubbles: true }));
      search.blur();
      dismiss(trigger);
      await sleep(100);
      const shown = clean(root.innerText) || search.value;
      if (shown.includes(candidates[0]) || search.value === candidates[0]) return { ok: true, chosen: candidates[0], via: 'free-text' };
    }
    if (i < 0) {
      if (search) setNativeValue(search, '');
      dismiss(trigger);
      const seen = opts.length ? opts.map((o) => clean(o.innerText)) : firstSeen;
      const opened = seen.length || editableFocused();
      return { ok: false, reason: seen.length ? 'no matching option' : opened ? '打开了，但没有任何选项' : 'dropdown did not open',
        options: seen.slice(0, 30) };
    }
    const chosen = clean(opts[i].innerText);
    const seen = await choose(root, opts[i], chosen);
    return seen ? { ok: true, chosen } : { ok: true, chosen, unverified: true };
  }

  // ---------- 级联选择 ----------

  function cascaderColumns() {
    return [...document.querySelectorAll('[class*="cascader-menu"], [class*="cascader__menu"]')].filter((e) =>
      visible(e) && [...e.classList].some((c) => /cascader-menu$|cascader__menu$/.test(c)));
  }

  function columnItems(col) {
    const sel = '[class*="menu-item"], [class*="cascader-node"], [role="menuitem"], [role="option"], li';
    return [...col.querySelectorAll(sel)].filter((e) => visible(e) && clean(e.innerText) && !e.querySelector(sel));
  }

  const REGION_SUFFIX = /(省|市|自治区|特别行政区|自治州|地区|盟|县|区)$/;

  async function fillCascader(root, segments) {
    const trigger = root.querySelector('[class*="selector"], [class*="trigger"], input') || root;
    await tap(trigger);
    trigger.focus && trigger.focus();
    let cols = await waitFor(cascaderColumns, 1500);
    if (!cols.length) { dismiss(trigger); return { ok: false, reason: 'cascader did not open' }; }
    let segs = [...segments];
    const chosen = [];
    for (let level = 0; level < 8; level++) {
      cols = cascaderColumns();
      if (cols.length <= level) break; // 选到叶子后弹层关闭
      const items = columnItems(cols[level]);
      const texts = items.map((e) => clean(e.innerText));
      let hit = -1;
      let used = -1;
      for (let j = 0; j < segs.length && hit < 0; j++) {
        hit = pickOption(texts, [segs[j], segs[j].replace(REGION_SUFFIX, '')]);
        used = j;
      }
      // 直辖市：选了「上海」之后下一级往往还是「上海市」
      if (hit < 0 && !segs.length && chosen.length) hit = pickOption(texts, [chosen[chosen.length - 1].replace(REGION_SUFFIX, '')]);
      else if (hit >= 0) segs = segs.slice(used + 1);
      if (hit < 0) break;
      chosen.push(texts[hit]);
      await tap(items[hit]);
      await sleep(200);
    }
    const closed = !cascaderColumns().length;
    if (!closed) dismiss(trigger);
    await sleep(80);
    const shown = clean(root.innerText);
    const ok = closed && chosen.length > 0 && shown.includes(chosen[chosen.length - 1].replace(REGION_SUFFIX, ''));
    return ok ? { ok, chosen: chosen.join(' / ') }
      : { ok: false, reason: chosen.length ? `只选到「${chosen.join(' / ')}」，没选到最后一级` : 'no matching option',
          options: columnItems(cols[0] || document.body).map((e) => clean(e.innerText)).slice(0, 30) };
  }

  // ---------- 日期选择器 ----------

  const CN_MONTH = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二'];
  const EN_MONTH = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

  function monthOf(t) {
    let m = t.match(/^(\d{1,2})\s*月$/);
    if (m) return +m[1];
    m = t.match(/^(十[一二]?|[一二三四五六七八九])月$/);
    if (m) return CN_MONTH.indexOf(m[1]) + 1;
    const i = EN_MONTH.indexOf(t.slice(0, 3).toLowerCase());
    return i >= 0 && /^[a-z]{3,9}\.?$/i.test(t) ? i + 1 : 0;
  }

  function dateParts(text) {
    const n = (text || '').match(/\d+/g) || [];
    return n.length ? { y: +n[0], m: +(n[1] || 0), d: +(n[2] || 0) } : null;
  }

  function sameDate(text, iso) {
    const a = dateParts(text);
    const b = dateParts(iso);
    return !!a && a.y === b.y && a.m === b.m && (!b.d || !a.d || a.d === b.d);
  }

  function findPanel(before, trigger) {
    // Element Plus 等会用 aria-controls 指明弹层 id，最准
    const holder = trigger.closest && trigger.closest('[aria-controls]');
    const ctl = holder && holder.getAttribute('aria-controls');
    const byId = ctl && document.getElementById(ctl);
    if (byId && visible(byId)) return byId;
    if (byId) return null;  // 知道自己的面板是哪个、它却没开：别拿别的字段还开着的面板凑数
    // 排除页面上其他字段自己的 picker 容器（它们也带 date-picker 之类的 class）
    const all = [...document.querySelectorAll(PANEL_SEL)].filter((p) => visible(p) && !p.contains(trigger)
      && !p.closest('[data-af-panel], [' + ATTR + ']') && !p.querySelector('[' + ATTR + ']'));
    const outer = all.filter((p) => !all.some((q) => q !== p && q.contains(p)));
    const fresh = outer.find((p) => !before.has(p));
    if (fresh) return fresh;
    // 已经开着的旧面板只有范围日期框能沿用（选完开始日期，同一个面板接着选结束日期）。
    // 普通日期框沿用会把日期点进别的字段里（2026-10-08 插件实测：开始时间被写成了结束时间）
    const holderEl = trigger.closest && trigger.closest('[' + ATTR + ']');
    const isRange = holderEl && holderEl.querySelectorAll('input').length > 1;
    return isRange ? outer[outer.length - 1] || null : null;
  }

  const YEAR_BTN_RE = /super|d-arrow|double|year/i;

  function buttonIn(panel, re, aria, texts, excludeYear) {
    return [...panel.querySelectorAll('button, a, span, i, div')].find((b) => {
      if (!visible(b)) return false;
      const cls = typeof b.className === 'string' ? b.className : '';
      if (excludeYear && (YEAR_BTN_RE.test(cls) || /年/.test(b.getAttribute('aria-label') || ''))) return false;
      return re.test(cls) || aria.test(b.getAttribute('aria-label') || b.getAttribute('title') || '') || texts.includes(clean(b.innerText));
    }) || null;
  }

  function readPanel(panel) {
    const headers = [...panel.querySelectorAll('[class*="header"]')]
      .filter((h) => visible(h) && !h.querySelector('td, [class*="cell"]'));
    const header = headers.find((h) => /\d{4}/.test(h.innerText)) || headers[0] || panel;
    const ht = clean(header.innerText);
    const hy = ht.match(/(\d{4})/);
    let hm = ht.match(/(\d{1,2})\s*月/);
    if (!hm) { const k = EN_MONTH.findIndex((x) => ht.toLowerCase().includes(x)); hm = k >= 0 ? [0, k + 1] : null; }
    // 范围选择器有左右两块面板，只看第一块
    let sub = header.parentElement;
    while (sub && sub !== panel && !sub.querySelector('td, [class*="cell"]')) sub = sub.parentElement;
    sub = sub || panel;
    const leaves = [...sub.querySelectorAll('td, [class*="cell"], [role="gridcell"], a, span, div')].filter((e) =>
      visible(e) && !header.contains(e) && ![...e.children].some((c) => clean(c.innerText) === clean(e.innerText)));
    const cellCls = (e) => (e.closest('td, [class*="cell"]') || e).className || '';
    const inView = leaves.some((e) => /in-view/.test(cellCls(e)));
    const days = leaves.filter((e) => /^\d{1,2}$/.test(clean(e.innerText))
      && (inView ? /in-view/.test(cellCls(e)) : !/prev|next|other|disabled|outside|last-month|adjacent/.test(cellCls(e))));
    return {
      year: hy ? +hy[1] : null,
      month: hm ? +hm[1] : null,
      years: leaves.filter((e) => /^\d{4}$/.test(clean(e.innerText))),
      months: leaves.filter((e) => monthOf(clean(e.innerText))),
      days,
      prevYear: buttonIn(panel, /super-prev|prev-year|d-arrow-left|double-left|year-prev|prevyear/i, /上一年|previous year/i, ['«']),
      nextYear: buttonIn(panel, /super-next|next-year|d-arrow-right|double-right|year-next|nextyear/i, /下一年|next year/i, ['»']),
      prevMonth: buttonIn(panel, /(^|[\s_-])prev(-btn|-month|$|\s)|arrow-left|month-prev/i, /上个月|上一月|previous month/i, ['‹', '<'], true),
      nextMonth: buttonIn(panel, /(^|[\s_-])next(-btn|-month|$|\s)|arrow-right|month-next/i, /下个月|下一月|next month/i, ['›', '>'], true),
      yearLabel: [...header.querySelectorAll('*')].find((e) => /^\d{4}\s*年?$/.test(clean(e.innerText)) && !e.children.length) || null,
    };
  }

  // 在弹出的日历面板里翻页并点选目标日期；iso 为 YYYY-MM 或 YYYY-MM-DD
  async function pickInPanel(trigger, iso) {
    const t = dateParts(iso);
    const before = new Set([...document.querySelectorAll(PANEL_SEL)].filter(visible));
    const holder = (trigger.closest && trigger.closest('[' + ATTR + ']')) || trigger;
    // 范围日期框有两个输入框：真实点击要点在对应的那个上，不能点整个控件的中间（会落到另一个框上）
    const root = holder.querySelectorAll('input').length > 1 ? trigger : holder;
    let panel = findPanel(before, trigger);
    if (!panel) {
      await openPopup(root, () => findPanel(before, trigger), 900);
      panel = findPanel(before, trigger);
    }
    if (!panel) return 'picker did not open';
    // 面板顶部有可输入的日期框（北森 phoenix）：按它现有值的格式直接输入并回车，比翻页快也稳
    const pin = [...panel.querySelectorAll('input')].find((i) => visible(i) && !i.readOnly && !i.disabled);
    if (pin) {
      const sample = pin.value || pin.getAttribute('placeholder') || '';
      const pad2 = (n) => String(n).padStart(2, '0');
      const monthOnly = /^\d{4}-\d{1,2}$/.test(sample) || (!t.d && !/\d{4}-\d{1,2}-\d{1,2}/.test(sample));
      const txt = monthOnly ? `${t.y}-${pad2(t.m)}` : `${t.y}-${pad2(t.m)}-${pad2(t.d || 1)}`;
      pin.focus();
      setNativeValue(pin, txt);
      for (const type of ['keydown', 'keypress', 'keyup']) {
        pin.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
      }
      await sleep(300);
      if (!findPanel(before, trigger)) return null;  // 回车后面板关了，多半已经选上，由调用方核对
    }
    for (let step = 0; step < 120; step++) {
      panel = findPanel(before, trigger);
      if (!panel) { log('panel gone at step', step); return null; } // 选完自动关闭
      const s = readPanel(panel);
      log('step', step, 'header', s.year, s.month, 'days', s.days.length, 'months', s.months.length, 'years', s.years.length, panel.className);
      const pressAndWait = async (b) => { if (!b) return false; await tap(b); await sleep(60); return true; };
      if (s.days.length >= 28 && !(s.months.length >= 12)) {
        if (s.year == null || s.month == null) return 'cannot read calendar header';
        const diff = (t.y * 12 + t.m) - (s.year * 12 + s.month);
        if (diff === 0) {
          const day = s.days.find((e) => +clean(e.innerText) === (t.d || 1));
          if (!day) return 'day cell not found';
          await tap(day);
          break;
        }
        if (Math.abs(diff) >= 12 && await pressAndWait(diff > 0 ? s.nextYear : s.prevYear)) continue;
        if (Math.abs(diff) >= 24 && s.yearLabel && await pressAndWait(s.yearLabel)) continue;
        if (!await pressAndWait(diff > 0 ? s.nextMonth : s.prevMonth)) return 'month navigation button not found';
        continue;
      }
      if (s.months.length >= 12) {
        if (s.year != null && s.year !== t.y) {
          if (!await pressAndWait(t.y > s.year ? s.nextYear : s.prevYear)) return 'year navigation button not found';
          continue;
        }
        const cell = s.months.find((e) => monthOf(clean(e.innerText)) === t.m);
        if (!cell) return 'month cell not found';
        await tap(cell);
        await sleep(120);
        // 月份面板之后可能还要选日
        const again = findPanel(before, trigger);
        if (!again || !t.d) break;
        const s2 = readPanel(again);
        if (!(s2.days.length >= 28)) break;
        continue;
      }
      if (s.years.length >= 9) {
        const cell = s.years.find((e) => +clean(e.innerText) === t.y);
        if (cell) { await tap(cell); await sleep(80); continue; }
        const first = +clean(s.years[0].innerText);
        if (!await pressAndWait(t.y > first ? s.nextYear : s.prevYear)) return 'year navigation button not found';
        continue;
      }
      return 'unrecognized calendar panel';
    }
    await sleep(120);
    const still = findPanel(before, trigger);
    if (still) {
      const ok = [...still.querySelectorAll('button, a, span')].find((b) => visible(b) && /^(确定|确认|ok)$/i.test(clean(b.innerText)));
      if (ok) { await tap(ok); await sleep(100); }
    }
    return null;
  }

  async function typeDate(inp, text) {
    if (!inp || inp.readOnly || inp.disabled) return false;
    press(inp);
    inp.focus();
    await sleep(80);
    setNativeValue(inp, text);
    inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
    await sleep(120);
    return true;
  }

  function presentToggle(root) {
    let a = root.parentElement;
    for (let i = 0; a && i < 6; i++, a = a.parentElement) {
      const box = [...a.querySelectorAll('input[type="checkbox"]')].find((c) =>
        /至今|在读|在职|目前|present|current/i.test(clean((c.closest('label') || c.parentElement).innerText)));
      if (box) return box;
    }
    return null;
  }

  async function fillDate(root, text, iso, selectLike) {
    const inp = root.tagName === 'INPUT' ? root : root.querySelector('input');
    const isDisabled = () => (inp && inp.disabled) || /disabled/.test(typeof root.className === 'string' ? root.className : '');
    let note = '';
    if (isDisabled()) {
      // 资料里有结束时间，但「至今」被勾上（常见于站点解析简历）导致结束框禁用：取消这个勾选。只认至今/在读/在职
      const box = presentToggle(root);
      if (box && box.checked) {
        await tap(box.closest('label') || box);
        await sleep(250);
        note = '已取消勾选「至今」';
      }
      if (isDisabled()) return { ok: false, reason: '日期框被禁用（常见原因：旁边勾选了「至今」）' };
    }
    const read = () => (inp && inp.value) || clean(root.innerText);
    if (!selectLike && await typeDate(inp, text)) {
      inp.blur();
      inp.dispatchEvent(new Event('blur', { bubbles: true }));
      dismiss(inp);
      await sleep(120);
      if (sameDate(read(), iso)) return { ok: true, chosen: read(), via: 'typing', note };
    }
    let err = null;
    // 面板可能被其他字段迟到的异步弹层挤掉，没填上就再试一次
    for (let attempt = 0; attempt < 2 && !sameDate(read(), iso); attempt++) {
      err = await pickInPanel(inp || root, iso);
      await sleep(100);
      if (findPanel(new Set(), inp || root)) dismiss(inp || root);
      await sleep(attempt ? 0 : 300);
    }
    if (sameDate(read(), iso)) return { ok: true, chosen: read(), via: 'panel', note };
    return { ok: false, reason: err || `日期面板选完后值为「${read()}」` };
  }

  async function fillDateRange(root, texts, isos) {
    const inputs = [...root.querySelectorAll('input')].slice(0, 2);
    const done = () => inputs.every((inp, k) => sameDate(inp.value, isos[k]));
    for (let k = 0; k < 2; k++) await typeDate(inputs[k], texts[k]);
    if (done()) { dismiss(inputs[1]); return { ok: true, chosen: inputs.map((i) => i.value).join(' ~ '), via: 'typing' }; }
    for (let k = 0; k < 2; k++) {
      const err = await pickInPanel(inputs[k], isos[k]);
      if (err) { dismiss(inputs[k]); return { ok: false, reason: `第 ${k + 1} 个日期：${err}` }; }
      await sleep(120);
    }
    if (findPanel(new Set(), root)) dismiss(inputs[1]);
    return done() ? { ok: true, chosen: inputs.map((i) => i.value).join(' ~ '), via: 'panel' }
      : { ok: false, reason: `范围选择后值为「${inputs.map((i) => i.value).join(' ~ ')}」` };
  }

  // ---------- 文本框：换行、字数上限、长短版本 ----------

  // 单行输入框会吞掉换行，写进去的值就和候选值不一样（得物 2026-10-09：470 字的回答被判成「填写失败」）
  function forSingleLine(text) {
    return text.replace(/\s*\n+\s*/g, (m, off, s) => (/[。！？；，、.!?;,:：]$/.test(s.slice(0, off)) ? '' : ' '));
  }

  // 页面上的字数上限：maxlength，或「超出 267 个字符」「还能输入 20 字」「123/200」这类计数
  function charLimit(el, written) {
    if (el.maxLength > 0 && el.maxLength < 100000) return el.maxLength;
    let box = el;
    for (let i = 0; i < 4 && box.parentElement; i++) {
      box = box.parentElement;
      if (box.querySelectorAll('input, textarea').length > 1) break;
    }
    const t = clean(box.innerText || '');
    let m = t.match(/超出\s*(\d+)\s*个?(字|字符)/);
    if (m) return Math.max(0, written - Number(m[1]));
    m = t.match(/(\d+)\s*\/\s*(\d+)/);
    if (m && Number(m[2]) >= 10 && Number(m[1]) >= 0) return Number(m[2]);
    m = t.match(/(?:最多|不超过|限|至多)\s*(\d+)\s*个?(字|字符)/);
    return m ? Number(m[1]) : 0;
  }

  // 超出上限时截到最后一个句末标点，保证是完整的句子
  function cutToLimit(text, limit) {
    const head = text.slice(0, limit);
    const k = Math.max(head.lastIndexOf('。'), head.lastIndexOf('；'), head.lastIndexOf('！'), head.lastIndexOf('？'));
    return k > limit * 0.5 ? head.slice(0, k + 1) : head;
  }

  async function fillTextValue(el, candidates, kind) {
    const single = el.tagName === 'INPUT';
    const list = candidates.map((c) => (single ? forSingleLine(c) : c));
    const write = (v) => {
      el.focus();
      setNativeValue(el, v);
      el.dispatchEvent(new Event('blur', { bubbles: true }));
      el.blur();
    };
    let value = list[0];
    write(value);
    // 日期 / 短文本不会有字数问题；长文本才等站点的计数更新后检查
    if (value.length < 30 || kind === 'date' || kind === 'month') return { ok: el.value === value, chosen: el.value };
    await sleep(250);
    const limit = charLimit(el, value.length);
    if (limit > 0 && value.length > limit) {
      const fit = list.filter((c) => c.length <= limit).sort((a, b) => b.length - a.length)[0];
      if (fit) {
        value = fit;
        write(value);
        return { ok: el.value === value, chosen: el.value, note: `网站限 ${limit} 字，用了较短的版本（${value.length} 字）` };
      }
      value = cutToLimit(list[list.length - 1], limit);
      write(value);
      return { ok: el.value === value, chosen: el.value, truncated: true,
        note: `网站限 ${limit} 字，资料里的答案放不下，已截取到 ${value.length} 字，请检查或手动改写` };
    }
    return { ok: el.value === value, chosen: el.value };
  }

  async function fill(id, kind, candidates, meta) {
    meta = meta || {};
    const el = document.querySelector('[' + ATTR + '="' + id + '"]');
    if (!el) return { ok: false, reason: 'field not found' };
    candidates = (candidates || []).filter((c) => c !== null && c !== undefined && String(c) !== '').map(String);
    if (!candidates.length) return { ok: false, reason: 'no value' };
    el.scrollIntoView({ block: 'center' });

    if (kind === 'text' || kind === 'textarea' || kind === 'date' || kind === 'month') {
      if (el.readOnly) return { ok: false, reason: 'readonly (picker widget)' };
      return fillTextValue(el, candidates, kind);
    }
    if (kind === 'select') {
      const opts = [...el.options].filter((o) => o.value !== '');
      const i = pickOption(opts.map((o) => clean(o.text)), candidates);
      if (i < 0) return { ok: false, reason: 'no matching option', options: opts.map((o) => clean(o.text)) };
      setNativeValue(el, opts[i].value);
      return { ok: true, chosen: clean(opts[i].text) };
    }
    if (kind === 'radio' && !el.querySelector('input[type="radio"]')) {
      const items = divRadioItems(el);
      const texts = items.map((e) => clean(e.innerText));
      const k = pickOption(texts, candidates);
      if (k < 0) return { ok: false, reason: 'no matching option', options: texts };
      const item = items[k];
      const dot = item.querySelector('[class*="circle"], [class*="dot"], [class*="inner"]');
      const text = [...item.querySelectorAll('span, label, div')].find((e) => clean(e.innerText) === texts[k] && !e.children.length);
      for (const t of [text, item, dot].filter((x, i, a) => x && a.indexOf(x) === i)) {
        if (divRadioOn(item)) break;
        await tap(t);
        await sleep(150);
      }
      return divRadioOn(item) ? { ok: true, chosen: texts[k] } : { ok: true, chosen: texts[k], unverified: true };
    }
    if (kind === 'radio') {
      const radios = [...el.querySelectorAll('input[type="radio"]')];
      const i = pickOption(radios.map(radioLabel), candidates);
      if (i < 0) return { ok: false, reason: 'no matching option', options: radios.map(radioLabel) };
      // 依次点标签、圆点图标、选项文字，点一下核对一下。绝不直接改 checked：
      // 组件库（北森 / React）不认这种改法，页面不显示、提交也不带，却会被误报成「已填」（2026-10-08 讯飞实测）
      const r = radios[i];
      const label = r.closest('label') || r.parentElement;
      const isOn = () => r.checked || [r.parentElement, label].some((e) => e
        && /(^|[\s_-])(is-)?(checked|active|selected)(\s|$|--)|--checked|-checked/i.test(typeof e.className === 'string' ? e.className : ''));
      const targets = [label, label && label.querySelector('[class*="radio"]:not(input)'), r,
        label && [...label.querySelectorAll('span, div')].find((e) => clean(e.innerText) === radioLabel(r))]
        .filter((x, k, arr) => x && arr.indexOf(x) === k);
      for (const t of targets) {
        if (isOn()) break;
        await tap(t);
        await sleep(150);
      }
      return isOn() ? { ok: true, chosen: radioLabel(r) } : { ok: true, chosen: radioLabel(r), unverified: true };
    }
    // 外观是下拉框、点开是日历（北森 phoenix 的出生日期 / 开始时间）
    if (kind === 'custom_select' && meta.date) return fillDate(el, candidates[0], meta.date, true);
    if (kind === 'custom_select') return fillCustomSelect(el, candidates, meta);
    if (kind === 'cascader') return fillCascader(el, candidates);
    if (kind === 'date_picker') return fillDate(el, candidates[0], meta.date || candidates[0]);
    if (kind === 'date_range') {
      if (!meta.range || candidates.length < 2) return { ok: false, reason: '起止时间不全' };
      return fillDateRange(el, candidates, meta.range);
    }
    return { ok: false, reason: 'unsupported kind ' + kind };
  }

  function clickAdd(addId) {
    const el = document.querySelector('[' + ADD_ATTR + '="' + addId + '"]');
    if (!el) return false;
    if (DANGER_RE.test(clean(el.innerText))) return false;
    press(el);
    return true;
  }

  const COLORS = { filled: '#16a34a', kept: '#16a34a', uncertain: '#d97706', unfilled: '#dc2626', sensitive: '#7c3aed' };

  function mark(id, status, note) {
    const el = document.querySelector('[' + ATTR + '="' + id + '"]');
    if (!el) return;
    el.style.outline = '2px solid ' + (COLORS[status] || '#999');
    el.style.outlineOffset = '2px';
    el.setAttribute('data-af-status', status);
    if (note) el.title = note;
  }

  function panel(items) {
    document.querySelector('[data-af-panel]')?.remove();
    const box = document.createElement('div');
    box.setAttribute('data-af-panel', '');
    box.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;width:300px;max-height:60vh;overflow:auto;'
      + 'background:#fff;color:#111;border:1px solid #ddd;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.15);'
      + 'font:13px/1.5 system-ui,sans-serif;padding:10px 12px';
    const count = (s) => items.filter((x) => x.status === s).length;
    const head = document.createElement('div');
    head.style.cssText = 'font-weight:600;margin-bottom:6px';
    head.textContent = `自动填写：已填 ${count('filled')}｜待确认 ${count('uncertain')}｜未填 ${count('unfilled') + count('sensitive')}`;
    box.appendChild(head);
    const tip = document.createElement('div');
    tip.style.cssText = 'color:#666;margin-bottom:6px';
    tip.textContent = '请逐项检查后自己点提交。点击下面的条目可跳到对应字段。';
    box.appendChild(tip);
    items.filter((x) => x.status !== 'filled' && x.status !== 'kept').forEach((x) => {
      const row = document.createElement('div');
      row.style.cssText = 'cursor:pointer;padding:2px 0;border-top:1px solid #f0f0f0';
      row.style.color = COLORS[x.status] || '#333';
      row.textContent = `[${x.section || '-'}] ${x.label || '(无标签)'}${x.note ? ' — ' + x.note : ''}`;
      row.onclick = () => document.querySelector('[' + ATTR + '="' + x.id + '"]')?.scrollIntoView({ block: 'center' });
      box.appendChild(row);
    });
    const close = document.createElement('div');
    close.textContent = '关闭';
    close.style.cssText = 'margin-top:6px;color:#2563eb;cursor:pointer';
    close.onclick = () => box.remove();
    box.appendChild(close);
    document.body.appendChild(box);
  }

  // 诊断：逐个打开下拉 / 日期控件，记下控件本身和弹出来的结构，然后关掉。不填任何值。
  // 用来适配新站点：工具「打不开」的控件，看它打开后到底长什么样。
  async function diagnose(limit) {
    const { fields, addButtons } = extract();
    const out = { url: location.href, title: document.title, fields, addButtons, probes: [] };
    const targets = fields.filter((f) => ['custom_select', 'cascader', 'date_picker', 'date_range'].includes(f.kind));
    for (const f of targets.slice(0, limit || 15)) {
      const root = document.querySelector('[' + ATTR + '="' + f.id + '"]');
      if (!root) continue;
      const rec = { id: f.id, kind: f.kind, label: f.label, root: root.outerHTML.slice(0, 4000), popups: [] };
      try {
      const changed = new Set();
      const mo = new MutationObserver((muts) => muts.forEach((m) => {
        if (m.type === 'childList') m.addedNodes.forEach((n) => { if (n.nodeType === 1) changed.add(n); });
        else if (m.target.nodeType === 1) changed.add(m.target);
      }));
      mo.observe(document.body, { childList: true, subtree: true, attributes: true,
        attributeFilter: ['class', 'style', 'aria-expanded', 'hidden'] });
      const trigger = root.querySelector('[class*="selector"], [class*="trigger"], input') || root;
      root.scrollIntoView({ block: 'center' });
      press(trigger);
      await sleep(900);
      mo.disconnect();
      const list = [...changed].filter((e) => e.isConnected && visible(e) && !e.closest('[data-af-panel]'));
      const outer = list.filter((e) => !list.some((o) => o !== e && o.contains(e)));
      rec.trigger = trigger === root ? 'root' : trigger.outerHTML.slice(0, 600);
      rec.popups = outer.slice(0, 6).map((e) => e.outerHTML.slice(0, 8000));
      rec.rootAfter = root.outerHTML.slice(0, 3000);
      dismiss(trigger);
      await sleep(350);
      } catch (e) {
        rec.error = String(e && e.stack || e);  // 一个控件出错不影响其他控件
      }
      out.probes.push(rec);
    }
    return out;
  }

  // ---------- 分区编辑（OPPO：分区默认只读，点铅笔才出现输入框，每个分区单独「保存」） ----------
  const EDIT_RE = /(^|[\s_-])edit($|[\s_-])|module-title__edit|icon-edit|bianji/i;

  function findExpanders() {
    const headings = headingCandidates();
    const out = [];
    document.querySelectorAll('[class*="edit"], [class*="Edit"], [title*="编辑"], [aria-label*="编辑"], button, a, span').forEach((el) => {
      if (!visible(el) || el.closest('[data-af-panel], [' + ATTR + ']')) return;
      const cls = typeof el.className === 'string' ? el.className : '';
      const text = clean(el.innerText);
      const isEdit = EDIT_RE.test(cls) || /编辑/.test(el.getAttribute('title') || el.getAttribute('aria-label') || '')
        || text === '编辑';
      if (!isEdit || out.some((o) => o.el.contains(el) || el.contains(o.el))) return;
      out.push({ el, section: sectionOf(el, headings), sectionTrail: sectionTrail(el, headings) });
    });
    return out.map((o) => {
      let id = o.el.getAttribute('data-af-exp');
      if (!id) { id = 'e' + seq++; o.el.setAttribute('data-af-exp', id); }
      return { id, section: o.section, sectionTrail: o.sectionTrail };
    });
  }

  // 点分区的「编辑」，并核对输入框确实变多了（编辑表单出来了）。保存失败后网站常弹提示条挡住铅笔，
  // 点击会被挡掉——等一会儿再点，最多 3 次（2026-10-08 插件实测：「基础信息」没打开）
  async function clickExpander(id) {
    const count = () => [...document.querySelectorAll('input, textarea, select')].filter(visible).length;
    for (let attempt = 0; attempt < 3; attempt++) {
      const el = document.querySelector('[data-af-exp="' + id + '"]');
      if (!el) return false;
      const before = count();
      await tap(el);
      if (await waitFor(() => count() > before || null, 1500)) {
        // 已保存过的分区重新打开时，下拉框先显示选项编码（OPPO 学历「20」），选项加载完才变成文字
        const codes = () => [...document.querySelectorAll('.el-select input, .ant-select-selection-item')]
          .some((i) => visible(i) && /^\d+$/.test((i.value || i.innerText || '').trim()));
        await waitFor(() => !codes() || null, 8000);
        return true;
      }
      await sleep(1000);
    }
    return false;
  }

  // 只认文字恰好是「保存」的按钮；「保存并提交」「投递」「提交」一律不碰
  function saveButtons() {
    return [...document.querySelectorAll('button, [role="button"], a')].filter((b) =>
      visible(b) && /^保\s*存$/.test(clean(b.innerText)) && !b.closest('[data-af-panel]'));
  }

  // 页面上的校验报错（保存失败时把原因带回报告）
  function formErrors() {
    return [...document.querySelectorAll('.el-form-item__error, .ant-form-item-explain-error, [class*="error-msg"], [class*="errorMsg"], [class*="form-item__error"]')]
      .filter(visible).map((e) => {
        const item = e.closest('[class*="form-item"]');
        const lab = item && item.querySelector('[class*="label"]');
        return (lab ? clean(lab.innerText) + '：' : '') + clean(e.innerText);
      }).filter(Boolean).slice(0, 20);
  }

  // 提示类弹框（Element Plus MessageBox 等）。不含普通 el-dialog：有的网站整张表单就放在 dialog 里
  const MSGBOX_SEL = '.el-message-box, .ant-modal-confirm, .van-dialog, [role="alertdialog"]';
  const openMsgBoxes = () => [...document.querySelectorAll(MSGBOX_SEL)].filter((d) => visible(d) && !d.closest('[data-af-panel]'));

  // 处理挡在页面上的提示框，不然后面的真实点击都落在遮罩上（OPPO：点「保存」后弹「保存前请确认以上经历」）。
  // 刚点完「保存」时弹的「保存」确认框 → 点「确认」（这就是保存这一步本身）；其他一律「取消」/ 关掉。
  // 提到提交 / 投递 / 删除 / 离开的框永远不点确认。
  async function settleMsgBoxes(afterSave) {
    const handled = [];
    for (let i = 0; i < 6; i++) {
      const box = openMsgBoxes().pop();
      if (!box) break;
      const txt = clean(box.innerText);
      const btns = [...box.querySelectorAll('button, [role="button"]')].filter(visible);
      const ok = btns.find((b) => /^(确\s*认|确\s*定|ok)$/i.test(clean(b.innerText)));
      const cancel = btns.find((b) => /^(取\s*消|关\s*闭|cancel)$/i.test(clean(b.innerText)))
        || box.querySelector('[class*="headerbtn"], [class*="close"], [aria-label="Close"]');
      const confirmSave = afterSave && ok && /保存/.test(txt) && !/提交|投递|删除|移除|清空|放弃|离开|退出/.test(txt);
      log('msgbox', txt.slice(0, 60), confirmSave ? 'confirm' : 'cancel');
      if (confirmSave) await tap(ok);
      else if (cancel) await tap(cancel);
      else dismiss(box);
      handled.push((confirmSave ? '确认：' : '关闭：') + txt.slice(0, 40));
      await waitFor(() => !box.isConnected || !visible(box) || null, 1500);
      await sleep(200);
    }
    return handled;
  }

  async function saveSections() {
    const btns = saveButtons();
    const dialogs = await settleMsgBoxes(false);
    for (const b of btns) {
      await tap(b);
      await sleep(600);
      dialogs.push(...await settleMsgBoxes(true));
    }
    await sleep(600);
    const left = saveButtons();
    // 这次新打开的分区自己的「保存」按钮没了，就算这个分区存上了。之前没存成的分区（比如缺照片的基础信息）
    // 的按钮会一直留着，不能因为它们把后面每个分区都判成失败
    const stale = window.__afStaleSave || new Set();
    const fresh = btns.filter((x) => !stale.has(x));
    const sectionSaved = fresh.length ? fresh.every((x) => !left.includes(x)) : !left.length;
    window.__afStaleSave = new Set(left);
    return { clicked: btns.length, remaining: left.length, sectionSaved, errors: formErrors(), dialogs };
  }

  // ---------- 给驱动用的任务接口 ----------
  // 驱动调用 startJob 后反复 pollJob：有点击请求就去真实点击，然后 ackClick；done 时取结果。
  function startJob(id, kind, candidates, meta, trusted) {
    window.__afClickQueueEnabled = !!trusted;
    window.__afClickQueue = [];
    const job = { done: false };
    window.__afJob = job;
    // 先清掉挡住页面的提示框（只点取消 / 关闭），否则这一步的点击会落在遮罩上
    const clear = kind === '__save' ? Promise.resolve() : settleMsgBoxes(false);
    const work = clear.then(() => (kind === '__expand' ? clickExpander(id) : kind === '__save' ? saveSections() : fill(id, kind, candidates, meta)));
    work
      .then((r) => { job.result = r; }, (e) => { job.result = { ok: false, reason: String((e && e.message) || e) }; })
      .finally(() => { job.done = true; window.__afClickQueueEnabled = false; });
    return true;
  }

  function pollJob() {
    const q = window.__afClickQueue || [];
    if (q.length) {
      if (q[0].key) return { key: q[0].key };
      return q[0].text !== undefined ? { text: q[0].text } : { click: { x: q[0].x, y: q[0].y } };
    }
    const j = window.__afJob || {};
    return j.done ? { done: true, result: j.result } : { done: false };
  }

  function ackClick() {
    const it = (window.__afClickQueue || []).shift();
    if (it) it.res(true);
    return true;
  }

  window.__autofill = { version: VERSION, extract, fill, clickAdd, mark, panel, pickOption, diagnose,
    startJob, pollJob, ackClick, findExpanders, saveButtons, formErrors };
})();
