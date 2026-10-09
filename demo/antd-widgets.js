// demo 共用的 AntD / Element 风格控件模拟。只为测试，行为尽量贴近真实组件：
// 弹层挂在 body 上、mousedown 打开、可输入 picker 必须回车确认、失焦丢弃未确认输入。
(() => {
  window.__submitted = false;
  const pad = (n) => String(n).padStart(2, '0');
  const today = new Date();
  const REGIONS = { '北京市': ['北京市'], '上海市': ['上海市'], '江苏省': ['南京市', '苏州市', '无锡市'], '浙江省': ['杭州市', '宁波市'], '安徽省': ['合肥市', '芜湖市'] };
  const SCHOOLS = ['示例大学', '示例大学附属学院', '示例理工大学', '示例师范大学', '另一所大学'];
  const CN = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二'];

  let dd = null;
  function closeDD() { if (dd) { dd.el.remove(); dd = null; } }
  function openDD(owner, cls) {
    closeDD();
    const r = owner.getBoundingClientRect();
    const el = document.createElement('div');
    el.className = 'dd ' + cls;
    el.style.left = (r.left + scrollX) + 'px';
    el.style.top = (r.bottom + scrollY + 2) + 'px';
    document.body.appendChild(el);
    dd = { el, owner };
    return el;
  }
  function btn(cls, text, fn) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = cls; b.textContent = text;
    b.addEventListener('click', fn);
    return b;
  }
  function setSelectText(root, text) {
    root.querySelector('.ant-select-selection-placeholder')?.remove();
    let s = root.querySelector('.ant-select-selection-item');
    if (!s) { s = document.createElement('span'); s.className = 'ant-select-selection-item'; root.querySelector('.ant-select-selector').appendChild(s); }
    s.textContent = text;
  }

  function openAntPicker(root) {
    const mode = root.dataset.mode;
    const cur = (root.dataset.value || '').split('-').map(Number);
    const st = { y: cur[0] || today.getFullYear(), m: cur[1] || today.getMonth() + 1 };
    const el = openDD(root, 'ant-picker-dropdown');
    const commit = (v) => { root.dataset.value = v; root.querySelector('input').value = v; closeDD(); };
    function render() {
      el.innerHTML = '';
      const panel = document.createElement('div'); panel.className = 'ant-picker-panel';
      const h = document.createElement('div'); h.className = 'ant-picker-header';
      const move = (dm) => () => { const t = st.y * 12 + st.m - 1 + dm; st.y = Math.floor(t / 12); st.m = t % 12 + 1; render(); };
      h.appendChild(btn('ant-picker-header-super-prev-btn', '«', move(-12)));
      if (mode === 'date') h.appendChild(btn('ant-picker-header-prev-btn', '‹', move(-1)));
      const view = document.createElement('div'); view.className = 'ant-picker-header-view';
      view.appendChild(btn('ant-picker-year-btn', st.y + '年', () => {}));
      if (mode === 'date') view.appendChild(btn('ant-picker-month-btn', st.m + '月', () => {}));
      h.appendChild(view);
      if (mode === 'date') h.appendChild(btn('ant-picker-header-next-btn', '›', move(1)));
      h.appendChild(btn('ant-picker-header-super-next-btn', '»', move(12)));
      panel.appendChild(h);
      const table = document.createElement('table'); table.className = 'ant-picker-content';
      const cell = (text, inView, fn) => {
        const td = document.createElement('td');
        td.className = 'ant-picker-cell' + (inView ? ' ant-picker-cell-in-view' : '');
        td.innerHTML = '<div class="ant-picker-cell-inner"></div>';
        td.firstChild.textContent = text;
        td.addEventListener('click', fn);
        return td;
      };
      if (mode === 'month') {
        for (let r = 0; r < 4; r++) {
          const tr = table.insertRow();
          for (let c = 0; c < 3; c++) { const k = r * 3 + c + 1; tr.appendChild(cell(k + '月', true, () => commit(`${st.y}-${pad(k)}`))); }
        }
      } else {
        const first = new Date(st.y, st.m - 1, 1);
        const start = new Date(first); start.setDate(1 - ((first.getDay() + 6) % 7));
        for (let r = 0; r < 6; r++) {
          const tr = table.insertRow();
          for (let c = 0; c < 7; c++) {
            const d = new Date(start); d.setDate(start.getDate() + r * 7 + c);
            const v = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
            tr.appendChild(cell(String(d.getDate()), d.getMonth() === st.m - 1, () => commit(v)));
          }
        }
      }
      panel.appendChild(table);
      el.appendChild(panel);
    }
    render();
  }

  function openRange(root, active) {
    const inputs = root.querySelectorAll('input');
    const st = { y: today.getFullYear() - 1, active };
    const el = openDD(root, 'ant-picker-dropdown ant-picker-dropdown-range');
    dd.setActive = (k) => { st.active = k; };
    function render() {
      el.innerHTML = '';
      const panels = document.createElement('div'); panels.className = 'ant-picker-panels';
      [0, 1].forEach((p) => {
        const panel = document.createElement('div'); panel.className = 'ant-picker-panel';
        const h = document.createElement('div'); h.className = 'ant-picker-header';
        if (p === 0) h.appendChild(btn('ant-picker-header-super-prev-btn', '«', () => { st.y--; render(); }));
        const view = document.createElement('div'); view.className = 'ant-picker-header-view';
        view.appendChild(btn('ant-picker-year-btn', (st.y + p) + '年', () => {}));
        h.appendChild(view);
        if (p === 1) h.appendChild(btn('ant-picker-header-super-next-btn', '»', () => { st.y++; render(); }));
        panel.appendChild(h);
        const table = document.createElement('table'); table.className = 'ant-picker-content';
        for (let r = 0; r < 4; r++) {
          const tr = table.insertRow();
          for (let c = 0; c < 3; c++) {
            const k = r * 3 + c + 1;
            const td = document.createElement('td'); td.className = 'ant-picker-cell ant-picker-cell-in-view';
            td.innerHTML = '<div class="ant-picker-cell-inner"></div>'; td.firstChild.textContent = k + '月';
            td.addEventListener('click', () => {
              inputs[st.active].value = `${st.y + p}-${pad(k)}`;
              if (st.active === 0) { st.active = 1; render(); } else closeDD();
            });
            tr.appendChild(td);
          }
        }
        panel.appendChild(table);
        panels.appendChild(panel);
      });
      el.appendChild(panels);
    }
    render();
  }

  function openElMonth(root) {
    const st = { y: today.getFullYear() };
    const el = openDD(root, 'el-picker-panel el-date-picker');
    function render() {
      el.innerHTML = '';
      const h = document.createElement('div'); h.className = 'el-date-picker__header';
      h.appendChild(btn('el-picker-panel__icon-btn el-icon-d-arrow-left', '', () => { st.y--; render(); }));
      const label = document.createElement('span'); label.className = 'el-date-picker__header-label'; label.textContent = st.y + ' 年';
      h.appendChild(label);
      h.appendChild(btn('el-picker-panel__icon-btn el-icon-d-arrow-right', '', () => { st.y++; render(); }));
      el.appendChild(h);
      const table = document.createElement('table'); table.className = 'el-month-table';
      for (let r = 0; r < 3; r++) {
        const tr = table.insertRow();
        for (let c = 0; c < 4; c++) {
          const k = r * 4 + c + 1;
          const td = document.createElement('td');
          td.innerHTML = '<div><a class="cell"></a></div>'; td.querySelector('a').textContent = CN[k - 1] + '月';
          td.addEventListener('click', () => { root.dataset.value = `${st.y}-${pad(k)}`; root.querySelector('input').value = root.dataset.value; closeDD(); });
          tr.appendChild(td);
        }
      }
      el.appendChild(table);
    }
    render();
  }

  function openCascader(root) {
    const path = [];
    const el = openDD(root, 'ant-select-dropdown ant-cascader-dropdown');
    function render() {
      el.innerHTML = '<div class="ant-cascader-menus"></div>';
      const menus = el.firstChild;
      const cols = [Object.keys(REGIONS)];
      if (path[0]) cols.push(REGIONS[path[0]]);
      cols.forEach((items, level) => {
        const ul = document.createElement('ul'); ul.className = 'ant-cascader-menu';
        items.forEach((name) => {
          const li = document.createElement('li'); li.className = 'ant-cascader-menu-item';
          li.innerHTML = '<div class="ant-cascader-menu-item-content"></div>'; li.firstChild.textContent = name;
          li.addEventListener('click', () => {
            path.length = level; path.push(name);
            if (level === 0) render();
            else { root.dataset.value = path.join('/'); setSelectText(root, path.join(' / ')); closeDD(); }
          });
          ul.appendChild(li);
        });
        menus.appendChild(ul);
      });
    }
    render();
  }

  // 普通单选、远程搜索、多选、AutoComplete
  // 多选下拉单独挂着：和小红书实测一致，Escape、点别处、打开别的下拉都关不掉它，只有输入框失焦才关
  let multiDD = null;
  function closeMulti() { if (multiDD) { multiDD.el.remove(); multiDD = null; } }
  function openMulti(root) {
    if (multiDD && multiDD.owner === root) return multiDD.el;
    closeMulti();
    const r = root.getBoundingClientRect();
    const el = document.createElement('div');
    el.className = 'dd ant-select-dropdown';
    el.style.left = (r.left + scrollX) + 'px';
    el.style.top = (r.bottom + scrollY + 2) + 'px';
    el.addEventListener('mousedown', (e) => e.preventDefault());  // 和真实 AntD 一样：点选项不让输入框失焦
    document.body.appendChild(el);
    multiDD = { el, owner: root };
    return el;
  }

  function openSelect(root, query) {
    const isMulti = root.classList.contains('ant-select-multiple');
    const el = isMulti ? openMulti(root) : (dd && dd.owner === root) ? dd.el : openDD(root, 'ant-select-dropdown');
    el.innerHTML = '';
    const multi = root.classList.contains('ant-select-multiple');
    const auto = root.classList.contains('ant-select-auto-complete');
    let opts = root.dataset.options ? root.dataset.options.split(',') : [];
    if (root.dataset.source === 'schools') opts = query ? SCHOOLS.filter((s) => s.includes(query)) : [];
    else if (query) opts = opts.filter((o) => o.includes(query));
    if (!opts.length) { if (!auto) el.innerHTML = '<div class="ant-select-item-empty">请输入关键词搜索</div>'; return; }
    opts.forEach((o) => {
      const it = document.createElement('div'); it.className = 'ant-select-item ant-select-item-option';
      it.innerHTML = '<div class="ant-select-item-option-content"></div>'; it.firstChild.textContent = o;
      it.addEventListener('click', () => {
        const inp = root.querySelector('input');
        if (multi) {
          const vals = root.dataset.value ? root.dataset.value.split(',') : [];
          if (!vals.includes(o)) vals.push(o);
          root.dataset.value = vals.join(',');
          setSelectText(root, vals.join('、'));
          if (inp) inp.value = '';
          return; // 多选：弹层保持打开
        }
        root.dataset.value = o; setSelectText(root, o);
        if (inp) inp.value = auto ? o : '';
        closeDD();
      });
      el.appendChild(it);
    });
  }
  let searchTimer = null;
  document.addEventListener('input', (e) => {
    const root = e.target.closest('.ant-select-show-search');
    if (!root) return;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => openSelect(root, e.target.value.trim()), 300); // 模拟远程搜索延迟
  });

  document.addEventListener('mousedown', (e) => {
    if (dd && dd.el.contains(e.target)) return;
    if (multiDD && multiDD.el.contains(e.target)) return;
    const range = e.target.closest('.ant-picker-range');
    const picker = e.target.closest('.ant-picker');
    const elp = e.target.closest('.el-date-editor');
    const cas = e.target.closest('.ant-cascader');
    const sel = e.target.closest('.ant-select');
    if (range) {
      const k = Math.max(0, [...range.querySelectorAll('input')].indexOf(e.target.closest('input')));
      if (dd && dd.owner === range) dd.setActive(k); else openRange(range, k);
    } else if (picker) { if (!dd || dd.owner !== picker) openAntPicker(picker); }
    else if (elp) { if (!dd || dd.owner !== elp) openElMonth(elp); }
    else if (cas) { if (!dd || dd.owner !== cas) openCascader(cas); }
    else if (sel && sel.classList.contains('ant-select-multiple')) openSelect(sel, '');
    // 和真实 AntD 一样异步渲染（有展开动画），新选项晚一点才出现
    else if (sel) { if (!dd || dd.owner !== sel) { closeDD(); setTimeout(() => openSelect(sel, ''), 120); } }
    else closeDD();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeDD(); return; }
    const picker = e.target.closest && e.target.closest('.ant-picker:not(.ant-picker-range)');
    if (e.key === 'Enter' && picker && !e.target.readOnly) {
      const ok = picker.dataset.mode === 'month' ? /^\d{4}-\d{2}$/ : /^\d{4}-\d{2}-\d{2}$/;
      if (ok.test(e.target.value)) { picker.dataset.value = e.target.value; closeDD(); }
    }
  });
  document.addEventListener('focusout', (e) => {
    if (multiDD && multiDD.owner.contains(e.target)) closeMulti();
    const picker = e.target.closest && e.target.closest('.ant-picker:not(.ant-picker-range)');
    if (picker && !e.target.readOnly) e.target.value = picker.dataset.value || '';
    // AutoComplete：失焦时输入框里的文字就是值
    const auto = e.target.closest && e.target.closest('.ant-select-auto-complete');
    if (auto && e.target.value) { auto.dataset.value = e.target.value; setSelectText(auto, e.target.value); }
  });

  // 可重复段落：按钮写 data-add-for="列表 id"
  document.addEventListener('click', (e) => {
    const a = e.target.closest('[data-add-for]');
    if (!a) return;
    const list = document.getElementById(a.dataset.addFor);
    const tpl = list.querySelector('template');
    const c = tpl ? tpl.content.firstElementChild.cloneNode(true) : list.firstElementChild.cloneNode(true);
    const n = list.querySelectorAll(':scope > .block').length;
    c.querySelectorAll('input').forEach((i) => { if (i.type !== 'radio') i.value = ''; if (i.id) i.id = i.id.replace(/_\d+_/, `_${n}_`); });
    c.querySelectorAll('label[for]').forEach((l) => { l.htmlFor = l.htmlFor.replace(/_\d+_/, `_${n}_`); });
    c.querySelectorAll('[data-value]').forEach((x) => { delete x.dataset.value; });
    c.querySelectorAll('.ant-select-selection-item').forEach((x) => x.remove());
    list.appendChild(c);
  });

  function val(el) {
    if (el.classList.contains('ant-picker-range')) {
      const [a, b] = el.querySelectorAll('input');
      return a.value || b.value ? `${a.value}~${b.value}` : '';
    }
    if (el.classList.contains('ant-radio-group')) {
      const c = el.querySelector('input:checked');
      return c ? c.closest('label').textContent.trim() : '';
    }
    return el.dataset.value || (['INPUT', 'TEXTAREA'].includes(el.tagName) ? el.value : '');
  }
  window.__formValues = () => {
    const out = {};
    document.querySelectorAll('[data-key]').forEach((el) => { out[el.dataset.key] = val(el); });
    document.querySelectorAll('[data-list]').forEach((list) => {
      [...list.querySelectorAll(':scope > .block')].forEach((b, i) => {
        b.querySelectorAll('[data-f]').forEach((el) => { out[`${list.dataset.list}.${i}.${el.dataset.f}`] = val(el); });
      });
    });
    out.__submitted = window.__submitted;
    return out;
  };
  document.addEventListener('click', (e) => { if (e.target.closest('[data-submit]')) window.__submitted = true; });
  // 「至今」：勾上后同一行的结束日期框禁用
  document.addEventListener('change', (e) => {
    if (!e.target.classList.contains('present-toggle')) return;
    const end = e.target.closest('.ant-row').querySelectorAll('.ant-picker')[1];
    end.classList.toggle('ant-picker-disabled', e.target.checked);
    end.querySelector('input').disabled = e.target.checked;
  });
})();
