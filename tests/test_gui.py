"""桌面界面后端：知识库（抽取 / 合并 / 缺失项）、多厂商 AI 接口（三种协议、故障转移、key 打码）。"""

import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from autofill import gui, knowledge, llm, server, settings
from autofill.profile import Basic, Education, Profile

UI = {"Origin": "http://127.0.0.1:8765"}

EXTRACTED = json.dumps({
    "basic": {"name": "李四", "phone": "13900000000", "email": "li@example.com", "gender": "男",
              "id_number": "110101200001011234", "birth_date": "2000-01-02"},
    "education": [{"school": "某某大学", "major": "软件工程", "degree": "本科", "start": "2019-09", "end": "2023-06"}],
    "experience": [{"kind": "实习", "company": "某公司", "title": "后端", "start": "2022-07", "end": "2022-09"}],
    "skills": ["Python", "Go"], "bogus": 1,
}, ensure_ascii=False)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFILL_PROFILE", str(tmp_path / "profile.yaml"))
    monkeypatch.setenv("AUTOFILL_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AUTOFILL_RESUME_DIR", str(tmp_path / "resume"))
    monkeypatch.setenv("AUTOFILL_RUNS", str(tmp_path / "runs"))
    for env in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


# ---------- 安全 ----------

def test_ui_same_origin_allowed_but_other_web_pages_not(client):
    assert client.get("/api/ui/state", headers=UI).status_code == 200
    assert client.get("/api/ui/state", headers={"Origin": "https://evil.example.com"}).status_code == 403
    assert client.post("/api/ui/ingest", json={"text": "x"}, headers={"Origin": "http://127.0.0.1:9999"}).status_code == 403


def test_ui_page_is_served(client):
    r = client.get("/ui/")
    assert r.status_code == 200 and "网申自动填写助手" in r.text
    assert client.get("/ui/app.js").status_code == 200


# ---------- 缺失项 ----------

def test_missing_reports_required_and_recommended():
    empty = knowledge.missing(Profile())
    assert {"basic.name", "education"} <= {m["key"] for m in empty}
    assert knowledge.completeness(Profile()) == 0

    p = Profile(basic=Basic(name="张三", phone="1", email="a@b.c", gender="男", birth_date="2000-01-01"),
                education=[Education(school="X", major="Y", degree="本科", start="2020-09", end="2024-06")])
    levels = {m["level"] for m in knowledge.missing(p)}
    assert levels == {"recommended"}
    assert 50 < knowledge.completeness(p) < 100


# ---------- 抽取与合并 ----------

def test_parse_extraction_drops_unknown_and_id_number():
    p = knowledge.parse_extraction("```json\n" + EXTRACTED + "\n```")
    assert p.basic.name == "李四" and p.basic.id_number is None
    assert p.skills == "Python、Go"
    assert p.education[0].school == "某某大学"


@pytest.mark.parametrize("bad", ["not json", "[1,2]", ""])
def test_parse_extraction_rejects_garbage(bad):
    with pytest.raises(ValueError):
        knowledge.parse_extraction(bad)


def test_redact_hides_id_numbers():
    assert "110101200001011234" not in knowledge.redact("身份证 110101200001011234 手机 13900000000")
    assert "13900000000" in knowledge.redact("手机 13900000000")


def test_merge_fills_blanks_without_clobbering():
    old = Profile(basic=Basic(name="手改的名字"), education=[Education(school="某某大学", major=None)])
    new = knowledge.parse_extraction(EXTRACTED)
    merged, notes = knowledge.merge(old, new)
    assert merged.basic.name == "手改的名字"  # 不覆盖
    assert merged.basic.phone == "13900000000"
    assert len(merged.education) == 1 and merged.education[0].major == "软件工程"  # 同一段只补空
    assert len(merged.experience) == 1 and notes
    merged2, _ = knowledge.merge(merged, new, overwrite=True)
    assert merged2.basic.name == "李四" and len(merged2.experience) == 1  # 重复整理不会重复添加


def test_quick_extract():
    p = knowledge.extract_quick("姓名：王五 手机 13812345678 邮箱 w@x.com github.com/wangwu 出生：2001年5月")
    assert (p.basic.name, p.basic.phone, p.basic.email) == ("王五", "13812345678", "w@x.com")
    assert p.basic.birth_date == "2001-05-01" and "github.com/wangwu" in p.basic.github


def test_extract_text_docx_and_txt_and_unsupported():
    from docx import Document

    doc = Document()
    doc.add_paragraph("张三 13800000000")
    buf = io.BytesIO(); doc.save(buf)
    assert "13800000000" in knowledge.extract_text("a.docx", buf.getvalue())
    assert "你好" in knowledge.extract_text("a.txt", "你好".encode("gbk"))
    with pytest.raises(ValueError):
        knowledge.extract_text("a.exe", b"x")
    with pytest.raises(ValueError):
        knowledge.extract_text("a.txt", b"   ")


# ---------- 接口：资料 ----------

class FakeLLM:
    last_used = "Fake"

    def __init__(self, reply): self.reply = reply
    def complete(self, system, user, json_mode=True):
        self.seen = user
        return self.reply


def test_ingest_with_ai_saves_profile_and_reports_missing(client, monkeypatch):
    fake = FakeLLM(EXTRACTED)
    monkeypatch.setattr(gui, "build_client", lambda name="auto": fake)
    r = client.post("/api/ui/ingest", json={"text": "李四 身份证 110101200001011234"}, headers=UI)
    assert r.status_code == 200
    body = r.json()
    assert "110101200001011234" not in fake.seen
    assert body["profile"]["basic"]["name"] == "李四" and body["used"] == "Fake"
    assert any(m["key"] == "basic.hometown" for m in body["missing"])
    assert client.get("/api/ui/state", headers=UI).json()["profile"]["basic"]["phone"] == "13900000000"


def test_ingest_without_ai_falls_back_to_quick(client):
    r = client.post("/api/ui/ingest", json={"text": "手机 13912345678"}, headers=UI)
    assert r.status_code == 200 and r.json()["profile"]["basic"]["phone"] == "13912345678"
    assert r.json()["warning"]


def test_ingest_ai_failure_is_reported_not_saved(client, monkeypatch):
    monkeypatch.setattr(gui, "build_client", lambda name="auto": FakeLLM("garbage"))
    r = client.post("/api/ui/ingest", json={"text": "x"}, headers=UI)
    assert r.status_code == 502 and "AI 整理失败" in r.json()["detail"]
    assert not gui.profile_path().exists()


def test_put_profile_roundtrip_keeps_hidden_fields_and_backs_up(client, tmp_path):
    (tmp_path / "profile.yaml").write_text("basic: {name: 旧}\nanswers: {'亲属': '否'}\n", encoding="utf-8")
    r = client.put("/api/ui/profile", json={"profile": {"basic": {"name": "新"}}}, headers=UI)
    assert r.status_code == 200
    saved = knowledge.load(tmp_path / "profile.yaml")
    assert saved.basic.name == "新" and saved.answers == {"亲属": "否"}
    assert (tmp_path / "profile.yaml.bak").exists()


def test_upload_resume_saves_file_and_returns_text(client, tmp_path):
    r = client.post("/api/ui/resume", json={"name": "../我的简历.txt", "b64": base64.b64encode("张三 简历".encode()).decode()},
                    headers=UI)
    assert r.status_code == 200 and r.json()["text"] == "张三 简历"
    assert (tmp_path / "resume" / "default" / "我的简历.txt").exists()  # 路径穿越被去掉
    assert client.get("/api/ui/state", headers=UI).json()["resume"] == "我的简历.txt"
    assert client.post("/api/ui/resume", json={"name": "a.exe", "b64": "AA=="}, headers=UI).status_code == 400


# ---------- 接口：AI 厂商 ----------

def test_llm_settings_mask_key_and_keep_it_when_blank(client, tmp_path):
    body = {"providers": [{"preset": "deepseek", "api_key": "sk-secret-1234567890"}]}
    r = client.put("/api/ui/llm", json=body, headers=UI).json()
    p = r["providers"][0]
    assert "secret" not in json.dumps(r) and p["key_mask"].endswith("7890") and p["usable"]
    again = client.put("/api/ui/llm", json={"providers": [{"id": p["id"], "preset": "deepseek", "model": "deepseek-reasoner"}]},
                       headers=UI).json()["providers"][0]
    assert again["model"] == "deepseek-reasoner" and again["key_mask"].endswith("7890")  # 留空沿用旧 key
    assert json.loads((tmp_path / "settings.json").read_text())["providers"][0]["api_key"] == "sk-secret-1234567890"


def test_env_keys_still_work_as_providers(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert client.get("/api/health", headers=UI).json()["llm"] == ["auto"]
    assert llm.build_client("deepseek").clients[0].cfg["api_key"] == "sk-env"
    assert llm.build_client("none") is None


class Resp:
    def __init__(self, status, data): self.status_code, self._d, self.text = status, data, str(data)
    def json(self): return self._d


@pytest.mark.parametrize("preset,url_part,ok_body", [
    ("openai", "/chat/completions", {"choices": [{"message": {"content": "pong"}}]}),
    ("anthropic", "/v1/messages", {"content": [{"type": "text", "text": "pong"}]}),
    ("gemini", ":generateContent", {"candidates": [{"content": {"parts": [{"text": "pong"}]}}]}),
])
def test_three_protocols_parse_replies(monkeypatch, preset, url_part, ok_body):
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        return Resp(200, ok_body)

    monkeypatch.setattr(httpx, "post", post)
    cfg = {**settings.PRESETS[preset], "preset": preset, "api_key": "k"}
    assert llm.ProviderClient(cfg).complete("sys", "hi") == "pong"
    assert url_part in seen["url"]
    auth = json.dumps(seen["headers"])
    assert "k" in auth


def test_failover_switches_to_next_provider(monkeypatch):
    def post(url, **kw):
        if "bad.example" in url:
            return Resp(429, {"error": {"message": "rate limited"}})
        return Resp(200, {"choices": [{"message": {"content": "from-b"}}]})

    monkeypatch.setattr(httpx, "post", post)
    a = {"preset": "custom", "protocol": "openai", "base_url": "https://bad.example/v1", "model": "m", "api_key": "k"}
    b = {"preset": "openai", "protocol": "openai", "base_url": "https://good.example/v1", "model": "m", "api_key": "k"}
    fo = llm.FailoverClient([llm.ProviderClient(a), llm.ProviderClient(b)])
    assert fo.complete("s", "u") == "from-b" and fo.last_used == "OpenAI" and "rate limited" in fo.errors[0]
    only_bad = llm.FailoverClient([llm.ProviderClient(a)])
    with pytest.raises(llm.LLMError):
        only_bad.complete("s", "u")


def test_connection_test_reports_errors(client, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: Resp(401, {"error": {"message": "invalid api key"}}))
    r = client.post("/api/ui/llm/test", json={"preset": "deepseek", "api_key": "bad"}, headers=UI).json()
    assert not r["ok"] and "invalid api key" in r["error"]
    assert client.post("/api/ui/llm/test", json={"preset": "deepseek"}, headers=UI).json()["error"] == "还没填 API Key"
    monkeypatch.setattr(httpx, "post", lambda url, **kw: Resp(200, {"choices": [{"message": {"content": "ok"}}]}))
    assert client.post("/api/ui/llm/test", json={"preset": "ollama"}, headers=UI).json()["ok"]  # 本地模型无需 key
