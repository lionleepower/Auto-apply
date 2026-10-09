# 测试方法

四层，从快到慢、从离线到真实站点。改代码后先跑第 1 层；改了页面脚本或插件，再用第 3、4 层在真实站点上验证。

| 层 | 测什么 | 命令 | 耗时 | 需要 |
|---|---|---|---|---|
| 1. 离线全套 | 规则、资料模型、AI 映射、demo 表单端到端、插件流程（真跑 `extension/flow.js`）、桌面助手后端、真实站点回归 | `.venv\Scripts\python -m pytest -q` | ~5 分钟 | 本机 Edge（无头） |
| 2. 离线审计 | 用导出的真实页面结构重跑字段识别 | `.venv\Scripts\autofill --profile profile.example.yaml audit [文件] -v` | 秒级 | `runs/diag-*.json` |
| 3. 真实站点 · 命令行引擎 | Playwright 驱动在已登录的真实网申页上填 | `python tools/live/live.py <站点> run --save` | 1–2 分钟 / 站 | 已登录的 `.browser-data-msedge` |
| 4. 真实站点 · 插件 | 真实插件代码 + 真实点击，单页或多页并行 | `python tools/live/ext_run.py <站点> [<站点> …]` | ~2 分钟 | 同上 |

以下命令都在项目根目录 `E:\leyan\auto-apply` 下运行，`python` 指 `.venv\Scripts\python`。

---

## 1. 离线全套（pytest）

```bash
.venv\Scripts\python -m pytest -q                      # 全部
.venv\Scripts\python -m pytest -q tests/test_rules.py  # 只跑一个文件
```

| 文件 | 内容 |
|---|---|
| `test_rules.py` | 分区识别、标签 → 资料键、日期格式、地区拆分、备选值、页面值比较 |
| `test_resumes.py` | 多份简历、海外（英文）规则、证件号开关、常用问答 |
| `test_llm.py` | AI 映射的解析与容错（坏 JSON、编造的键） |
| `test_demo_forms.py` | 在 `demo/*.html` 上跑完整流程，和每个字段的标准答案逐一比对 |
| `test_server.py` | 插件后端接口 + 直接执行 `extension/flow.js` 的端到端流程；检查 `extension/autofill.js` 与源码同步 |
| `test_ats_regression.py` | 用 `tests/fixtures/` 里脱敏的真实站点诊断重跑识别（百度、飞书…） |
| `test_batch.py` | 投递表读取、内推码、进度记录 |
| `test_gui.py` | 桌面助手：知识库抽取 / 合并 / 缺失项、多厂商 AI 配置与故障转移 |

- 测试只用 `profile.example.yaml`（虚构人物），不读你的真实资料，不需要 API key
- 浏览器默认本机 Edge 无头；`AUTOFILL_TEST_BROWSER=chrome` 可切换
- **改了 `src/autofill/js/autofill.js` 之后**，`test_server.py` 会因为插件副本没同步而失败。先同步再测：
  ```bash
  .venv\Scripts\python -c "import sys; sys.path.insert(0,'src'); from autofill.server import sync_extension_js; sync_extension_js()"
  ```
  （重启本地服务也会自动同步）

---

## 2. 离线审计（不用打开网站）

1. 在插件侧边栏点「导出诊断」→ `runs/diag-<站点>-<时间>.json`（只有页面结构，不含资料值）
2. 重跑识别：
   ```bash
   .venv\Scripts\autofill --profile profile.example.yaml audit                     # runs/ 下全部诊断
   .venv\Scripts\autofill --profile profile.example.yaml audit runs/diag-xxx.json -v
   ```
3. 修好规则后，把诊断脱敏放进 `tests/fixtures/`，在 `tests/test_ats_regression.py` 里补期望，变成永久回归测试

---

## 3、4. 真实站点实测（`tools/live/`）

### 一次性准备

1. **登录**（只有你能做：账号、密码、验证码都不交给工具或 agent）：
   ```bash
   .venv\Scripts\autofill login
   ```
   在弹出的独立 Edge 里登录要测的网申站，关窗口即可。登录状态保存在 `.browser-data-msedge/`，和你日常用的 Edge 分开。过期了重来一次。
2. **站点列表**：复制 `tools/live/sites.example.json` 为 `tools/live/sites.json`（已在 `.gitignore`），填真实申请表链接和内推码：
   ```json
   { "oppo": { "url": "https://careers.oppo.com/...", "referral": "REF123456", "note": "分区编辑式，测试加 --save" } }
   ```
3. 资料用的是真实的 `profile.yaml`。

### 脚本

| 命令 | 作用 |
|---|---|
| `python tools/live/live.py <站点> shot` | 截图 + 字段概览（不填） |
| `python tools/live/live.py <站点> dry` | 只读预演：每个字段打算填什么（不改页面） |
| `python tools/live/live.py <站点> run [--save] [--overwrite]` | 命令行引擎真实填写；`--save` 点分区「保存」 |
| `python tools/live/live.py <站点> js "<表达式>"` | 注入页面脚本后执行 JS，打印结果（看 DOM 用） |
| `python tools/live/ext_run.py <站点>` | 插件单页实测（默认勾「保存」） |
| `python tools/live/ext_run.py <站点A> <站点B>` | 插件**多页并行**实测：依次点「填写本页」不等前一个，过程中来回切标签页核对侧边栏 |
| `python tools/live/ext_run.py … --debug` | 同上，并把各页面的调试日志写到 `runs/live/ext-trace-<站点>-<时间>.txt` |
| `python tools/live/trace.py <站点>` | 命令行引擎完整跑一遍，记录每一步（任务、点击落点、日期面板、弹框） |
| `python tools/live/trace.py <站点> --field 发表时间 [--value 2024-07]` | 只测一个字段（自动打开它所在的分区） |

通用：`--show` 把浏览器窗口放到屏幕上看着它填（默认在屏幕外，不打扰你用电脑）。

- 所有输出（报告、截图、日志）在 `runs/live/`
- `ext_run.py` 自己在端口 **8766** 起一个测试服务、用一份改连 8766 的插件副本（`.live-ext/`），跑完关掉；你日常的 8765 服务和装好的插件不受影响
- **同一时间只能跑一个实测脚本**：它们共用 `.browser-data-msedge`，Edge 不允许两个进程同时打开同一个配置目录

### 怎么读结果

```
summary: {'filled': 58, 'uncertain': 0, 'kept': 1, 'unfilled': 17, 'sensitive': 0}  耗时 102s
  分区「基础信息」未保存：个人照片不能为空、证件信息不能为空
  分区「教育经历」已保存  弹框：['确认：提示 保存前请确认以上经历 取消 确认']
  unfi custom_sele 个人信息 现居住地 | basic.current_city | 填写失败：只选到「江苏省 / 苏州市」
```

| 状态 | 含义 | 要不要管 |
|---|---|---|
| `filled` | 填上并核对过 | — |
| `kept` | 页面原值和资料一致（之前填过 / 站点预填） | — |
| `uncertain` | 点了但没核对到，或页面值和资料不一致没覆盖 | **要看**：多半是控件适配问题 |
| `unfilled` + `填写失败：…` | 尝试了但失败 | **要看**：适配问题 |
| `unfilled` + `资料里没有「…」` | 缺资料 | 补资料（不要编） |
| `unfilled` + `附件/勾选项请手动处理`、`该字段不自动填写` | 设计上不碰 | — |
| `sensitive` | 证件号等，按设置不自动填 | — |
| 分区「未保存」+ 网页报错 | 网页的必填校验没过；照片 / 证件这类只能你手动处理 | 看报错是不是缺资料 |

**判定通过**：`uncertain` 为 0；`unfilled` 里没有「填写失败」；分区「未保存」的原因只剩只能手动处理的项（照片、证件）。

### 排查一个填不上的字段

1. `python tools/live/trace.py <站点> --field <标签>`：单独复现
2. 看日志里的 `[autofill] tap <方式> <文字> <x> <y> <命中元素 class>`：
   - 命中元素是遮罩（`el-overlay…`）、导航栏 → 被挡住了（弹框没关？）
   - 命中 `null` 或坐标在屏幕外 → 量坐标时元素还没站稳 / 被滚出了视野
   - `step … header 年 月` → 日期面板每一步读到的年月
3. 单独能过、整体跑不过 → 用 `trace.py <站点>`（完整跑）或 `ext_run.py --debug`，看它前面的步骤留下了什么（弹框、滚动位置）
4. 看 DOM：`python tools/live/live.py <站点> js "() => document.querySelector('…').outerHTML"`
5. 修 `src/autofill/js/autofill.js`（控件操作）或 `rules.py`（标签规则）→ 同步插件 → 第 1 层 → 再实测

---

## 给 AI agent 复用：实测循环

把下面这段（改掉尖括号里的内容）直接发给 Claude Code 等 agent，它就能自己按「测 → 读报告 → 修 → 再测」循环到通过。前提：你已经跑过 `autofill login` 并配好 `tools/live/sites.json`。

```text
在 E:\leyan\auto-apply 里测试网申自动填写。先读 README.md 和 TESTING.md。
目标站点：<oppo iflytek>（在 tools/live/sites.json 里）。我已经用 `autofill login` 登录好了。

按这个循环做，直到通过或确认剩下的都是资料缺失：
1. 跑 `python tools/live/ext_run.py <站点…>`（多个站点就是并行测试）；分区编辑式站点默认会点「保存」。
2. 读输出和 runs/live/ 里最新的 run-*.json。按 TESTING.md「怎么读结果」分类：
   适配问题（uncertain、填写失败）要修；资料缺失列出来问我，不要编。
3. 对每个适配问题：用 `tools/live/trace.py <站点> --field <标签>` 复现，看调试日志找原因，
   修 src/autofill/js/autofill.js 或 rules.py，在 tests/ 里补测试。
4. 同步插件页面脚本（TESTING.md 第 1 节的命令），跑 `.venv\Scripts\python -m pytest -q`，必须全过。
5. 回到第 1 步。两次实测结果一致才算稳定。
最后用中文汇报：每个站点的 summary、修了什么（原因 + 改法）、还缺哪些资料。

规则（必须遵守）：
- 永远不点提交 / 投递 / 保存并提交；只允许点文字恰好是「保存」的分区按钮（脚本已经这样做）。
- 不登录、不输入密码或验证码；跳到登录页就停下来叫我重新 `autofill login`。
- 不编造资料（GPA、排名、日期、导师、部门…）；缺什么问我。
- 不改 profile.yaml 里的事实；不在聊天里复述证件号、手机号、API key。
- 同一时间只跑一个实测脚本（共用浏览器配置目录）；同一家公司不要开两个标签页同时填。
- 测试会在网申页上留下草稿 / 已保存的分区，这是预期的。
```

### 新增一个测试站点

1. 在 `tools/live/sites.json` 加一项（名字随意，`url` 是申请表页，不是职位详情页）
2. `autofill login` 里登录这个站
3. `python tools/live/live.py <名字> shot` 看字段是否都抽出来了、分区对不对
4. `python tools/live/live.py <名字> dry` 看映射，再 `ext_run.py <名字>` 实测
5. 修好后导出诊断、脱敏，放进 `tests/fixtures/` 做成回归测试（见第 2 层）
