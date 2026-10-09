"""多份简历、海外（英文）规则、证件号开关、自定义问答。"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from autofill import gui, knowledge, server
from autofill.profile import Basic, Profile, load_profile
from autofill.rules import candidates_for, classify_section, format_date, match_label

UI = {"Origin": "http://127.0.0.1:8765"}

EXTRACTED = json.dumps({
    "basic": {"name": "李四", "email": "li@example.com"},
    "education": [{"school": "某某大学", "major": "软件工程", "degree": "本科", "start": "2019-09", "end": "2023-06"}],
}, ensure_ascii=False)


class FakeLLM:
    last_used = "Fake"

    def __init__(self, reply): self.reply = reply
    def complete(self, system, user, json_mode=True): return self.reply


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFILL_PROFILE", str(tmp_path / "profile.yaml"))
    monkeypatch.setenv("AUTOFILL_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AUTOFILL_RESUME_DIR", str(tmp_path / "resume"))
    monkeypatch.setenv("AUTOFILL_RUNS", str(tmp_path / "runs"))
    for env in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def _create(client, **kw):
    r = client.post("/api/ui/resumes", json=kw, headers=UI)
    assert r.status_code == 200, r.text
    return r.json()


def test_legacy_flat_profile_migrates_to_default_resume(client, tmp_path):
    (tmp_path / "profile.yaml").write_text(
        "basic: {name: 张三}\neducation: [{school: 某大学}]\nskills: Python\nresume_file: /x/a.pdf\n", encoding="utf-8")
    st = client.get("/api/ui/state", headers=UI).json()
    assert [r["id"] for r in st["resumes"]] == ["default"] and st["profile"]["education"][0]["school"] == "某大学"
    p = load_profile(tmp_path / "profile.yaml")  # 填表流程拿到的仍是平铺视图
    assert p.education[0].school == "某大学" and p.skills == "Python" and p.resume_file == "/x/a.pdf"


def test_variants_share_basic_but_not_sections(client, tmp_path):
    client.put("/api/ui/profile", json={"profile": {"basic": {"name": "张三", "name_en": "San Zhang"},
                                                     "education": [{"school": "中文大学"}]}}, headers=UI)
    st = _create(client, name="Overseas", locale="overseas")
    rid = st["current"]
    assert st["locale"] == "overseas" and st["profile"]["basic"]["name"] == "张三"  # 基本信息共用
    assert st["profile"]["education"] == []  # 其余各自独立
    client.put("/api/ui/profile", json={"resume": rid, "profile": {
        **st["profile"], "education": [{"school": "MIT"}], "basic": {**st["profile"]["basic"], "linkedin": "in/x"}}},
        headers=UI)
    assert load_profile(tmp_path / "profile.yaml", rid).education[0].school == "MIT"
    cn = load_profile(tmp_path / "profile.yaml")  # 默认仍是第一份
    assert cn.education[0].school == "中文大学" and cn.basic.linkedin == "in/x"
    # 英文姓名拆出 first / last（只在填表视图里推导，不写回文件）
    assert (cn.basic.first_name, cn.basic.last_name) == ("San", "Zhang")
    assert "first_name" not in (tmp_path / "profile.yaml").read_text(encoding="utf-8")


def test_copy_default_rename_and_delete(client):
    first = client.get("/api/ui/state", headers=UI).json()["current"]
    client.put("/api/ui/profile", json={"profile": {"education": [{"school": "A大学"}]}}, headers=UI)
    cp = _create(client, name="副本", copy_from=first)
    assert cp["profile"]["education"][0]["school"] == "A大学" and cp["resume"] is None
    st = client.patch(f"/api/ui/resumes/{cp['current']}", json={"name": "改名", "make_default": True}, headers=UI).json()
    assert st["default"] == cp["current"] and [r["name"] for r in st["resumes"]] == ["默认简历", "改名"]
    assert client.patch(f"/api/ui/resumes/{first}", json={"locale": "mars"}, headers=UI).status_code == 400
    st = client.delete(f"/api/ui/resumes/{cp['current']}", headers=UI).json()
    assert [r["id"] for r in st["resumes"]] == [first] and st["default"] == first  # 默认简历被删后自动换
    assert client.delete(f"/api/ui/resumes/{first}", headers=UI).status_code == 400  # 至少留一份
    assert client.get("/api/ui/state?resume=nope", headers=UI).status_code == 404


def test_resume_files_and_notes_are_per_variant(client, tmp_path):
    rid = _create(client, name="B")["current"]
    b64 = base64.b64encode("英文简历".encode()).decode()
    client.post("/api/ui/resume", json={"name": "cv.txt", "b64": b64, "resume": rid}, headers=UI)
    client.put("/api/ui/notes", json={"text": "B 的笔记", "resume": rid}, headers=UI)
    assert (tmp_path / "resume" / rid / "cv.txt").exists()
    assert client.get(f"/api/ui/state?resume={rid}", headers=UI).json()["notes"] == "B 的笔记"
    assert client.get("/api/ui/state", headers=UI).json()["notes"] == ""  # 默认简历不受影响
    client.delete(f"/api/ui/resumes/{rid}", headers=UI)
    assert not (tmp_path / "resume" / rid).exists()


def test_ingest_goes_into_selected_resume_only(client, monkeypatch):
    monkeypatch.setattr(gui, "build_client", lambda name="auto": FakeLLM(EXTRACTED))
    rid = _create(client, name="B")["current"]
    st = client.post("/api/ui/ingest", json={"text": "x", "resume": rid}, headers=UI).json()
    assert st["profile"]["education"][0]["school"] == "某某大学"
    default = client.get("/api/ui/state", headers=UI).json()
    assert default["profile"]["education"] == [] and default["profile"]["basic"]["name"] == "李四"  # 只有基本信息共用


def test_overseas_checks_differ_from_cn():
    cn = {m["key"] for m in knowledge.missing(Profile())}
    ov = {m["key"] for m in knowledge.missing(Profile(locale="overseas"))}
    assert "basic.birth_date" in cn and "basic.birth_date" not in ov
    assert {"basic.country", "basic.authorized_to_work", "basic.need_sponsorship"} <= ov
    # 只写了 first / last 也算有英文姓名；False 也是有效答案
    p = Profile(locale="overseas", basic=Basic(first_name="San", last_name="Zhang", need_sponsorship=False))
    miss = {m["key"] for m in knowledge.missing(p)}
    assert "basic.name_en" not in miss and "basic.need_sponsorship" not in miss


def test_id_autofill_toggle_and_custom_answers(client, tmp_path):
    assert client.get("/api/ui/state", headers=UI).json()["id_autofill"] is False
    r = client.put("/api/ui/profile", headers=UI, json={
        "profile": {"basic": {"id_number": "110101200001011234"}}, "id_autofill": True,
        "answers": [{"pattern": "亲属|relative", "answer": "否"}, {"pattern": "", "answer": "x"}]}).json()
    assert r["id_autofill"] is True and r["answers"] == [{"pattern": "亲属|relative", "answer": "否"}]
    p = load_profile(tmp_path / "profile.yaml")
    assert "basic.id_number" not in p.never_fill and p.answers == {"亲属|relative": "否"}
    off = client.put("/api/ui/profile", json={"profile": {}, "id_autofill": False}, headers=UI).json()
    assert off["id_autofill"] is False
    bad = client.put("/api/ui/profile", json={"profile": {}, "answers": [{"pattern": "(", "answer": "a"}]}, headers=UI)
    assert bad.status_code == 400


def test_plan_uses_selected_resume(client):
    rid = _create(client, name="O", locale="overseas")["current"]
    client.put("/api/ui/profile", headers=UI, json={"resume": rid, "profile": {
        "basic": {"name_en": "San Zhang", "email": "a@b.co"}, "education": [{"school": "MIT", "degree": "硕士"}]}})
    fields = [
        {"id": "f1", "label": "School", "section": "Education", "section_kind": "education", "kind": "text",
         "placeholder": "", "options": [], "index": 0, "value": ""},
        {"id": "f2", "label": "First name", "section": "Personal Information", "section_kind": "basic",
         "kind": "text", "placeholder": "", "options": [], "index": 0, "value": ""}]
    plan = client.post("/api/plan", headers=UI, json={"url": "https://x.example/apply", "fields": fields,
                                                       "options": {"resume": rid}}).json()
    got = {f["id"]: f["candidates"] for f in plan["fill"]}
    assert got["f1"] == ["MIT"] and got["f2"] == ["San"]
    other = client.post("/api/plan", headers=UI, json={"url": "https://x.example/apply", "fields": fields[:1]}).json()
    assert other["fill"] == []  # 默认简历里没有教育经历


def test_health_lists_resumes(client):
    _create(client, name="O", locale="overseas")
    h = client.get("/api/health", headers=UI).json()
    assert [r["locale"] for r in h["resumes"]] == ["cn", "overseas"] and h["default_resume"] == "default"


# ---------- 英文表单规则 ----------

EN_LABELS = [
    ("basic", "First Name", "basic.first_name"), ("basic", "Last name", "basic.last_name"),
    ("basic", "Full name", "basic.name_en"), ("basic", "Email address", "basic.email"),
    ("basic", "Phone number", "basic.phone"), ("basic", "LinkedIn Profile", "basic.linkedin"),
    ("basic", "Are you legally authorized to work in the United States?", "basic.authorized_to_work"),
    ("basic", "Will you now or in the future require sponsorship?", "basic.need_sponsorship"),
    ("basic", "Zip code", "basic.postal_code"), ("basic", "Country", "basic.country"),
    ("basic", "Start date", "basic.available"), ("basic", "Race", "__ignore__"),
    ("basic", "Veteran status", "__ignore__"), ("basic", "Address Line 2", "__ignore__"),
    ("basic", "Date of birth", "basic.birth_date"),
    ("education", "School", "education.school"), ("education", "Degree", "education.degree"),
    ("education", "Field of study", "education.major"), ("education", "Start date", "education.start"),
    ("education", "Graduation date", "education.end"), ("education", "GPA", "education.gpa"),
    ("work", "Company", "work.company"), ("work", "Job title", "work.title"), ("work", "Start date", "work.start"),
    ("work", "Responsibilities", "work.description"), ("project", "Project name", "project.name"),
]


@pytest.mark.parametrize("section,label,key", EN_LABELS)
def test_english_labels(section, label, key):
    assert match_label(label, section) == key


def test_english_section_titles():
    titles = ("Education", "Work Experience", "Projects", "Publications", "Personal Information")
    assert [classify_section(t) for t in titles] == ["education", "work", "project", "paper", "basic"]


def test_chinese_rules_unchanged_by_english_ones():
    assert match_label("姓名", "basic") == "basic.name" and match_label("学校", "education") == "education.school"
    assert match_label("星级评分", "basic") == "__ignore__"


def test_english_degree_and_us_dates():
    c = candidates_for("education.degree", "硕士", "select")
    assert "Master's Degree" in c and "硕士研究生" in c
    assert candidates_for("education.degree", "Bachelor's", "select")[0] == "Bachelor's"
    assert "本科" in candidates_for("education.degree", "Bachelor of Science", "select")
    assert format_date("2023-09", "text", "MM/YYYY") == "09/2023"
    assert format_date("2023-09-05", "text", "MM/DD/YYYY") == "09/05/2023"
    assert format_date("2023-09", "text", "YYYY-MM") == "2023-09"


def test_long_fixed_answer_is_not_split_into_alternatives(profile):
    from autofill.pipeline import Item, resolve

    profile.answers["协作.*项目"] = "背景：我要投递很多网站，逐个手填太慢。方案：与 AI 协作，我定边界，AI 实现；结果：能跑通。"
    profile.answers["亲属"] = "否、没有、无"
    it = Item(id="f1", label="请描述一个与 AI 协作完成的项目", section="x", section_kind="basic", kind="text", index=0)
    it.key = "answer:协作.*项目"
    resolve(it, {"placeholder": "", "label": it.label, "kind": "text", "id": "f1"}, profile, {})
    assert it.candidates == [profile.answers["协作.*项目"]]
    short = Item(id="f2", label="是否有亲属", section="x", section_kind="basic", kind="radio", index=0)
    short.key = "answer:亲属"
    resolve(short, {"placeholder": "", "label": short.label, "kind": "radio", "id": "f2"}, profile, {})
    assert short.candidates[:3] == ["否", "没有", "无"]


# ---------- 作品集 ----------

def _zip_bytes(files: dict[str, str]) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def test_portfolio_upload_is_per_resume_and_imports_readme(client, tmp_path):
    z = _zip_bytes({"proj/README.md": "# Agent 项目\n工具调用 Agent 与离线评测", "proj/src/main.py": "print(1)",
                    "../evil/README.md": "不该被读"})
    rid = _create(client, name="B")["current"]
    r = client.post("/api/ui/portfolio", headers=UI, json={"name": "我的作品集.zip", "resume": rid,
                                                            "b64": base64.b64encode(z).decode()})
    assert r.status_code == 200
    body = r.json()
    assert "工具调用 Agent" in body["text"] and "print(1)" not in body["text"] and "不该被读" not in body["text"]
    st = client.get(f"/api/ui/state?resume={rid}", headers=UI).json()
    assert st["portfolio"] == "我的作品集.zip"
    assert client.get("/api/ui/state", headers=UI).json()["portfolio"] is None  # 别的简历没有
    assert (tmp_path / "resume" / rid / "portfolio" / "我的作品集.zip").exists()
    h = client.get("/api/health", headers=UI).json()
    assert {x["id"]: x["portfolio"] for x in h["resumes"]}[rid] == "我的作品集.zip"
    # 插件取文件
    f = client.post("/api/portfolio", json={"resume": rid}, headers=UI).json()
    assert f["name"] == "我的作品集.zip" and base64.b64decode(f["b64"]) == z
    assert client.post("/api/portfolio", json={"resume": "default"}, headers=UI).status_code == 404
    # 移除
    st = client.delete(f"/api/ui/portfolio?resume={rid}", headers=UI).json()
    assert st["portfolio"] is None and not (tmp_path / "resume" / rid / "portfolio").exists()


def test_portfolio_rejects_bad_files(client):
    ok = base64.b64encode(b"x").decode()
    assert client.post("/api/ui/portfolio", json={"name": "a.exe", "b64": ok}, headers=UI).status_code == 400
    assert client.post("/api/ui/portfolio", json={"name": "a.zip", "b64": ok}, headers=UI).status_code == 400  # 不是 zip
    big = base64.b64encode(b"0" * (30 * 1024 * 1024 + 1)).decode()
    assert client.post("/api/ui/portfolio", json={"name": "a.pdf", "b64": big}, headers=UI).status_code == 413


def test_zip_with_huge_or_binary_entries_is_safe():
    from autofill import knowledge

    z = _zip_bytes({"README.md": "x" * 300_000, "docs/NOTES.txt": "有用的说明", "a.png": "bin"})
    text = knowledge.extract_zip_text(z)
    assert "有用的说明" in text and "x" * 1000 not in text  # 超过 200KB 的单个文件跳过
