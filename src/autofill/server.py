"""浏览器插件的本地后端：插件负责在页面里抽取和填写，这里负责「填什么」（规则 / LLM / 资料）。

只监听 127.0.0.1，并且只接受浏览器插件发来的请求：普通网页即使知道端口，也读不到你的资料。
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .batch import load_targets, record, referral_from_url, target_for_url
from . import settings as app_settings
from .gui import UI_DIR, router as ui_router
from .llm import build_client
from .pipeline import (Item, _wanted_section, annotate, apply_result, build_report, next_add_click, page_script,
                       pick_resume_field, prepare, section_counts)
from .profile import load_profile

ROOT = Path(__file__).resolve().parents[2]
PORT = 8765
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
EXTENSION_ORIGINS = ("chrome-extension://", "extension://")
UI_ORIGINS = tuple(f"http://{h}:{PORT}" for h in ("127.0.0.1", "localhost"))  # 桌面界面自己（同源）
JS_SRC = Path(__file__).parent / "js" / "autofill.js"
EXT_DIR = ROOT / "extension"

app = FastAPI(title="autofill local")
app.include_router(ui_router)
SESSIONS: dict[str, dict] = {}
SESSION_TTL_S = 3 * 3600  # 多个页面同时填时，每页有十几轮会话，按时间而不是按个数清理
_LOCK = threading.Lock()  # 同步接口跑在线程池里，多页并行时会同时进来


def _profile_path() -> Path:
    return Path(os.environ.get("AUTOFILL_PROFILE") or ROOT / "profile.yaml")


def _runs_dir() -> Path:
    return Path(os.environ.get("AUTOFILL_RUNS") or ROOT / "runs")


def _targets():
    path = os.environ.get("AUTOFILL_TARGETS")
    return load_targets(path) if path and Path(path).exists() else []


@app.middleware("http")
async def only_from_extension(request: Request, call_next):
    # Host 校验挡 DNS rebinding；Origin 校验挡普通网页（网页发的跨域请求一定带 Origin，且伪造不了）
    host = request.headers.get("host", "").rsplit(":", 1)[0]
    origin = request.headers.get("origin")
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    if origin and not (origin.startswith(EXTENSION_ORIGINS) or origin in UI_ORIGINS):
        return JSONResponse({"detail": "forbidden origin"}, status_code=403)
    if request.method == "OPTIONS":
        resp = Response(status_code=204)
    else:
        resp = await call_next(request)
    if origin and origin.startswith(EXTENSION_ORIGINS):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "content-type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def _llm_label() -> str:
    names = [app_settings.public_view(p)["label"] for p in app_settings.load_providers() if app_settings.usable(p)]
    if not names:
        return "LLM（还没配置 AI 接口）"
    return names[0] + (f" 等 {len(names)} 家（自动切换）" if len(names) > 1 else "")


def _completeness() -> int:
    from . import knowledge

    return knowledge.completeness(knowledge.load(_profile_path()))


@app.get("/api/health")
def health():
    from . import knowledge

    doc = knowledge.load_doc(_profile_path())  # 首次使用还没有 profile.yaml 也要能连上
    profile = knowledge.flatten(doc)
    resume = Path(profile.resume_file) if profile.resume_file else None
    return {
        "ok": True,
        "name": profile.basic.name,
        "resumes": [{"id": v.id, "name": v.name, "locale": v.locale,
                     "file": Path(v.resume_file).name if v.resume_file and Path(v.resume_file).exists() else None}
                    for v in doc.resumes],
        "default_resume": profile.active_resume,
        "resume": resume.name if resume and resume.exists() else None,
        "llm": ["auto"] if build_client("auto") else [],
        "llm_label": _llm_label(),
        "complete": _completeness(),
        "targets": Path(os.environ["AUTOFILL_TARGETS"]).name if os.environ.get("AUTOFILL_TARGETS") else None,
    }


@app.get("/api/context")
def context(url: str):
    t = target_for_url(_targets(), url)
    return {
        "referral": referral_from_url(url) or (t.code if t else ""),
        "company": t.company if t else "",
        "ats": t.ats if t else "",
        "deadline": t.deadline if t else "",
    }


class ExpandIn(BaseModel):
    data: dict
    stuck: list[str] = Field(default_factory=list)
    last: dict | None = None  # 上一次点的 {sec, before}
    resume: str | None = None


@app.post("/api/expand")
def expand(body: ExpandIn):
    profile = load_profile(_profile_path(), body.resume)
    stuck = set(body.stuck)
    if body.last:  # 上次点了「添加」但段落数没变：别死循环
        if section_counts(annotate(body.data["fields"])).get(body.last["sec"], 0) <= body.last["before"]:
            stuck.add(body.last["sec"])
    nxt = next_add_click(body.data, profile, stuck)
    click = {"id": nxt[0]["id"], "sec": nxt[1], "before": nxt[2], "text": nxt[0]["text"]} if nxt else None
    return {"click": click, "stuck": sorted(stuck)}


class FieldsIn(BaseModel):
    fields: list[dict]


@app.post("/api/resume-target")
def resume_target(body: FieldsIn):
    f = pick_resume_field(body.fields)
    return {"id": f["id"] if f else None}


class ResumeFileIn(BaseModel):
    resume: str | None = None


@app.get("/api/resume")
def resume(resume: str | None = None):
    return _resume_file(resume)


@app.post("/api/resume")
def resume_post(body: ResumeFileIn):
    return _resume_file(body.resume)


def _resume_file(rid: str | None):
    profile = load_profile(_profile_path(), rid)
    path = Path(profile.resume_file or "")
    if not profile.resume_file or not path.exists():
        raise HTTPException(404, "profile.yaml 里的 resume_file 没设置或文件不存在")
    return {"name": path.name, "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "b64": base64.b64encode(path.read_bytes()).decode()}


class PlanOptions(BaseModel):
    overwrite: bool = False
    dry_run: bool = False
    llm: str = "none"
    referral: str = ""
    resume_uploaded: bool = False
    resume: str | None = None  # 用哪一份简历（变体 id），缺省为默认简历


class PlanIn(BaseModel):
    url: str
    fields: list[dict]
    round: int = 0  # 第几轮（同一个字段 id 在不同轮里各算一次）
    options: PlanOptions = Field(default_factory=PlanOptions)


@app.post("/api/plan")
def plan_fill(body: PlanIn):
    profile = load_profile(_profile_path(), body.options.resume)
    o = body.options
    llm = build_client(o.llm)
    fields = annotate(body.fields)
    ctx = {"referral": o.referral}
    items, llm_error = prepare(fields, profile, llm, ctx, overwrite=o.overwrite,
                               resume_uploaded=o.resume_uploaded, dry_run=o.dry_run)
    sid = uuid.uuid4().hex
    now = time.time()
    with _LOCK:
        SESSIONS[sid] = {"items": items, "t0": now, "llm_error": llm_error, "url": body.url,
                         "dry_run": o.dry_run, "ctx": ctx, "round": body.round, "resume": o.resume}
        for old in [k for k, v in SESSIONS.items() if now - v["t0"] > SESSION_TTL_S]:
            SESSIONS.pop(old, None)
    return {"session": sid, "ids": [it.id for it in items],
            "fill": [{"id": it.id, "kind": it.kind, "label": it.label, "candidates": it.candidates,
                      "meta": it.meta} for it in items if it.action == "fill"]}


class NextSectionIn(BaseModel):
    expanders: list[dict]
    opened: list[str] = Field(default_factory=list)
    resume: str | None = None


@app.post("/api/next-section")
def next_section(body: NextSectionIn):
    """分区编辑式表单（OPPO）：下一个该打开的分区。资料里没有内容的分区不打开。"""
    profile = load_profile(_profile_path(), body.resume)
    nxt = next((e for e in body.expanders
                if e["section"] not in body.opened and _wanted_section(e["section"], profile)), None)
    return {"id": nxt["id"], "section": nxt["section"]} if nxt else {"id": None}


class FinishIn(BaseModel):
    session: str | None = None
    sessions: list[str] = Field(default_factory=list)  # 多轮填写（分区编辑式表单）
    results: dict[str, dict] = Field(default_factory=dict)
    upload_note: str | None = None
    sections: list[dict] = Field(default_factory=list)


@app.post("/api/finish")
def finish(body: FinishIn):
    ids = body.sessions or ([body.session] if body.session else [])
    with _LOCK:
        found = [SESSIONS.pop(i, None) for i in ids]
    if not ids or not all(found):
        raise HTTPException(404, "会话已过期，请重新点「填写本页」")
    merged: dict[str, Item] = {}
    for s in found:
        for it in s["items"]:
            if it.action == "fill":
                apply_result(it, body.results.get(f"{s['round']}:{it.id}") or body.results.get(it.id)
                             or {"ok": False, "reason": "页面没有返回结果"})
            old = merged.get(it.id)
            if not (old and old.status == "filled" and it.status == "kept"):
                merged[it.id] = it
    items = list(merged.values())
    s = found[0]
    s["llm_error"] = next((x["llm_error"] for x in found if x["llm_error"]), None)
    profile = load_profile(_profile_path(), s.get("resume"))
    report = build_report(s["url"], items, profile, t0=s["t0"], llm_error=s["llm_error"], dry_run=s["dry_run"],
                          ctx=s["ctx"], upload_note=body.upload_note, report_dir=None)
    report["sections"] = body.sections
    # 文件名带上站点和随机尾巴：几个页面同一秒填完也不会互相覆盖
    host = re.sub(r"[^\w.-]", "_", urlparse(s["url"]).hostname or "page")
    out = _runs_dir() / f"run-{time.strftime('%Y%m%d-%H%M%S')}-{host}-{uuid.uuid4().hex[:4]}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report["file"] = str(out)
    import json as _json
    out.write_text(_json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not s["dry_run"]:  # 插件里填过的公司也记进批量进度
        t = target_for_url(_targets(), s["url"])
        if t:
            with _LOCK:
                record(_runs_dir() / "applications.csv", t, "filled", report)
    return report


class DiagIn(BaseModel):
    data: dict


@app.post("/api/diag")
def diag(body: DiagIn):
    """插件「导出诊断」：页面结构存到 runs/diag-*.json，给适配新站点用。只存在本地。"""
    from urllib.parse import urlparse
    import json as _json

    host = urlparse(body.data.get("url", "")).netloc.replace(":", "_") or "page"
    out = _runs_dir() / f"diag-{host}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(body.data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"file": str(out), "probes": len(body.data.get("probes", []))}


class RevealIn(BaseModel):
    file: str


@app.post("/api/reveal")
def reveal(body: RevealIn):
    """在资源管理器里选中某个报告 / 诊断文件。只允许 runs 目录里的文件。"""
    import subprocess

    path = Path(body.file).resolve()
    runs = _runs_dir().resolve()
    if runs not in path.parents or not path.exists():
        raise HTTPException(400, "只能打开 runs 目录里的文件")
    subprocess.Popen(["explorer", "/select,", str(path)])
    return {"ok": True}


def sync_extension_js() -> None:
    """插件里的 autofill.js 是 src/autofill/js/autofill.js 的副本，每次启动服务时同步。"""
    EXT_DIR.mkdir(exist_ok=True)
    (EXT_DIR / "autofill.js").write_text(page_script(), encoding="utf-8")


def serve(port: int = PORT) -> None:
    import uvicorn

    sync_extension_js()
    print(f"自动填写服务已启动：http://127.0.0.1:{port}（只接受浏览器插件的请求）。关闭这个窗口即停止。")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


@app.get("/")
def home():
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/ui/")


from fastapi.staticfiles import StaticFiles  # noqa: E402  放最后：静态目录不能抢在 /api 路由前面

app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")
