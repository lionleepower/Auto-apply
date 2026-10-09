// 按 2026-10-07 真实诊断（runs/diag-iflytek / diag-oppo）复刻的控件，行为尽量贴近：
//   北森 phoenix-select：点开后选项是 li.phoenix-selectList__listItem，虚拟列表只渲染前 6 项，弹层里自带「搜索」框
//   北森 phoenix 日期：外观是下拉框，点开是日历，日历顶部有可输入的日期框，回车生效
//   北森地区选择器：.area-text-label，点省进入市
//   Element Plus 月份选择器：按在没聚焦的 <input> 上不打开（要按外层 wrapper），弹层 id 由 aria-controls 指明
//   Element Plus el-select：点 wrapper 打开，选项 li.el-select-dropdown__item
(() => {
  window.__submitted = false;
  // ?strict=1：Element Plus 部分只响应真实点击（isTrusted），和 OPPO 实测一致
  const STRICT = new URLSearchParams(location.search).has('strict');
  const pad = (n) => String(n).padStart(2, '0');
  const CN = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二'];
  const AREAS = { '北京市': [], '上海市': [], '江苏省': ['南京市', '苏州市', '无锡市'], '浙江省': ['杭州市', '宁波市'] };
  let layer = null;
  function close() { if (layer) { layer.el.remove(); layer = null; } }
  function open(owner, cls, id) {
    close();
    const r = owner.getBoundingClientRect();
    const el = document.createElement('div');
    el.className = cls;
    if (id) el.id = id;
    el.style.cssText = `position:absolute;left:${r.left + scrollX}px;top:${r.bottom + scrollY + 2}px;z-index:1000;background:#fff;border:1px solid #ccc;padding:6px;min-width:200px`;
    document.body.appendChild(el);
    layer = { el, owner };
    return el;
  }
  function showValue(root, text) {
    const ph = root.querySelector('.phoenix-select__placeHolder, .el-select__placeholder');
    ph.textContent = text;
    ph.classList.remove('phoenix-select__placeHolder--show', 'is-transparent');
    root.dataset.value = text;
  }

  // ---- 北森 phoenix-select ----
  function openPhoenixList(root) {
    const el = open(root, 'common-unmodeled-layer');
    el.innerHTML = '<div class="phoenix-selectList phoenix-selectList--search"><div class="phoenix-selectList__searchWrapper">'
      + '<div class="phoenix-input phoenix-search"><input class="phoenix-input__input" type="text" placeholder="搜索"></div></div>'
      + '<ul class="phoenix-selectList__list"><div class="phoenix-selectList__virtualList"></div></ul></div>';
    const list = el.querySelector('.phoenix-selectList__virtualList');
    const all = root.dataset.options.split(',');
    const render = (q) => {
      list.innerHTML = '';
      all.filter((o) => !q || o.includes(q)).slice(0, 6).forEach((o) => {  // 虚拟列表：只渲染 6 项
        const li = document.createElement('li');
        li.className = 'phoenix-selectList__listItem';
        li.innerHTML = '<div class="phoenix-selectList__singleLabel"><span><span></span></span></div>';
        li.querySelector('span span').textContent = o;
        li.addEventListener('click', () => { showValue(root, o); close(); });
        list.appendChild(li);
      });
    };
    render('');
    el.querySelector('input').addEventListener('input', (e) => render(e.target.value.trim()));
  }

  function openPhoenixDate(root) {
    const month = root.dataset.date === 'month';
    const el = open(root, 'common-unmodeled-layer');
    el.innerHTML = `<div class="phoenix-date-picker"><div class="phoenix-calendar${month ? ' phoenix-calendar-month-calendar' : ''}">`
      + '<div class="phoenix-calendar-input-wrap"><input class="phoenix-calendar-input" placeholder="请输入"></div>'
      + '<div class="phoenix-calendar-header"><a class="phoenix-calendar-prev-year-btn" role="button">«</a>'
      + '<a class="phoenix-calendar-year-select" role="button">2004年</a><a class="phoenix-calendar-next-year-btn">»</a></div></div></div>';
    const inp = el.querySelector('input');
    inp.value = month ? '2026-10' : '2004-01-01';
    const ok = month ? /^\d{4}-\d{2}$/ : /^\d{4}-\d{2}-\d{2}$/;
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter' && ok.test(inp.value)) { showValue(root, inp.value); close(); } });
  }

  function openArea(root) {
    const el = open(root, 'common-unmodeled-layer');
    el.innerHTML = '<div class="area-selector-container"><div class="area-search-input"><input type="text" placeholder="搜索"></div>'
      + '<div class="area-data-container"></div></div>';
    const box = el.querySelector('.area-data-container');
    const render = (prov) => {
      box.innerHTML = '';
      (prov ? AREAS[prov] : Object.keys(AREAS)).forEach((name) => {
        const item = document.createElement('div');
        item.className = 'area-item-container';
        item.innerHTML = '<div class="area-item-name"><span class="area-text-label"></span></div>';
        item.querySelector('span').textContent = name;
        item.addEventListener('click', () => {
          if (!prov && AREAS[name].length) render(name);
          else { showValue(root, prov ? `${prov}/${name}` : name); close(); }
        });
        box.appendChild(item);
      });
    };
    render(null);
  }

  // ---- Element Plus ----
  function openElMonth(root) {
    const id = root.getAttribute('aria-controls');
    const el = open(root, 'el-popper', id);
    const st = { y: new Date().getFullYear() };
    const render = () => {
      el.innerHTML = '<div class="el-picker-panel el-date-picker"><div class="el-date-picker__header">'
        + '<button type="button" class="el-picker-panel__icon-btn d-arrow-left"></button>'
        + `<span class="el-date-picker__header-label">${st.y} 年</span>`
        + '<button type="button" class="el-picker-panel__icon-btn d-arrow-right"></button></div><table class="el-month-table"></table></div>';
      el.querySelector('.d-arrow-left').onclick = (e) => { if (STRICT && !e.isTrusted) return; st.y--; render(); };
      el.querySelector('.d-arrow-right').onclick = (e) => { if (STRICT && !e.isTrusted) return; st.y++; render(); };
      const table = el.querySelector('table');
      for (let r = 0; r < 3; r++) {
        const tr = table.insertRow();
        for (let c = 0; c < 4; c++) {
          const k = r * 4 + c + 1;
          const td = document.createElement('td');
          td.innerHTML = '<div><span class="cell"></span></div>';
          td.querySelector('.cell').textContent = CN[k - 1] + '月';
          td.onclick = (e) => { if (STRICT && !e.isTrusted) return; root.dataset.value = `${st.y}-${pad(k)}`; root.querySelector('input').value = root.dataset.value; close(); };
          tr.appendChild(td);
        }
      }
    };
    render();
  }

  function openElSelect(root) {
    const el = open(root, 'el-select-dropdown');
    const ul = document.createElement('ul');
    root.dataset.options.split(',').forEach((o) => {
      const li = document.createElement('li');
      li.className = 'el-select-dropdown__item';
      li.innerHTML = '<span></span>';
      li.firstChild.textContent = o;
      li.onclick = (e) => { if (STRICT && !e.isTrusted) return; showValue(root, o); close(); };
      ul.appendChild(li);
    });
    el.appendChild(ul);
  }

  document.addEventListener('mousedown', (e) => {
    if (layer && layer.el.contains(e.target)) return;
    const elm = e.target.closest('.el-date-editor');
    if (elm && STRICT && !e.isTrusted) return;
    if (elm) {
      // Element Plus：按在没获得焦点的 <input> 上不打开
      if (e.target.tagName === 'INPUT' && document.activeElement !== e.target) return;
      if (!layer || layer.owner !== elm) openElMonth(elm);
      return;
    }
    if (!e.target.closest('.phoenix-select, .el-select')) close();
  });
  document.addEventListener('click', (e) => {
    if (layer && layer.el.contains(e.target)) return;
    const ph = e.target.closest('.phoenix-select');
    const es = e.target.closest('.el-select');
    if ((es || (layer && layer.el.matches('.el-select-dropdown, .el-popper'))) && STRICT && !e.isTrusted) return;
    if (ph && (!layer || layer.owner !== ph)) {
      if (ph.dataset.date) openPhoenixDate(ph);
      else if (ph.dataset.area !== undefined) openArea(ph);
      else openPhoenixList(ph);
    } else if (es && (!layer || layer.owner !== es)) openElSelect(es);
    if (e.target.closest('[data-submit]')) window.__submitted = true;
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

  window.__formValues = () => {
    const out = {};
    document.querySelectorAll('[data-key]').forEach((el) => {
      out[el.dataset.key] = el.dataset.value || (el.tagName === 'INPUT' ? el.value : '');
    });
    out.__submitted = window.__submitted;
    return out;
  };
})();
