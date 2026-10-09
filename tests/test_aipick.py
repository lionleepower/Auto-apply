"""选项对不上时让 AI 挑最接近的一项：只发这一条资料值、白名单、结果标黄、没依据不猜。"""

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from autofill import server
from autofill.aipick import ALLOWED_KEYS, pick_option
from autofill.pipeline import run

from .conftest import ROOT, demo_url
from .test_server import ORIGIN, run_flow

INTENT = "Agent 开发 / AI 应用开发"
OPTIONS = ["技术", "产品", "政企", "销售", "综合"]


class FakeLLM:
    def __init__(self, reply='{"choice": "技术"}', boom=False):
        self.reply, self.boom, self.calls = reply, boom, []

    def complete(self, system, user, json_mode=True):
        self.calls.append(user)
        if self.boom:
            raise RuntimeError("network down")
        return self.reply


# ---------- 单元：边界 ----------

def test_picks_an_option_from_the_list():
    llm = FakeLLM()
    r = pick_option(llm, "目标职位类别", "basic.job_intention", INTENT, OPTIONS)
    assert r["choice"] == "技术"
    sent = json.loads(llm.calls[0])
    assert sent == {"题目": "目标职位类别", "资料值": INTENT, "选项": OPTIONS}  # 只有这三样


@pytest.mark.parametrize("key", ["basic.name", "basic.phone", "basic.email", "basic.id_number", "basic.birth_date",
                                 "basic.github", "education.school", None])
def test_sensitive_or_unlisted_keys_are_never_sent(key):
    llm = FakeLLM()
    r = pick_option(llm, "x", key, "13800000000", OPTIONS)
    assert r["choice"] is None and llm.calls == []  # 根本没有调用模型
    assert not ({"basic.name", "basic.phone", "basic.email", "basic.id_number", "basic.birth_date"} & ALLOWED_KEYS)


def test_no_value_means_no_guess_and_no_call():
    llm = FakeLLM()
    assert pick_option(llm, "每周使用 AI 工具的频次", "basic.job_intention", None, OPTIONS)["choice"] is None
    assert llm.calls == []


@pytest.mark.parametrize("llm", [
    FakeLLM('{"choice": "运营"}'),          # 编造了不在选项里的
    FakeLLM('{"choice": null}'),             # 没有相近的
    FakeLLM("我觉得是技术"),                  # 坏 JSON
    FakeLLM(boom=True),                      # 网络错误
])
def test_invented_or_failed_answers_become_no_choice(llm):
    assert pick_option(llm, "目标职位类别", "basic.job_intention", INTENT, OPTIONS)["choice"] is None


def test_huge_option_lists_are_not_sent():
    llm = FakeLLM()
    assert pick_option(llm, "城市", "basic.expected_city", "苏州", [f"城市{i}" for i in range(500)])["choice"] is None
    assert llm.calls == []


# ---------- 命令行 / Playwright 路径 ----------

def test_select_filled_by_ai_is_marked_yellow(page, profile, tmp_path):
    p = profile.model_copy(deep=True)
    p.basic.job_intention = INTENT
    llm = FakeLLM()
    page.goto(demo_url("ai_pick_style"))
    report = run(page, p, llm=llm, report_dir=tmp_path)
    got = page.evaluate("() => window.__formValues()")
    item = next(i for i in report["items"] if i["label"].startswith("目标职位类别"))
    assert got["category"] == "技术"
    assert item["status"] == "uncertain" and "AI" in item["note"], item
    for secret in (p.basic.phone, p.basic.email, p.basic.name, p.basic.id_number):
        assert all(secret not in c for c in llm.calls)  # 整个流程里模型都没看到这些


def test_without_ai_nothing_is_guessed(page, profile, tmp_path):
    p = profile.model_copy(deep=True)
    p.basic.job_intention = INTENT
    page.goto(demo_url("ai_pick_style"))
    report = run(page, p, report_dir=tmp_path)
    assert page.evaluate("() => window.__formValues()")["category"] == ""
    assert next(i for i in report["items"] if i["label"].startswith("目标职位类别"))["status"] == "unfilled"


def test_questions_about_the_user_without_data_are_left_alone(page, profile, tmp_path):
    p = profile.model_copy(deep=True)
    p.basic.job_intention = INTENT
    llm = FakeLLM('{"choice": "每天"}')
    page.goto(demo_url("ai_pick_style"))
    run(page, p, llm=llm, report_dir=tmp_path)
    assert page.evaluate("() => window.__formValues()")["ai_freq"] == ""  # 没有对应资料：不编


# ---------- 插件流程 + 本地服务 ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    prof = yaml.safe_load((ROOT / "profile.example.yaml").read_text(encoding="utf-8"))
    prof["basic"]["job_intention"] = INTENT
    (tmp_path / "p.yaml").write_text(yaml.safe_dump(prof, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("AUTOFILL_PROFILE", str(tmp_path / "p.yaml"))
    monkeypatch.setenv("AUTOFILL_RUNS", str(tmp_path / "runs"))
    monkeypatch.delenv("AUTOFILL_TARGETS", raising=False)
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def test_plugin_flow_uses_ai_pick_and_respects_the_switch(page, browser, client, monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(server, "build_client", lambda name="auto": llm if name != "none" else None)
    report = run_flow(page, client, demo_url("ai_pick_style"), llm="auto")
    got = page.evaluate("() => window.__formValues()")
    assert got["category"] == "技术" and got["ai_freq"] == ""
    item = next(i for i in report["items"] if i["label"].startswith("目标职位类别"))
    assert item["status"] == "uncertain" and "AI" in item["note"]
    picks = lambda: [c for c in llm.calls if "资料值" in c]  # 只数「选项挑选」的调用；字段分类那条不带资料值
    n = len(picks())
    assert n == 1 and INTENT in picks()[0]
    # 侧边栏的开关关掉后不再问 AI
    page2 = browser.new_page()
    report2 = run_flow(page2, client, demo_url("ai_pick_style"), llm="auto", aiPick=False)
    assert page2.evaluate("() => window.__formValues()")["category"] == "" and len(picks()) == n
    page2.close()
    assert next(i for i in report2["items"] if i["label"].startswith("目标职位类别"))["status"] == "unfilled"


def test_pick_option_endpoint_refuses_unknown_sessions_and_sensitive_items(client, monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(server, "build_client", lambda name="auto": llm)
    r = client.post("/api/pick-option", json={"session": "nope", "id": "f1", "options": OPTIONS}, headers=ORIGIN)
    assert r.status_code == 404 and llm.calls == []
