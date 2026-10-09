"""适配器框架：按招聘系统分开的专用逻辑 + 通用兜底。

目标是隔离：改某个系统的适配器，不能影响别的系统；一个都没命中时走纯通用逻辑。
"""

import pytest

from autofill.pipeline import extract, page_script, run

from .conftest import demo_url


def adapters_of(page) -> list[str]:
    page.evaluate(page_script())
    page.evaluate("() => window.__autofill.extract()")
    return page.evaluate("() => window.__autofill.adapters()")


@pytest.mark.parametrize("demo,expected", [
    ("moka_sd_style", ["moka"]),
    ("feishu_ud_style", ["feishu"]),
    ("beisen_style", ["beisen"]),
    ("beisen_oppo_style", ["beisen"]),
    # 其余是 AntD / Element 风格或自研结构：不命中任何适配器，走通用逻辑
    ("feishu_style", []),
    ("moka_style", []),
    ("xhs_style", []),
    ("portfolio_style", []),
])
def test_adapter_detection_by_page_signature(page, demo, expected):
    page.goto(demo_url(demo))
    assert adapters_of(page) == expected


def test_forced_adapter_modes(page):
    page.add_init_script("window.__afForceAdapter = 'none'")
    page.goto(demo_url("moka_sd_style"))
    assert adapters_of(page) == []


def test_adapter_hooks_fall_back_to_generic_when_they_do_not_care(page, profile, tmp_path):
    # 命中飞书适配器的页面上，通用逻辑照常工作（飞书适配器只追加选项选择器）
    page.goto(demo_url("feishu_ud_style"))
    report = run(page, profile, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    assert got["name"] == "张三" and got["gender"] == "男" and got["degree"] == "硕士"
    assert not [i for i in report["items"] if i["status"] == "uncertain"]


def test_feishu_adapter_is_what_makes_ud_dropdowns_work(page, profile, tmp_path):
    # 必要：不命中适配器（纯通用）时，飞书 ud 下拉的选项认不出来
    page.add_init_script("window.__afForceAdapter = 'none'")
    page.goto(demo_url("feishu_ud_style"))
    run(page, profile, report_dir=tmp_path)
    assert page.evaluate("() => window.__formValues()").get("gender", "") == ""


def test_other_adapters_do_not_leak_into_feishu_pages(page, profile, tmp_path):
    # 隔离：硬让 Moka 适配器去处理飞书页面，飞书的选项也认不出来——Moka 的选择器不会漏过去，反之亦然
    page.add_init_script("window.__afForceAdapter = 'moka'")
    page.goto(demo_url("feishu_ud_style"))
    run(page, profile, report_dir=tmp_path)
    assert page.evaluate("() => window.__formValues()").get("gender", "") == ""


def test_moka_adapter_is_what_makes_year_month_selects_work(page, profile, tmp_path):
    page.add_init_script("window.__afForceAdapter = 'none'")
    page.goto(demo_url("moka_sd_style"))
    run(page, profile, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    assert got["period"] != "2025-09~2026-12"  # 纯通用逻辑填不了「年 + 月」下拉组合


def test_beisen_adapter_options_are_not_in_the_generic_selector(page):
    # 北森的选项选择器只在命中北森页面时才追加
    page.goto(demo_url("feishu_style"))
    assert adapters_of(page) == []
    sel = page.evaluate("() => window.__autofill.optionSelector()")
    assert "area-text-label" not in sel and "ud__select" not in sel and "Menu-content-item" not in sel
    page.goto(demo_url("beisen_style"))
    assert adapters_of(page) == ["beisen"]
    assert "area-text-label" in page.evaluate("() => window.__autofill.optionSelector()")


def test_report_and_extract_record_the_active_adapter(page, profile, tmp_path):
    page.goto(demo_url("moka_sd_style"))
    data = extract(page)
    assert data["adapters"] == ["moka"]
    report = run(page, profile, report_dir=tmp_path)
    assert report["adapters"] == ["moka"]


# ---------- 学校：先试英文名，再选最接近的并红标 ----------

def _school_run(page, profile, tmp_path, school, school_en=None):
    p = profile.model_copy(deep=True)
    p.basic.available = "2026-11-18"
    p.education[0].school, p.education[0].school_en = school, school_en
    page.goto(demo_url("moka_sd_style"))
    report = run(page, p, report_dir=tmp_path)
    page.wait_for_timeout(600)
    item = next(i for i in report["items"] if i["label"] == "学校名称")
    return page.evaluate("() => window.__formValues()")["school"], item


def test_school_falls_back_to_english_name(page, profile, tmp_path):
    value, item = _school_run(page, profile, tmp_path, "爱丁堡大学", "The University of Edinburgh")
    assert value == "The University of Edinburgh" and item["status"] == "filled" and not item["risk"], item


def test_school_picks_closest_candidate_and_marks_it_red(page, profile, tmp_path):
    value, item = _school_run(page, profile, tmp_path, "示例理工学院")  # 库里只有「示例理工大学」
    assert value == "示例理工大学"
    assert item["status"] == "uncertain" and item["risk"] == "high", item
    assert "最接近" in item["note"] and "示例理工学院" in item["note"]
    # 页面上的高亮是红色（和「未填」同色），不是黄色
    color = page.evaluate("() => document.querySelector('[data-key=school]').style.outlineColor")
    assert color in ("rgb(220, 38, 38)", "#dc2626"), color


def test_school_with_nothing_similar_stays_unfilled(page, profile, tmp_path):
    value, item = _school_run(page, profile, tmp_path, "不存在的大学")
    assert value == "" and item["status"] == "unfilled" and not item["risk"], item


# ---------- 普通下拉：没有完全对得上时选最像的，标黄 ----------

SELECT_PAGE = """<html><body><h3>教育经历</h3>
<label>专业 <select><option value="">请选择</option><option>计算机科学</option><option>软件工程</option><option>金融学</option></select></label>
<label>学历 <select><option value="">请选择</option><option>本科</option><option>硕士</option></select></label>
</body></html>"""


def test_plain_select_guesses_closest_option_and_marks_it_yellow(page, profile, tmp_path):
    p = profile.model_copy(deep=True)
    p.education[0].major = "计算机应用"
    page.set_content(SELECT_PAGE)
    report = run(page, p, report_dir=tmp_path)
    major = next(i for i in report["items"] if i["label"] == "专业")
    assert page.eval_on_selector("select", "s => s.value") == "计算机科学"
    assert major["status"] == "uncertain" and not major["risk"] and "最接近" in major["note"], major


def test_single_character_answers_are_never_guessed(page, profile, tmp_path):
    p = profile.model_copy(deep=True)
    p.basic.gender = "男"
    page.set_content("<html><body><h3>基本信息</h3><label>性别 <select><option value=''>请选择</option><option>女</option><option>保密</option></select></label></body></html>")
    report = run(page, p, report_dir=tmp_path)
    gender = next(i for i in report["items"] if i["label"] == "性别")
    assert gender["status"] == "unfilled" and page.eval_on_selector("select", "s => s.value") == ""  # 男 ≠ 女，不猜
