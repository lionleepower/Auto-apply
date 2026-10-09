// 北森（phoenix 组件库，招聘站常见于 *.zhiye.com / italent）。这个文件里的东西只在北森页面上生效。
// 2026-10-07 讯飞诊断：选项是 li.phoenix-selectList__listItem，地区选择器是 .area-text-label。
registerAdapter({
  id: 'beisen',
  detect: () => /(^|\.)(zhiye|italent|beisen)\./.test(location.hostname)
    || !!document.querySelector('[class*="phoenix"]'),
  optionSel: '[class*="listItem"], [class*="area-text-label"]',
});
