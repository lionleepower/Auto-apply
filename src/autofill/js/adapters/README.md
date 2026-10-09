# 招聘系统适配器

页面脚本 = **通用核心**（`../autofill.js`）+ 这个目录下每个招聘系统一个适配器文件。`pipeline.page_script()` 把它们按文件名顺序拼进核心的 `/*@@ADAPTERS@@*/` 挂载点（同一个闭包里，能直接用核心的 `clean` / `tap` / `fillCustomSelect` / `dateParts` 等函数）。

**为什么分开**：不同招聘系统的组件库完全不同。以前所有特例都混在通用代码里，改一个系统的逻辑会误伤别的系统（飞书的标签修复曾经让 Moka 的所有下拉丢了标签）。现在：

- 只有 `detect()` 命中的适配器生效，其余完全不参与；一个都没命中就走纯通用逻辑
- 适配器之间互不可见，改 `moka.js` 不可能影响北森和飞书
- 报告和诊断里的 `adapters` 字段记录了本页命中了谁，排查先看它

## 现有适配器

| 文件 | 系统 | 识别方式 | 主要覆盖 |
|---|---|---|---|
| `moka.js` | Moka（`app.mokahr.com`，`sd-` 组件） | 网址 `mokahr.com`，或页面里有 `sd-Select-container` | 选项 / 日期面板选择器、年 + 月下拉、`apply-field` 标题、学校专业的搜索框、最接近学校（红标） |
| `feishu.js` | 飞书招聘（`*.jobs.feishu.cn`，`ud__` 组件） | 网址 `feishu.cn`，或页面里有 `ud__select` | 选项选择器 `.ud__select__list__item` |
| `beisen.js` | 北森（`phoenix` 组件） | 网址 `zhiye.com / italent`，或页面里有 `phoenix` | 选项 `listItem`、地区选择 `area-text-label` |

## 一个适配器可以提供什么

```js
registerAdapter({
  id: 'xxx',
  detect: () => /xxx\.com$/.test(location.hostname) || !!document.querySelector('[class*="xx-"]'),

  // 追加的选择器（命中时才并进通用的）
  optionSel: '...',   // 下拉选项
  panelSel: '...',    // 日期面板
  selectSel: '...',   // 下拉容器
  panel: { prevYear: /re/, nextYear: /re/, prevMonth: /re/, nextMonth: /re/, excludeCell: /re/ },  // 日期面板按钮 / 非本月格

  // 钩子：返回 undefined = 「我不管，交给通用逻辑」
  isSelectRoot(el) {},          // 这个元素是不是下拉
  collectExtra({ out, seen, inSeen }) {},   // 额外的控件（比如 Moka 的「年 + 月」下拉组合）
  label(root) {},               // 找标签
  currentValue(c) {},           // 读页面上已有的值
  fillDate(root, text, iso, selectLike) {}, fillDateRange(root, texts, isos) {},
  fillText(el, candidates) {},  // 文本框的填法（比如带候选的搜索框）
  panelHeader(panel) {},        // 日期面板的标题条
});
```

## 新增一个系统的流程

1. 在插件侧边栏「导出诊断」，拿到 `runs/diag-*.json`（含 `adapters`、控件结构、`unlabeled` 无标签字段的周围 HTML）
2. 新建 `xxx.js`，写 `detect` 和需要的覆盖项
3. 在 `demo/` 里按诊断里的真实结构做一个复刻页；`tests/test_adapters.py` 里加命中判断和「没有这个适配器就填不了」的测试
4. 把诊断脱敏放进 `tests/fixtures/`，在 `tests/test_ats_regression.py` 里补期望
5. 跑全量测试（`.venv\Scripts\python -m pytest -q`），然后同步插件并在 `edge://extensions` 重新加载

## 调试开关

页面里 `window.__afForceAdapter = 'none'` 只走通用逻辑；设成某个 id 则只启用它（改完刷新页面前设）。`window.__autofill.adapters()` 看本页命中了谁，`window.__autofill.optionSelector()` 看当前的选项选择器。
