// Moka（app.mokahr.com，组件库前缀 sd-）。这个文件里的东西只在 Moka 页面上生效。
// 2026-10-09 众安保险 / SHEIN 诊断：
//   · 选项是 sd-Menu-content-item；日期面板叫 sd-panal-menu-wrapper（官方拼写）
//   · 表单项是 <div class="apply-field 类型_info"><div class="title">性别</div><div class="ctrl">…</div></div>
//   · 毕业时间 = 年下拉 + 月下拉，就读时间 = 年 月 ~ 年 月 共 4 个下拉（class 含 month-range）
//   · 学校名称 / 专业名称是带候选的搜索框：直接写入的文字不从候选里选，失焦时会被清掉
registerAdapter({
  id: 'moka',
  detect: () => /(^|\.)mokahr\.com$/.test(location.hostname)
    || !!document.querySelector('[class*="sd-Select-container"], [class*="sd-Input-container"]'),
  optionSel: '[class*="Menu-content-item"]',
  panelSel: '[class*="panal-menu"]',
  selectSel: '[class*="Select-container"]',
  panel: {
    prevYear: /icondoubleLeft/, nextYear: /icondoubleRight/,
    prevMonth: /prev-tool|iconleft(\s|$)/, nextMonth: /next-tool|iconright(\s|$)/,
    excludeCell: /fade/,  // 非本月的格子
  },

  // 表单项的类型写在 apply-field 的类名里：string_info（自由输入）不是下拉，Select / select_info / day_info 才是
  isSelectRoot(el) {
    if (typeof el.className !== 'string' || !/(^|\s)sd-Select-container/.test(el.className)) return undefined;
    const item = el.closest('[class*="apply-field"]');
    const inp = el.querySelector('input');
    if (item && /string_info/.test(item.className)) return false;
    if (inp && /^请输入/.test(inp.getAttribute('placeholder') || '')) return false;
    return true;
  },

  // 「年 + 月」下拉组合当作一个日期控件
  collectExtra({ out, seen, inSeen }) {
    document.querySelectorAll('[class*="month-range"]').forEach((el) => {
      if (seen.has(el) || inSeen(el) || !visible(el) || el.closest('[class*="dropdown"], [class*="popup"]')) return;
      const n = this.yearMonthSelects(el).length;
      if (n < 2) return;
      seen.add(el);
      out.push({ el, kind: n >= 4 ? 'date_range' : 'date_picker' });
    });
  },

  // 表单项的标题就是标签
  label(root) {
    const item = root.closest('[class*="apply-field"]');
    if (!item || item.querySelectorAll('[' + ATTR + ']').length > 1) return undefined;
    const cap = [...item.children].find((c) => /(^|[\s_-])title([\s_-]|$)/i.test(typeof c.className === 'string' ? c.className : '') && !c.contains(root));
    const t = cap && cleanLabel(cap.innerText);
    return t && !NOT_LABEL.test(t) ? t.slice(0, 80) : undefined;
  },

  // 日期面板的标题条是 sd-basic-selector（里面有「2026年」「十月」），不叫 header；
  // 叫 header 的 sd-basic-date-header 其实是「一二三四五六日」那一行
  panelHeader(panel) {
    const yl = [...panel.querySelectorAll('*')].find((e) => !e.children.length && /^\d{4}\s*年(\s*\d{1,2}\s*月)?$/.test(clean(e.innerText)));
    return yl ? (yl.parentElement.closest('[class*="selector"]') || yl.parentElement) : undefined;
  },

  // ---- 年 / 月两个下拉：按结构认（站点用简历解析预填后，placeholder 会变空） ----
  yearMonthSelects(root) {
    if (!root || root.tagName === 'INPUT') return [];
    return [...root.querySelectorAll('[class*="Select-container"]')].filter((s) => s.querySelector('input'));
  },

  // 页面上已选的年月 → "2025-09" 或 "2025-09 ~ 2026-12"
  readYearMonth(root) {
    const shown = this.yearMonthSelects(root).map((s) => clean((s.querySelector('[class*="display-value"]') || {}).innerText || (s.querySelector('input') || {}).value || ''));
    const out = [];
    for (let k = 0; k + 1 < shown.length; k += 2) {
      const y = shown[k].match(/\d{4}/);
      const m = shown[k + 1].match(/\d{1,2}/);
      if (y && m) out.push(`${y[0]}-${String(m[0]).padStart(2, '0')}`);
    }
    return out.join(' ~ ');
  },

  // 依次打开、选中；每个下拉复用普通下拉的打开 / 搜索 / 核对逻辑
  async fillYearMonth(root, isos) {
    const sels = this.yearMonthSelects(root);
    if (sels.length < isos.length * 2) return { ok: false, reason: `年月下拉框数量不对（${sels.length} 个）` };
    const pad = (n) => String(n).padStart(2, '0');
    for (let k = 0; k < isos.length; k++) {
      const t = dateParts(isos[k]);
      if (!t || !t.y || !t.m) return { ok: false, reason: `日期「${isos[k]}」缺年或月` };
      const who = isos.length > 1 ? (k === 0 ? '开始' : '结束') : '';
      const y = await fillCustomSelect(sels[2 * k], [String(t.y), `${t.y}年`], {});
      if (!y.ok) return { ok: false, reason: `${who}年份：${y.reason}`, options: y.options };
      const m = await fillCustomSelect(sels[2 * k + 1], [`${t.m}月`, String(t.m), pad(t.m), `${pad(t.m)}月`], {});
      if (!m.ok) return { ok: false, reason: `${who}月份：${m.reason}`, options: m.options };
    }
    return { ok: true, chosen: isos.join(' ~ '), via: 'year-month selects' };
  },

  currentValue(c) {
    if ((c.kind === 'date_range' || c.kind === 'date_picker') && this.yearMonthSelects(c.el).length >= 2) return this.readYearMonth(c.el);
    return undefined;
  },
  fillDate(root, text, iso) {
    return this.yearMonthSelects(root).length >= 2 ? this.fillYearMonth(root, [iso]) : undefined;
  },
  fillDateRange(root, texts, isos) {
    return this.yearMonthSelects(root).length >= 4 ? this.fillYearMonth(root, isos) : undefined;
  },

  // ---- 带候选的搜索框（学校、专业）：不从候选里选，失焦时会被清掉（报告里是已填，页面上是空的） ----
  searchSelectRoot(el) {
    return el.tagName === 'INPUT' ? el.closest('[class*="Select-container"]') : null;
  },
  fillText(el, candidates) {
    return this.searchSelectRoot(el) ? this.fillSearchText(el, candidates) : undefined;
  },
  // 用「去掉大学 / 学院后缀」「前 3 个字」「前 2 个字」依次搜，收集候选，挑最像的
  async closestSearchHit(el, root, candidates) {
    const base = candidates[0];
    const queries = [...new Set([base.replace(/(大学|学院|学校)$/, ''), base.slice(0, 3), base.slice(0, 2)])].filter((q) => q.length >= 2);
    const pool = new Map();
    for (const q of queries) {
      el.focus();
      await typeText(el, q);
      await sleep(900);
      visibleOptions().filter((o) => !o.closest('[data-af-panel]')).forEach((o) => pool.set(clean(o.innerText), o));
      if (pool.size) break;
    }
    const texts = [...pool.keys()];
    const g = closestOption(texts, candidates, 0.5);
    if (g.i < 0) { setNativeValue(el, ''); dismiss(el); return null; }
    const pick = texts[g.i];
    const r = await fillCustomSelect(root, [pick], {});
    if (!r.ok) return null;
    return { ok: true, chosen: r.chosen || pick, guessed: true, risk: 'high',
      note: `候选里没有「${base}」，选了最接近的「${pick}」，请确认是不是同一所学校` };
  },

  async fillSearchText(el, candidates) {
    const root = this.searchSelectRoot(el);
    // 1. 当搜索下拉用：输入、等候选、点选，由普通下拉的逻辑核对
    const r = await fillCustomSelect(root, candidates, {});
    if (r.ok) return r;
    const noList = /no matching option|没有任何选项|dropdown did not open/.test(r.reason || '');
    if (!noList) return r;
    // 2. 站点没有给出完全匹配的候选：用更短的关键词搜，从搜到的候选里挑最接近的一所，用红色标出让人确认
    //    （资料里有学校英文名时，第 1 步已经试过英文名了）
    const guess = await this.closestSearchHit(el, root, candidates);
    if (guess) return guess;
    // 3. 一个候选都没有：试着直接输入，失焦后等一会儿，看站点是不是把它清掉了
    const value = candidates[0];
    el.focus();
    setNativeValue(el, value);
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    el.blur();
    dismiss(el);
    await sleep(500);
    const kept = clean(root.innerText) + ' ' + el.value;
    if (el.value === value || kept.includes(value)) {
      return { ok: true, chosen: value, note: '站点没有匹配的候选项，按输入内容填写（失焦后内容仍在）' };
    }
    setNativeValue(el, '');
    return { ok: false, reason: `站点要求从候选里选，但候选里没有「${value}」，直接输入也被清掉了（请手动选择）`, options: r.options };
  },
});
