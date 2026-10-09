"""真实站点回归：用「导出诊断」存下的页面结构（已脱敏）重跑字段识别，再加飞书 ud 组件的最小复现页。

新遇到的站点：把诊断文件脱敏后放进 tests/fixtures/，在这里补几条期望，以后改规则就不会把它改坏。
"""

import json
from pathlib import Path

import pytest

from autofill.pipeline import annotate, plan, run

from .conftest import ROOT, demo_url

FIX = Path(__file__).parent / "fixtures"


def mapped(name: str, profile):
    fields = annotate([dict(f) for f in json.loads((FIX / name).read_text(encoding="utf-8"))["fields"]])
    items, _ = plan(fields, profile, None)
    return list(zip(fields, items))


def find(rows, **want):
    """按 label / placeholder 的前缀找字段，返回（字段, 识别结果）列表。"""
    out = []
    for f, it in rows:
        if all(str(f.get(k, "")).startswith(v) for k, v in want.items()):
            out.append((f, it))
    assert out, f"夹具里没有 {want}"
    return out


# ---------- 百度校招 ----------

def test_baidu_label_hidden_in_placeholder_is_recovered(profile):
    rows = mapped("baidu_campus_fields.json", profile)
    got = {f["label"]: it.key for f, it in rows if f.get("_label_from_ph")}
    assert got["是否接受调剂"] == "basic.accept_adjustment"
    assert got["目标工作城市"] == "basic.expected_city"
    assert got["学历"] == "education.degree"
    assert got["成绩排名"] == "education.rank"


def test_baidu_school_hint_is_not_mistaken_for_enrol_date(profile):
    # 「请输入学校全称…」里的「入学」曾被匹配成入学时间，导致学校下拉去开日历面板
    rows = mapped("baidu_campus_fields.json", profile)
    schools = find(rows, label="请输入学校全称")
    assert {it.key for _, it in schools} == {"education.school"}


def test_baidu_gpa_has_separate_scale_selector(profile):
    rows = mapped("baidu_campus_fields.json", profile)
    assert {it.key for _, it in find(rows, label="GPA类型")} == {"education.gpa_scale"}
    assert {it.key for _, it in find(rows, label="GPA成绩")} == {"education.gpa"}


def test_baidu_id_number_row_and_work_description_section(profile):
    rows = mapped("baidu_campus_fields.json", profile)
    ids = [it for f, it in rows if f.get("section") == "证件号码" and f["kind"] == "text"]
    assert [it.key for it in ids] == ["basic.id_number"]  # 「证件类型下拉 + 号码输入框」一行，标签只在行首
    for f, it in find(rows, label="工作描述"):
        assert f["section_kind"] == "work" and it.key == "work.description"  # 标题链全是别的字段的标签


def test_baidu_overall_mapping_rate(profile):
    rows = [(f, it) for f, it in mapped("baidu_campus_fields.json", profile) if f["kind"] not in ("file", "checkbox")]
    unmapped = [f["label"] or f["placeholder"] for f, it in rows if not it.key]
    assert len(unmapped) <= 9, unmapped  # 目前 8 个（面试方式、行业类别等站点专有项）；变多就是改坏了


# ---------- 飞书招聘（得物） ----------

def test_feishu_unlabeled_date_boxes_are_start_end_pairs(profile):
    rows = mapped("feishu_poizon_fields.json", profile)
    pairs = [(f["section_kind"], it.key) for f, it in rows if f.get("_datebox")]
    assert pairs[:2] == [("education", "education.start"), ("education", "education.end")]
    assert ("work", "work.start") in pairs and ("project", "project.end") in pairs
    assert len(pairs) % 2 == 0


def test_feishu_example_in_parentheses_does_not_trigger_github(profile):
    rows = mapped("feishu_poizon_fields.json", profile)
    ai = find(rows, label="请列出你常用的 AI 工具")
    assert all(it.key != "basic.github" for _, it in ai)


def test_feishu_expected_graduation_label(profile):
    rows = mapped("feishu_poizon_fields.json", profile)
    assert find(rows, label="预计毕业时间")[0][1].key == "education.end"


# ---------- 飞书 ud 下拉最小复现页 ----------

def test_feishu_ud_select_demo(page, profile, tmp_path):
    page.goto(demo_url("feishu_ud_style"))
    report = run(page, profile, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    assert got["gender"] == "男" and got["degree"] == "硕士"
    assert (got["edu_start"], got["edu_end"]) == ("2025-09", "2026-12")  # 无标签的日期范围框
    assert got["ai_tools"] == "" and got["__submitted"] is False  # AI 工具问句不能被填成 GitHub 链接
    bad = [it for it in report["items"] if it["status"] == "uncertain"]
    assert not bad, bad


# ---------- 辅助规则 ----------

def test_gpa_scale_candidates():
    from autofill.rules import gpa_scale_candidates

    assert gpa_scale_candidates("3.8/4.0")[0] == "4分制"
    assert gpa_scale_candidates("90/100")[0] == "百分制"
    assert gpa_scale_candidates("4.5/5")[0] == "5分制"
    assert gpa_scale_candidates("3.8") == ["其他"]


def test_prompt_words_and_bracket_examples_do_not_confuse_rules():
    from autofill.rules import match_label

    assert match_label("请输入学校", "education") == "education.school"
    assert match_label("请输入姓名", "basic") == "basic.name"
    long = "请列出你常用的 AI 工具 & 模型（编码工具：Cursor、GitHub Copilot...... 大模型：GPT-4o）"
    assert match_label(long, "basic") is None
    assert match_label("GitHub 链接", "basic") == "basic.github"


def test_previous_salary_and_referral_channel_are_left_alone():
    from autofill.rules import IGNORE, match_label

    assert match_label("职位年薪", "work") == IGNORE  # 不是职位名称
    assert match_label("推荐方式", "basic") == IGNORE  # 不是内推码
    assert match_label("内推码", "basic") == "ctx.referral"
    assert match_label("职位名称", "work") == "work.title"


def test_filled_select_value_is_not_taken_as_its_label(page, profile, tmp_path):
    # 二次填写：下拉里已有「男」「硕士」时，标签不能变成选中的值
    page.goto(demo_url("feishu_ud_style"))
    run(page, profile, report_dir=tmp_path)
    labels = page.evaluate("() => window.__autofill.extract().fields.map((f) => f.label)")
    assert "性别" in labels and "学历" in labels and "男" not in labels and "硕士" not in labels


# ---------- 作品集、字数上限、单行输入框 ----------

LONG = "背景：我同时投递约 70 家公司，表单各不相同。\n方案：与 AI 协作开发自动填写工具，我定边界并验收。\n" + "补充说明" * 60
SHORT = "背景：同时投递约 70 家，表单各不相同。方案：与 AI 协作开发“只填不交”的自动填写工具，我定边界并验收。"


def _with_answers(profile, **answers):
    p = profile.model_copy(deep=True)
    p.answers = {k: v for k, v in answers.items()}
    return p


def test_multiline_answer_in_single_line_input_is_not_a_failure(page, profile, tmp_path):
    # 单行输入框会吞掉换行：值和候选值不一样，曾被判成「填写失败」（得物）
    p = _with_answers(profile, **{"常用的.*AI.*工具": "编码协作：Claude Code。\n大模型 API：DeepSeek。"})
    page.goto(demo_url("portfolio_style"))
    report = run(page, p, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    assert got["ai_tools"] == "编码协作：Claude Code。大模型 API：DeepSeek。"
    item = next(i for i in report["items"] if "常用的" in i["label"])
    assert item["status"] == "filled", item


def test_over_limit_switches_to_the_short_version(page, profile, tmp_path):
    assert len(LONG) > 200 and len(SHORT) <= 200
    p = _with_answers(profile, **{"与.*AI.*协作": LONG + "\n---\n" + SHORT})
    page.goto(demo_url("portfolio_style"))
    report = run(page, p, report_dir=tmp_path)
    assert page.evaluate("() => window.__formValues()")["ai_story"] == SHORT
    item = next(i for i in report["items"] if "协作" in i["label"])
    assert item["status"] == "filled" and "较短的版本" in item["note"], item


def test_over_limit_without_short_version_is_cut_and_flagged(page, profile, tmp_path):
    p = _with_answers(profile, **{"与.*AI.*协作": LONG})
    page.goto(demo_url("portfolio_style"))
    report = run(page, p, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")["ai_story"]
    assert 0 < len(got) <= 200 and LONG.replace("\n", "").startswith(got)  # 只截取，不改写
    item = next(i for i in report["items"] if "协作" in i["label"])
    assert item["status"] == "uncertain" and "已截取" in item["note"], item  # 必须让人看到


def test_portfolio_upload_and_links(page, profile, tmp_path):
    cv, zp = tmp_path / "cv.pdf", tmp_path / "my_portfolio.zip"
    cv.write_bytes(b"%PDF-1.4 demo")
    zp.write_bytes(b"PK\x03\x04 demo")
    p = profile.model_copy(update={"resume_file": str(cv), "portfolio_file": str(zp)})
    p.basic.github = "github.com/zhangsan-demo"
    page.goto(demo_url("portfolio_style"))
    report = run(page, p, report_dir=tmp_path, upload=True)
    got = page.evaluate("() => window.__formValues()")
    assert got["resume"] == "cv.pdf" and got["portfolio"] == "my_portfolio.zip"  # 各传各的框
    assert "已上传简历：cv.pdf" in report["upload"] and "已上传作品集：my_portfolio.zip" in report["upload"]
    assert got["work_url"] == "github.com/zhangsan-demo"  # 作品链接缺省用 GitHub
    assert got["__submitted"] is False


def test_portfolio_description_is_built_from_project_links(profile):
    from autofill.pipeline import Item, resolve

    p = profile.model_copy(deep=True)
    p.projects[0].url = "github.com/zhangsan-demo/agent"
    it = Item(id="f1", label="作品描述", section="获奖信息", section_kind="award", kind="text", index=0)
    it.key = "portfolio.desc"
    resolve(it, {"placeholder": "", "label": "作品描述", "kind": "text", "id": "f1"}, p, {})
    assert "github.com/zhangsan-demo/agent" in it.candidates[0] and p.projects[0].name in it.candidates[0]


def test_portfolio_target_ignores_resume_and_other_attachments():
    from autofill.pipeline import pick_portfolio_field

    fields = [{"kind": "file", "label": "上传简历", "section": "附件简历", "id": "a"},
              {"kind": "file", "label": "上传论文 仅支持 pdf", "section": "学术成果", "id": "b"},
              {"kind": "file", "label": "拖拽/点击上传您的作品集", "section": "作品", "id": "c"}]
    assert pick_portfolio_field(fields)["id"] == "c"
    assert pick_portfolio_field(fields[:2]) is None


def test_char_counter_text_is_never_taken_as_the_question(page, profile, tmp_path):
    # 超限后站点在题目旁显示「超出 N 个字符」；再次填写时题目不能被它顶替
    p = _with_answers(profile, **{"与.*AI.*协作": LONG})
    page.goto(demo_url("portfolio_style"))
    run(page, p, report_dir=tmp_path)  # 第一次：只放得下截取版，计数提示可能还在
    page.evaluate("() => { const i = document.querySelector('[data-key=ai_story]'); i.value = 'x'.repeat(260); i.dispatchEvent(new Event('input')); }")
    labels = page.evaluate("() => window.__autofill.extract().fields.map((f) => f.label)")
    assert not any(l.startswith("超出") for l in labels), labels


# ---------- Moka（众安保险，sd- 组件） ----------

def test_moka_zhongan_labels(profile):
    rows = mapped("moka_zhongan_fields.json", profile)
    assert find(rows, label="出生日期")[0][1].key == "basic.birth_date"
    assert find(rows, label="毕业时间")[0][1].key == "education.end"
    assert find(rows, label="姓名")[0][1].key == "basic.name"
    assert {it.key for _, it in find(rows, label="学校名称")} == {"education.school"}
    assert {it.key for _, it in find(rows, label="专业名称")} == {"education.major"}


def test_moka_open_question_uses_fixed_answer(profile):
    p = _with_answers(profile, **{"用AI解决的.*场景": "背景：A。\n---\n背景：B。"})
    rows = []
    fields = annotate([dict(f, value="") for f in json.loads((FIX / "moka_zhongan_fields.json").read_text("utf-8"))["fields"]])
    items, _ = plan(fields, p, None)
    hit = [it for f, it in zip(fields, items) if f["label"].startswith("简单描述1个")]
    assert hit and hit[0].key == "answer:用AI解决的.*场景"


def test_moka_sd_demo_year_month_selects_and_date_panel(page, profile, tmp_path):
    page.goto(demo_url("moka_sd_style"))
    profile = profile.model_copy(deep=True)
    profile.basic.available = "2026-11-18"
    report = run(page, profile, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    assert got["avail"] == "2026-11-18"  # SHEIN 的日视图面板：标题「2026年 十月」，prev-tool / next-tool 翻月，fade 格不点
    assert (got["gender"], got["degree"]) == ("男", "硕士")  # 输入框形态的下拉；标签在同一表单项的 .title 里
    labels = {i["label"] for i in report["items"]}
    assert {"性别", "最高学历"} <= labels, labels  # 曾因把整个下拉控件排除出标签文字而丢了标签
    assert got["grad"] == "2026-12"  # 毕业时间 = 年下拉 + 月下拉
    assert got["period"] == "2025-09~2026-12"  # 就读时间 = 起止各一对，共 4 个下拉
    assert got["birth"] == "2000-05-01"  # sd-panal 日期面板：双箭头翻年 → 点月 → 点日
    assert got["name"] == "张三" and got["school"] == "示例大学" and got["__submitted"] is False
    bad = [i["label"] for i in report["items"] if i["status"] in ("unfilled", "uncertain")]
    assert not bad, bad


def test_diagnose_records_context_of_unlabeled_fields(page):
    from autofill.pipeline import extract

    page.goto(demo_url("feishu_ud_style"))
    extract(page)  # 注入页面脚本
    data = page.evaluate("async () => await window.__autofill.diagnose(3)")
    assert "unlabeled" in data and any("<" in u["html"] for u in data["unlabeled"])


def test_moka_prefilled_year_month_selects_are_still_recognised_and_compared(page, profile, tmp_path):
    # 站点解析简历后年月已经选好，输入框 placeholder 变成空：仍要认出来，一致就保留，不一致才改
    page.goto(demo_url("moka_sd_style"))
    run(page, profile, report_dir=tmp_path)  # 第一次：填好
    page.evaluate("() => document.querySelectorAll('.sd-Select-container input').forEach((i) => i.removeAttribute('placeholder'))")
    again = run(page, profile, report_dir=tmp_path)
    by_label = {i["label"]: i for i in again["items"]}
    assert by_label["就读时间"]["status"] == "kept" and by_label["毕业时间"]["status"] == "kept", by_label
    assert page.evaluate("() => window.__formValues()")["period"] == "2025-09~2026-12"
    # 页面上是别的起止时间：资料是「2025-09 ~ 2026-12」，只对上开始时间不算一致
    page.evaluate("() => { const s = document.querySelectorAll('[data-key=period] .sd-Input-display-value'); s[2].textContent = '2024'; s[3].textContent = '6月'; }")
    p2 = run(page, profile, report_dir=tmp_path, overwrite=True)
    assert page.evaluate("() => window.__formValues()")["period"] == "2025-09~2026-12"
    assert {i["label"]: i for i in p2["items"]}["就读时间"]["status"] == "filled"


def test_available_text_is_not_forced_into_a_date_picker(page, profile, tmp_path):
    # 资料里「到岗时间」写的是「随时到岗」：日期框不硬填，并且说清楚原因
    page.goto(demo_url("moka_sd_style"))
    report = run(page, profile, report_dir=tmp_path)
    item = next(i for i in report["items"] if i["label"] == "可到岗实习时间")
    assert item["status"] == "unfilled" and "不是具体日期" in item["note"], item
    assert page.evaluate("() => window.__formValues()")["avail"] == ""


def test_internship_end_time_is_not_confused_with_availability():
    from autofill.rules import IGNORE, match_label

    assert match_label("预计实习结束时间", "basic") == IGNORE
    assert match_label("可到岗实习时间", "basic") == "basic.available"


def test_stale_marks_from_earlier_extractions_do_not_hide_labels(page, profile, tmp_path):
    # 页面没刷新、旧版本把下拉里的 <input> 标成了文本框；新版本把整个 label 当成下拉后，
    # 里面还留着旧的 data-af-id，导致「容器里有多个控件」而放弃找标题（众安 / SHEIN 2026-10-09）
    page.goto(demo_url("moka_sd_style"))
    page.evaluate("() => document.querySelectorAll('.sd-Select-container input').forEach((i, k) => { i.setAttribute('data-af-id', 'stale' + k); i.setAttribute('data-af-status', 'unfilled'); })")
    report = run(page, profile, report_dir=tmp_path)
    labels = {i["label"]: i["status"] for i in report["items"]}
    assert labels.get("性别") == "filled" and labels.get("最高学历") == "filled", labels
    # 陈旧标记被清掉：下拉里面的 <input> 不再带标记；自由输入框（学校 / 专业）本身是当前控件，保留
    assert page.evaluate("() => document.querySelectorAll('.apply-field.Select input[data-af-id]').length") == 0
    assert labels.get("学校名称") == "filled" and labels.get("专业名称") == "filled", labels  # string_info 当文本框，不当下拉


def test_open_popup_text_is_not_a_label(page, profile, tmp_path):
    # 上一个日期面板没关干净时，它的文字（「2026 年 十月 一 二 三 …」）不能被当成别的字段的标签
    page.goto(demo_url("moka_sd_style"))
    page.click("#avail")  # 面板留在页面上
    labels = page.evaluate("() => (window.__autofill ? 0 : 0) || 0")
    from autofill.pipeline import extract

    fields = extract(page)["fields"]
    assert not any("一 二 三" in f["label"] for f in fields), [f["label"] for f in fields]
