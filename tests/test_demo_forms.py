"""端到端：在 demo 表单上跑完整流程，和标准答案逐字段比对。"""

import pytest

from autofill.pipeline import run

from .conftest import demo_url

FEISHU_EXPECTED = {
    "name": "张三", "phone": "13800000000", "email": "zhangsan@example.com", "gender": "男",
    "birth": "2000-05-01", "political": "共青团员", "city": "上海", "available": "随时到岗",
    "edu.0.school": "示例大学", "edu.0.degree": "硕士研究生", "edu.0.major": "高性能计算与数据科学",
    "edu.0.start": "2025-09", "edu.0.end": "2026-12", "edu.0.overseas": "是",
    "edu.1.school": "示例理工大学", "edu.1.degree": "本科", "edu.1.major": "计算机科学",
    "edu.1.start": "2021-09", "edu.1.end": "2025-06", "edu.1.overseas": "否",
    "work.0.company": "示例科技", "work.0.title": "前端开发实习生", "work.0.start": "2023.08",
    "work.0.end": "2023.09", "work.0.desc": "用 Vue 开发天气组件。",
    "proj.0.name": "示例 Agent 项目", "proj.0.role": "独立开发", "proj.0.desc": "工具调用 Agent 与离线评测。",
    "proj.1.name": "示例性能评测", "proj.1.role": "负责人", "proj.1.desc": "4,096 核扩展性实验。",
    "english": "雅思", "github": "github.com/zhangsan-demo", "intro": "做事细致，重视数据可信度。",
    # 必须保持原样的字段
    "resume": "", "consent": False, "__submitted": False,
}

BEISEN_EXPECTED = {
    "name": "张三", "name_en": "San Zhang", "gender": "男", "phone": "13800000000",
    "email": "zhangsan@example.com", "political": "共青团员", "hometown": "江苏苏州", "exp_city": "上海",
    "edu.0.school": "示例大学", "edu.0.college": "信息学院", "edu.0.major": "高性能计算与数据科学",
    "edu.0.degree": "硕士", "edu.0.start": "2025-09", "edu.0.end": "2026-12", "edu.0.gpa": "3.8/4.0",
    "edu.1.school": "示例理工大学", "edu.1.college": "计算机学院", "edu.1.major": "计算机科学",
    "edu.1.degree": "本科", "edu.1.start": "2021-09", "edu.1.end": "2025-06", "edu.1.gpa": "3.6/4.0",
    "work.0.company": "示例科技", "work.0.title": "前端开发实习生", "work.0.start": "2023/08",
    "work.0.end": "2023/09", "work.0.desc": "用 Vue 开发天气组件。",
    # 必须保持为空的字段：敏感、紧急联系人、开放式问题、证件类型
    "id_type": "", "id_number": "", "emergency": "", "why_us": "", "__submitted": False,
}


MOKA_EXPECTED = {
    "name": "张三",
    "birth": "2000-05-01",  # 只读日期面板：跨 26 年翻页再点日
    "hometown": "江苏省/苏州市",  # 级联：「江苏苏州」拆成两级
    "exp_city": "上海市/上海市",  # 级联：直辖市两级同名
    "edu.0.school": "示例大学",  # 远程搜索下拉，且不能选成「示例大学附属学院」
    "edu.0.degree": "硕士", "edu.0.major": "高性能计算与数据科学", "edu.0.period": "2025-09~2026-12",
    "edu.1.school": "示例理工大学", "edu.1.degree": "本科", "edu.1.major": "计算机科学",
    "edu.1.period": "2021-09~2025-06",  # 范围选择器：左右两块月份面板
    "work.0.company": "示例科技",
    "work.0.start": "2023-08",  # Element 风格中文月份面板
    "work.0.end": "2023-09",  # 可输入 picker：输入后必须回车才生效
    "__submitted": False,
}


@pytest.mark.parametrize("name,expected", [
    ("feishu_style", FEISHU_EXPECTED), ("beisen_style", BEISEN_EXPECTED), ("moka_style", MOKA_EXPECTED),
])
def test_demo_form(page, profile, name, expected, tmp_path):
    page.goto(demo_url(name))
    report = run(page, profile, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    wrong = {k: (got.get(k), v) for k, v in expected.items() if got.get(k) != v}
    accuracy = 1 - len(wrong) / len(expected)
    print(f"\n{name}: 字段正确率 {accuracy:.1%}，summary={report['summary']}")
    assert not wrong, f"不一致（实际, 期望）：{wrong}"
    assert not any(k not in expected and v not in ("", False) for k, v in got.items()), "出现了多余的非空字段"


def test_never_clicks_submit_even_when_rerun(page, profile, tmp_path):
    page.goto(demo_url("beisen_style"))
    run(page, profile, report_dir=tmp_path)
    run(page, profile, report_dir=tmp_path)  # 多页表单场景：同一页重复跑
    assert page.evaluate("() => window.__submitted") is False


def test_rerun_does_not_overwrite_user_edits(page, profile, tmp_path):
    page.goto(demo_url("beisen_style"))
    run(page, profile, report_dir=tmp_path)
    page.fill('[data-key="name"]', "我手动改的")
    report = run(page, profile, report_dir=tmp_path)
    assert page.input_value('[data-key="name"]') == "我手动改的"
    assert report["summary"]["kept"] > 0


def test_sensitive_field_marked(page, profile, tmp_path):
    page.goto(demo_url("beisen_style"))
    report = run(page, profile, report_dir=tmp_path)
    id_items = [it for it in report["items"] if it["label"] == "证件号码"]
    assert id_items and id_items[0]["status"] == "sensitive"


# 复刻 2026-10-07 小红书实测结构：Tailwind 分区标题、两个日期框共用 label 且 id 都是 startDate、
# 论文与竞赛 id 重复、单选没有 name、多选城市、AutoComplete、无标签的证件类型/区号下拉
XHS_EXPECTED = {
    "exp_city": "上海,杭州", "refer": "", "name": "张三", "id_type": "身份证", "id_number": "",
    "gender": "男", "birth": "2000-05", "area": "中国大陆", "phone": "13900000000", "email": "zhangsan@example.com",
    "edu.0.start": "2025-09", "edu.0.end": "2026-12", "edu.0.degree": "硕士", "edu.0.school": "示例大学",
    "edu.0.city": "上海", "edu.0.college": "信息学院", "edu.0.major": "高性能计算与数据科学",
    "edu.0.rank": "", "edu.0.advisor": "", "edu.0.lab": "", "edu.0.research": "",
    "edu.1.start": "2021-09", "edu.1.end": "2025-06", "edu.1.degree": "本科", "edu.1.school": "示例理工大学",
    "edu.1.city": "苏州", "edu.1.college": "计算机学院", "edu.1.major": "计算机科学",
    "work.0.start": "2023-08", "work.0.end": "2023-09", "work.0.company": "示例科技", "work.0.department": "",
    "work.0.title": "前端开发实习生", "work.0.kind": "实习", "work.0.desc": "用 Vue 开发天气组件。",
    "paste": "Zhang, San. 示例论文. 示例会议 2024.", "paper.0.title": "示例论文", "paper.0.venue": "示例会议 2024",
    "paper.0.order": "第一作者",
    "award.0.name": "", "award.0.rank": "", "award.0.date": "",
    "github": "github.com/zhangsan-demo", "star": "", "cert": "", "adjust": "是，服从调剂", "__submitted": False,
}


def test_xhs_style(page, profile, tmp_path):
    page.goto(demo_url("xhs_style"))
    report = run(page, profile, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    wrong = {k: (got.get(k), v) for k, v in XHS_EXPECTED.items() if got.get(k) != v}
    print(f"\nxhs_style: 字段正确率 {1 - len(wrong) / len(XHS_EXPECTED):.1%}，summary={report['summary']}")
    assert not wrong, f"不一致（实际, 期望）：{wrong}"


def test_unlabeled_fields_never_sent_to_llm(page, profile, tmp_path):
    """实测中模型把无标签的「证件类型」「区号」下拉猜成了证件号和籍贯。"""
    import json

    class Spy:
        sent: list = []

        def complete(self, system, user):
            Spy.sent = json.loads(user)["字段"]
            return "{}"

    page.goto(demo_url("xhs_style"))
    run(page, profile, llm=Spy(), report_dir=tmp_path)
    assert all(f.get("label") for f in Spy.sent)


def test_prefilled_value_that_disagrees_is_flagged(page, profile, tmp_path):
    """页面预填（账号信息、简历解析）和资料不一致时不覆盖，但要标黄提醒。"""
    page.goto(demo_url("xhs_style"))  # 手机号预填 13900000000，资料是 13800000000
    report = run(page, profile, report_dir=tmp_path)
    phone = next(it for it in report["items"] if it["key"] == "basic.phone" and it["kind"] == "text")
    assert phone["status"] == "uncertain" and "13900000000" in phone["note"]
    assert page.input_value('[data-key="phone"]') == "13900000000"


def test_unused_content_is_reported(page, profile, tmp_path):
    """表上没有项目栏时，必须在报告里说出来，而不是静默漏掉。"""
    page.goto(demo_url("xhs_style"))
    report = run(page, profile, report_dir=tmp_path)
    text = "\n".join(report["unused"])
    assert "项目经历 2 段整段没填" in text and "示例 Agent 项目" in text
    assert "自我评价" in text
    assert "GPA" in text  # 教育经历填了，但这张表没有 GPA 栏
    assert "证件号码" not in text  # 敏感字段不算遗漏


def test_upload_resume_goes_to_resume_box_only(page, profile, tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 demo")
    page.goto(demo_url("feishu_style"))
    report = run(page, profile.model_copy(update={"resume_file": str(cv)}), report_dir=tmp_path, upload=True)
    assert report["upload"] == "已上传简历：cv.pdf"
    assert page.evaluate("() => document.querySelector('[data-key=resume]').files.length") == 1
    assert page.evaluate("() => window.__submitted") is False


def test_dry_run_does_not_touch_page(page, profile, tmp_path):
    page.goto(demo_url("xhs_style"))
    before = page.evaluate("() => window.__formValues()")
    report = run(page, profile, report_dir=tmp_path, dry_run=True, upload=True)
    assert page.evaluate("() => window.__formValues()") == before
    assert report["summary"]["planned"] > 0 and report["upload"] is None


def test_upload_then_fix_parser_mistakes(page, profile, tmp_path):
    """小红书实测：上传简历后站点解析把专业写成「数据」、把项目塞进实习描述。本次上传过简历时，以资料为准改回来。"""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 demo")
    page.goto(demo_url("feishu_style"))
    report = run(page, profile.model_copy(update={"resume_file": str(cv)}), report_dir=tmp_path, upload=True)
    got = page.evaluate("() => window.__formValues()")
    assert got["name"] == "张三" and got["edu.0.major"] == "高性能计算与数据科学"
    name = next(it for it in report["items"] if it["key"] == "basic.name")
    assert "解析出错的名字" in name["note"]


def test_present_toggle_is_unchecked_when_profile_has_end_date(page, profile, tmp_path):
    """小红书实测：简历解析把实习的「至今」勾上，结束框被禁用。资料里有结束时间时取消勾选再填。"""
    page.goto(demo_url("xhs_style"))
    page.click("[data-add-for=work-list]")
    page.evaluate("""() => { const b = document.querySelector('#work-list .present-toggle');
                             b.checked = true; b.dispatchEvent(new Event('change', {bubbles: true})); }""")
    report = run(page, profile, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    assert got["work.0.end"] == "2023-09"
    assert page.evaluate("() => document.querySelector('#work-list .present-toggle').checked") is False
    end = next(it for it in report["items"] if it["key"] == "work.end")
    assert "至今" in end["note"]
    # 教育经历里没勾「至今」的不受影响
    assert page.evaluate("() => [...document.querySelectorAll('#edu-list .present-toggle')].some(b => b.checked)") is False


def test_referral_code_filled_from_context(page, profile, tmp_path):
    page.goto(demo_url("xhs_style"))
    run(page, profile, report_dir=tmp_path, ctx={"referral": "DS8x6aqH"})
    assert page.evaluate("() => window.__formValues().refer") == "DS8x6aqH"


# 按 2026-10-07 讯飞（北森 phoenix）与 OPPO（Element Plus）真实诊断复刻
BEISEN_OPPO_EXPECTED = {
    "birth": "2000-05-01",  # 下拉外观、点开是日历，日历顶部有输入框
    "hometown": "江苏省/苏州市",  # 地区选择器，省 → 市
    "city": "上海市",  # 虚拟列表只渲染 6 项，要先搜索
    "edu_start": "2025-09", "edu_end": "2026-12",  # Element Plus：共用 label、按 input 不打开、aria-controls
    "degree": "硕士研究生",  # 页面顶部有「硕士」导航项，不能点到它
    "school": "示例大学", "work_start": "2023-08", "work_end": "2023-09", "company": "示例科技",
    "__submitted": False,
}


def test_beisen_oppo_replica(page, profile, tmp_path):
    page.goto(demo_url("beisen_oppo_style"))
    report = run(page, profile, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    wrong = {k: (got.get(k), v) for k, v in BEISEN_OPPO_EXPECTED.items() if got.get(k) != v}
    print(f"\nbeisen_oppo_style: 字段正确率 {1 - len(wrong) / len(BEISEN_OPPO_EXPECTED):.1%}，summary={report['summary']}")
    assert not wrong, wrong


def test_beisen_oppo_strict_needs_trusted_clicks(page, profile, tmp_path):
    """严格模式：Element Plus 部分只认真实点击（OPPO 实测）。驱动用 page.mouse 真实点击，必须全部填上。"""
    page.goto(demo_url("beisen_oppo_style") + "?strict=1")
    run(page, profile, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    for k in ("edu_start", "edu_end", "degree"):
        assert got[k] == BEISEN_OPPO_EXPECTED[k], (k, got[k])


def test_strict_demo_really_ignores_synthetic_clicks(page):
    """确认严格模式真的会拒绝模拟点击，否则上面那个测试证明不了什么。"""
    from autofill.pipeline import page_script
    page.goto(demo_url("beisen_oppo_style") + "?strict=1")
    page.evaluate(page_script())
    page.evaluate("() => window.__autofill.extract()")
    fid = page.get_attribute("[data-key=degree]", "data-af-id")
    res = page.evaluate("([id]) => window.__autofill.fill(id, 'custom_select', ['硕士研究生'], {})", [fid])
    assert res["ok"] is False  # 不经过驱动、只有模拟点击：打不开
