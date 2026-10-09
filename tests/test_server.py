"""插件后端与插件流程。

端到端测试直接跑 extension/flow.js（插件里那份），把 env 换成 Playwright 实现：
exec 在页面里直接调用，api 经 expose_function 转给 FastAPI TestClient，带插件的 Origin。
"""

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from autofill import server

from .conftest import ROOT, demo_url
from .test_demo_forms import XHS_EXPECTED

EXT = ROOT / "extension"
ORIGIN = {"Origin": "chrome-extension://abcdefghijklmnop"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFILL_PROFILE", str(ROOT / "profile.example.yaml"))
    monkeypatch.setenv("AUTOFILL_RUNS", str(tmp_path / "runs"))
    monkeypatch.delenv("AUTOFILL_TARGETS", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


# ---------- 安全：普通网页读不到资料 ----------

def test_web_page_origin_is_rejected(client):
    r = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403
    r = client.get("/api/resume", headers={"Origin": "null"})  # file:// 或沙箱 iframe
    assert r.status_code == 403


def test_dns_rebinding_host_is_rejected(client):
    r = client.get("/api/health", headers={**ORIGIN, "Host": "evil.example.com:8765"})
    assert r.status_code == 403


def test_extension_origin_is_allowed(client):
    r = client.get("/api/health", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["name"] == "张三"
    assert r.headers["access-control-allow-origin"] == ORIGIN["Origin"]


def test_context_finds_referral(client, tmp_path, monkeypatch):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["OPPO", "", "", "科技", "https://careers.oppo.com/university/oppo/campus/post?shareId=1", "REF123456"])
    wb.save(tmp_path / "t.xlsx")
    monkeypatch.setenv("AUTOFILL_TARGETS", str(tmp_path / "t.xlsx"))
    r = client.get("/api/context", params={"url": "https://careers.oppo.com/university/oppo/center/resume?positionId=1"},
                   headers=ORIGIN).json()
    assert (r["company"], r["referral"]) == ("OPPO", "REF123456")
    r = client.get("/api/context", params={"url": "https://x.com/?recommendCode=ABCD1234"}, headers=ORIGIN).json()
    assert r["referral"] == "ABCD1234"


def test_extension_js_is_in_sync():
    """extension/autofill.js 必须和 src 里的一致（autofill serve 启动时会同步）。"""
    from autofill.pipeline import page_script
    assert (EXT / "autofill.js").read_text(encoding="utf-8") == page_script()


def test_newer_script_replaces_old_one_on_page(page):
    """插件更新后，开着的网申页里不能继续跑旧代码（2026-10-07 实测：诊断按钮因此失效）。"""
    from autofill.pipeline import page_script
    page.goto(demo_url("xhs_style"))
    old = page_script().replace("pickOption, diagnose,", "pickOption,")  # 模拟没有 diagnose 的旧版本
    assert old != page_script()
    page.evaluate(old.replace(page_script().split("const VERSION = '")[1].split("'")[0], "oldversion"))
    assert page.evaluate("() => typeof window.__autofill.diagnose") == "undefined"
    page.evaluate(page_script())
    assert page.evaluate("() => typeof window.__autofill.diagnose") == "function"
    page.evaluate("() => window.__autofill.extract()")
    ids = page.evaluate("() => [...document.querySelectorAll('[data-af-id]')].map(e => e.getAttribute('data-af-id'))")
    assert len(ids) == len(set(ids))  # 替换后编号不撞车


def test_manifest_scripts_exist():
    m = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert (EXT / m["background"]["service_worker"]).exists()
    assert (EXT / m["side_panel"]["default_path"]).exists()
    for p in m["icons"].values():
        assert (EXT / p).exists()


# ---------- 端到端：插件流程 + 本地服务 + 页面 ----------

def run_flow(page, client, url, **opts):
    def bridge(path, body):
        r = client.get("/api" + path, headers=ORIGIN) if body is None else \
            client.post("/api" + path, json=body, headers=ORIGIN)
        assert r.status_code == 200, r.text
        return r.json()

    page.goto(url)
    page.expose_function("apiBridge", bridge)
    page.add_script_tag(content=(EXT / "autofill.js").read_text(encoding="utf-8"))
    page.add_script_tag(content=(EXT / "flow.js").read_text(encoding="utf-8"))
    return page.evaluate("""async (opts) => {
        const env = {
            inject: async () => {},
            exec: async (f, args) => f(...args),
            api: (p, b) => window.apiBridge(p, b === undefined ? null : b),
            progress: () => {},
        };
        return AutofillFlow.run(env, opts);
    }""", {"url": url, "parseWaitMs": 800, **opts})


def test_flow_fills_xhs_demo_like_cli(page, client):
    report = run_flow(page, client, demo_url("xhs_style"), referral="DS8x6aqH")
    got = page.evaluate("() => window.__formValues()")
    expected = dict(XHS_EXPECTED, refer="DS8x6aqH")
    wrong = {k: (got.get(k), v) for k, v in expected.items() if got.get(k) != v}
    assert not wrong, wrong
    assert "项目经历 2 段整段没填" in "\n".join(report["unused"])
    assert page.evaluate("() => window.__submitted") is False
    # 页面上有颜色标记
    assert page.evaluate("() => document.querySelectorAll('[data-af-status]').length") > 20


def test_flow_dry_run_does_not_touch_page(page, client):
    page.goto(demo_url("xhs_style"))
    before = page.evaluate("() => window.__formValues()")
    report = run_flow(page, client, demo_url("xhs_style"), dryRun=True)
    assert page.evaluate("() => window.__formValues()") == before
    assert report["summary"]["planned"] > 0


def test_flow_uploads_resume_then_fixes_parser(page, client, tmp_path, monkeypatch):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 demo")
    prof = yaml.safe_load((ROOT / "profile.example.yaml").read_text(encoding="utf-8"))
    prof["resume_file"] = str(cv)
    (tmp_path / "p.yaml").write_text(yaml.safe_dump(prof, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("AUTOFILL_PROFILE", str(tmp_path / "p.yaml"))
    report = run_flow(page, client, demo_url("feishu_style"), upload=True)
    assert report["upload"] == "已上传简历：cv.pdf"
    assert page.evaluate("() => document.querySelector('[data-key=resume]').files[0].name") == "cv.pdf"
    got = page.evaluate("() => window.__formValues()")
    assert got["name"] == "张三" and got["edu.0.major"] == "高性能计算与数据科学"  # 解析写错的改回来了


def test_finish_with_expired_session(client):
    r = client.post("/api/finish", json={"session": "nope", "results": {}}, headers=ORIGIN)
    assert r.status_code == 404


def test_diagnose_records_popups_without_changing_values(page, client, tmp_path):
    from autofill.pipeline import page_script
    page.goto(demo_url("xhs_style"))
    page.evaluate(page_script())
    before = page.evaluate("() => window.__formValues()")
    data = page.evaluate("() => window.__autofill.diagnose(6)")
    assert page.evaluate("() => window.__formValues()") == before  # 只看不填
    gender = next(p for p in data["probes"] if p["label"] == "性别")
    assert any("ant-select-item-option" in h for h in gender["popups"])  # 抓到了弹出来的选项
    r = client.post("/api/diag", json={"data": data}, headers=ORIGIN).json()
    assert Path(r["file"]).exists() and r["probes"] == len(data["probes"])


def test_reveal_only_inside_runs(client, tmp_path):
    r = client.post("/api/reveal", json={"file": str(ROOT / "profile.yaml")}, headers=ORIGIN)
    assert r.status_code == 400


def test_flow_on_beisen_oppo_replica(page, client):
    from .test_demo_forms import BEISEN_OPPO_EXPECTED
    run_flow(page, client, demo_url("beisen_oppo_style"))
    got = page.evaluate("() => window.__formValues()")
    assert {k: got.get(k) for k in BEISEN_OPPO_EXPECTED} == BEISEN_OPPO_EXPECTED
