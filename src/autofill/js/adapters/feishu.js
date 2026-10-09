// 飞书招聘（*.jobs.feishu.cn，组件库前缀 ud__）。这个文件里的东西只在飞书招聘页面上生效。
// 2026-10-09 得物诊断：选项类名是 ud__select__list__item，里面还嵌着空的 ud__select__list__item__check。
// 必须用精确类名：宽松匹配会让选项被当成「含子选项」而排除，报「dropdown did not open」。
registerAdapter({
  id: 'feishu',
  detect: () => /(^|\.)feishu\.cn$/.test(location.hostname)
    || !!document.querySelector('[class*="ud__select"], [class*="ud__input"]'),
  optionSel: '.ud__select__list__item',
});
