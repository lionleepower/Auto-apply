import pytest

from autofill.rules import IGNORE, candidates_for, classify_section, format_date, match_label


@pytest.mark.parametrize("text,expected", [
    ("基本信息", "basic"), ("个人信息", "basic"), ("教育经历", "education"), ("教育背景", "education"),
    ("实习经历", "work"), ("工作/实习经历", "work"), ("项目经历", "project"), ("科研经历", "research"),
    ("求职意向", "basic"), ("", "basic"),
])
def test_classify_section(text, expected):
    assert classify_section(text) == expected


@pytest.mark.parametrize("label,section,key", [
    ("姓名", "basic", "basic.name"),
    ("英文姓名", "basic", "basic.name_en"),
    ("手机号码", "basic", "basic.phone"),
    ("紧急联系人电话", "basic", IGNORE),  # 不能把自己的手机号填进紧急联系人
    ("证件号码", "basic", "basic.id_number"),
    ("学校名称", "education", "education.school"),
    ("毕业院校", "education", "education.school"),
    ("是否海外院校", "education", "education.is_overseas"),
    ("院校所在国家", "education", "education.country"),
    ("毕业院校", "basic", "education.school"),
    ("毕业时间", "education", "education.end"),
    ("专业名称", "education", "education.major"),
    ("开始时间", "work", "work.start"),
    ("开始时间", "project", "project.start"),
    ("工作描述", "work", "work.description"),
    ("期望工作城市", "basic", "basic.expected_city"),
    ("你为什么选择我们公司？", "basic", None),
])
def test_match_label(label, section, key):
    assert match_label(label, section) == key


@pytest.mark.parametrize("value,kind,ph,out", [
    ("2025-09", "month", "", "2025-09"),
    ("2025-09", "date", "", "2025-09-01"),
    ("2025-09", "text", "YYYY.MM", "2025.09"),
    ("2025-09", "text", "yyyy/mm", "2025/09"),
    ("2025-09", "text", "如：2021-09", "2025-09"),
    ("2025-09", "text", "请输入，如2021年09月", "2025年09月"),
    ("2000-05-01", "text", "YYYY-MM-DD", "2000-05-01"),
])
def test_format_date(value, kind, ph, out):
    assert format_date(value, kind, ph) == out


def test_candidates():
    assert candidates_for("education.degree", "硕士", "custom_select")[:2] == ["硕士", "硕士研究生"]
    assert candidates_for("education.is_overseas", True, "radio")[0] == "是"
    assert candidates_for("education.is_overseas", False, "radio")[0] == "否"
    assert "雅思" in candidates_for("basic.english", "雅思 6.5", "select")
    assert candidates_for("basic.english", "雅思 6.5", "text") == ["雅思 6.5"]
    assert candidates_for("basic.gender", None, "radio") == []


@pytest.mark.parametrize("value,path", [
    ("江苏苏州", ["江苏", "苏州"]),
    ("江苏省苏州市", ["江苏省", "苏州市"]),
    ("江苏 / 苏州", ["江苏", "苏州"]),
    ("上海", ["上海"]),
    ("上海市", ["上海"]),
    ("内蒙古呼和浩特", ["内蒙古", "呼和浩特"]),
    ("伦敦", ["伦敦"]),
])
def test_split_region(value, path):
    from autofill.rules import split_region
    assert split_region(value) == path


@pytest.mark.parametrize("page_value,cands,key,same", [
    ("138****0000", ["13800000000"], "basic.phone", True),  # 账号预填的脱敏手机号
    ("138****0001", ["13800000000"], "basic.phone", False),
    ("2003-06", ["2003-06-24"], "basic.birth_date", True),  # 页面只到月
    ("2003-01", ["2003-06-24"], "basic.birth_date", False),  # 小红书解析出的错误值
    ("硕士研究生", ["硕士", "硕士研究生"], "education.degree", True),
    ("数据", ["计算机科学"], "education.major", False),
    ("github.com/x", ["github.com/x\nRAG：github.com/x/rag"], "basic.github", False),  # 要补上项目仓库
])
def test_same_value(page_value, cands, key, same):
    from autofill.pipeline import _same_value
    assert _same_value(page_value, cands, key) is same


# 2026-10-07 OPPO / 科大讯飞实测出现的标签
@pytest.mark.parametrize("label,section,key", [
    ("项目职责", "project", "project.description"),  # 之前被当成「项目角色」，填成了「个人项目」
    ("项目角色", "project", "project.role"),
    ("该学历是否为交流学习", "education", "education.is_exchange"),  # 之前被当成「学历」
    ("该学历是否为联合办学", "education", "education.is_joint"),
    ("受教育类型", "education", "education.study_type"),
    ("学历类型", "education", "education.study_type"),
    ("专业类别", "education", "education.major_category"),  # 之前被当成「专业」
    ("校招最高学历毕业时间需在27届", "education", "education.end"),  # 之前被当成「学历」
    ("是否为最高学历", "education", "education.is_highest"),
    ("成果描述", "paper", "paper.citation"),
    ("发布时间", "paper", "paper.date"),
    ("擅长的开发语言1", "basic", "basic.languages"),
])
def test_match_label_real_sites(label, section, key):
    assert match_label(label, section) == key


@pytest.mark.parametrize("value,alts", [
    ("新加坡 / 苏州", ["新加坡", "苏州"]),  # 带空格的斜杠是备选
    ("新加坡或苏州", ["新加坡", "苏州"]),
    ("江苏/苏州", ["江苏/苏州"]),  # 不带空格的是路径，不拆
    ("中国 江苏 南京 浦口区", ["中国 江苏 南京 浦口区"]),
])
def test_split_alternatives(value, alts):
    from autofill.rules import split_alternatives
    assert split_alternatives(value) == alts


def test_city_alone_gets_its_province():
    from autofill.rules import split_region
    assert split_region("苏州") == ["江苏", "苏州"]  # 地区选择器要先选省
    assert split_region("中国 江苏 南京 浦口区") == ["中国", "江苏", "南京", "浦口区"]


def test_present_end_date_becomes_current_month():
    from datetime import datetime

    from autofill.pipeline import _present
    assert _present("至今") == datetime.now().strftime("%Y-%m")
    assert _present("2026-08") == "2026-08"


def test_paper_date_is_a_date_key():
    from autofill.pipeline import _same_value
    assert _same_value("2024-07-01", ["2024-07"], "paper.date")  # 日期框只到日，资料只到月


def test_english_cert_without_score_matches():
    from autofill.pipeline import _same_value
    assert _same_value("雅思", ["雅思 6.5"], "basic.english")  # 分数在另一栏
    assert not _same_value("托福", ["雅思 6.5"], "basic.english")
