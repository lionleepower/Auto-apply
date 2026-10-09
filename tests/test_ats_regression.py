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
