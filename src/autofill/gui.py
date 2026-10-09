"""桌面界面的后端：知识库（资料 + 多份简历）、AI 接口配置、上手向导，以及把界面开成独立窗口的启动器。"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import knowledge, settings
from .llm import LLMError, build_client, check_provider
from .profile import LOCALES, VARIANT_FIELDS, Profile, ResumeVariant, find_variant, flatten, with_flat

router = APIRouter(prefix="/api/ui")
UI_DIR = Path(__file__).parent / "ui"
ROOT = settings.ROOT
MAX_UPLOAD = 15 * 1024 * 1024
ID_KEY = "basic.id_number"


def profile_path() -> Path:
    return Path(os.environ.get("AUTOFILL_PROFILE") or ROOT / "profile.yaml")


def notes_path(rid: str = "default") -> Path:
    base = profile_path().with_name("profile_notes.txt")
    return base if rid == "default" else base.with_name(f"profile_notes-{rid}.txt")


def resume_dir() -> Path:
    return Path(os.environ.get("AUTOFILL_RESUME_DIR") or ROOT / "resume")


# ---------- 结构化表单的字段描述（界面按它渲染） ----------

def _fields(labels: dict[str, str], **kw) -> list[dict]:
    out = []
    for name, label in labels.items():
        spec = {"key": name, "label": label}
        spec.update(kw.get(name, {}))
        out.append(spec)
    return out


DEGREES = ["专科", "本科", "硕士", "博士"]
YES_NO = {"type": "tribool"}  # 是 / 否 / 未填
BASIC_GROUPS = [
    {"id": "identity", "title": "证件信息", "shared": True, "open": True,
     "hint": "证件号只保存在本机。默认不会被自动填写，想让助手填就打开下面的开关。",
     "fields": _fields({"id_type": "证件类型", "id_number": "证件号码"},
                       id_type={"type": "select", "options": ["身份证", "护照", "港澳台居民证", "其他"]},
                       id_number={"placeholder": "18 位身份证号 / 护照号"})},
    {"id": "basic", "title": "基本信息", "shared": True, "open": True,
     "hint": "所有简历共用。",
     "fields": _fields({
         "name": "姓名", "gender": "性别", "birth_date": "出生日期", "phone": "手机号", "email": "邮箱",
         "political_status": "政治面貌", "ethnicity": "民族", "hometown": "籍贯 / 生源地", "current_city": "现居城市",
         "expected_city": "期望工作城市", "job_intention": "求职意向", "available": "到岗时间", "english": "英语水平",
         "name_en": "英文姓名", "github": "GitHub", "homepage": "个人主页",
         "portfolio": "作品集链接",
     }, gender={"type": "select", "options": ["男", "女"]}, birth_date={"placeholder": "2001-05-20"},
         expected_city={"placeholder": "上海，杭州"}, portfolio={"placeholder": "留空则使用 GitHub 链接"}, available={"placeholder": "随时到岗 / 2026-07"},
         english={"placeholder": "雅思 7.0 / 六级 580"}, name_en={"placeholder": "San Zhang"})},
    {"id": "overseas", "title": "海外申请信息", "shared": True, "open": False,
     "hint": "投英文表单（Greenhouse / Lever / Workday 等）时用到；名和姓留空时会从英文姓名自动拆。",
     "fields": _fields({
         "first_name": "名（First name）", "last_name": "姓（Last name）", "linkedin": "LinkedIn",
         "address": "街道地址", "country": "现居国家 / 地区", "state": "州 / 省", "postal_code": "邮编",
         "nationality": "国籍", "work_authorization": "工作许可状态",
         "authorized_to_work": "是否有当地合法工作资格", "need_sponsorship": "是否需要公司担保签证",
     }, authorized_to_work=YES_NO, need_sponsorship=YES_NO,
         work_authorization={"placeholder": "Need visa / Green Card / Citizen"},
         country={"placeholder": "China / United States"})},
]
SCHEMA = {
    "basic_groups": BASIC_GROUPS,
    "education": _fields({
        "school": "学校", "college": "学院", "major": "专业", "degree": "学历", "start": "入学（YYYY-MM）",
        "end": "毕业（YYYY-MM）", "gpa": "GPA / 成绩", "rank": "排名", "city": "学校所在城市", "advisor": "导师",
        "research_area": "研究方向",
    }, degree={"type": "select", "options": DEGREES}),
    "experience": _fields({
        "kind": "类型", "company": "公司 / 单位", "title": "岗位", "department": "部门", "city": "城市",
        "start": "开始（YYYY-MM）", "end": "结束（YYYY-MM，在职留空）", "description": "工作内容",
    }, kind={"type": "select", "options": ["实习", "工作", "科研"]}, description={"type": "textarea"}),
    "projects": _fields({
        "name": "项目名称", "role": "角色", "start": "开始", "end": "结束", "tech": "技术栈", "url": "链接",
        "description": "项目描述",
    }, description={"type": "textarea"}),
    "papers": _fields({
        "title": "论文题目", "venue": "会议 / 期刊", "year": "年份", "author_order": "作者顺序",
    }),
    "extra": [
        {"key": "skills", "label": "专业技能", "type": "textarea"},
        {"key": "awards", "label": "获奖情况", "type": "textarea"},
        {"key": "self_intro", "label": "自我评价", "type": "textarea"},
    ],
    "locales": LOCALES,
}


def _profile_json(p: Profile) -> dict:
    return p.model_dump(include={"basic", *VARIANT_FIELDS} - {"resume_file", "portfolio_file"})


def _load_doc() -> Profile:
    try:
        return knowledge.load_doc(profile_path())
    except Exception as e:  # profile.yaml 被手改坏了：给出明确提示，不要吞掉
        raise HTTPException(400, f"{profile_path().name} 读取失败：{e}") from e


def _variant_or_404(doc: Profile, rid: str | None) -> ResumeVariant:
    v = find_variant(doc, rid)
    if rid and (not v or v.id != rid):
        raise HTTPException(404, "没有这份简历")
    if not v:
        raise HTTPException(404, "还没有简历")
    return v


def _exists(path: str | None) -> bool:
    return bool(path) and Path(path).exists()


def _state(rid: str | None = None, doc: Profile | None = None) -> dict:
    doc = doc or _load_doc()
    v = _variant_or_404(doc, rid)
    flat = flatten(doc, v.id, derive=False)
    notes = notes_path(v.id).read_text(encoding="utf-8") if notes_path(v.id).exists() else ""
    return {
        "profile": _profile_json(flat),
        "answers": [{"pattern": k, "answer": a} for k, a in flat.answers.items()],
        "id_autofill": ID_KEY not in flat.never_fill,
        "resumes": [{"id": r.id, "name": r.name, "locale": r.locale,
                     "resume": Path(r.resume_file).name if _exists(r.resume_file) else None,
                     "portfolio": Path(r.portfolio_file).name if _exists(r.portfolio_file) else None,
                     "counts": {"education": len(r.education), "experience": len(r.experience),
                                "projects": len(r.projects), "papers": len(r.papers)}} for r in doc.resumes],
        "current": v.id, "default": find_variant(doc).id, "locale": v.locale,
        "missing": knowledge.missing(flat), "completeness": knowledge.completeness(flat),
        "notes": notes, "resume": Path(v.resume_file).name if _exists(v.resume_file) else None,
        "portfolio": Path(v.portfolio_file).name if _exists(v.portfolio_file) else None,
        "llm": _llm_view(), "ai_ready": build_client("auto") is not None,
    }


def _llm_view() -> dict:
    return {
        "providers": [settings.public_view(p) for p in settings.load_providers()],
        "presets": {k: {kk: v.get(kk) for kk in ("name", "protocol", "base_url", "model", "models", "no_key",
                                                  "key_url", "hint")} for k, v in settings.PRESETS.items()},
        "protocols": settings.PROTOCOLS,
    }


@router.get("/state")
def state(resume: str | None = None):
    return {**_state(resume), "schema": SCHEMA}


class ProfileIn(BaseModel):
    resume: str | None = None
    profile: dict
    answers: list[dict] | None = None  # [{pattern, answer}]
    id_autofill: bool | None = None


@router.put("/profile")
def put_profile(body: ProfileIn):
    doc = _load_doc()
    v = _variant_or_404(doc, body.resume)
    cur = flatten(doc, v.id, derive=False)
    answers = cur.answers if body.answers is None else {
        a["pattern"].strip(): str(a.get("answer", "")).strip() for a in body.answers
        if str(a.get("pattern", "")).strip() and str(a.get("answer", "")).strip()}
    for pat in answers:
        try:
            re.compile(pat)
        except re.error as e:
            raise HTTPException(400, f"问答里的「{pat}」不是有效的匹配写法：{e}") from e
    never = [k for k in cur.never_fill if k != ID_KEY]
    if body.id_autofill is None:
        never = cur.never_fill
    elif not body.id_autofill:
        never.append(ID_KEY)
    try:
        new = Profile.model_validate({**body.profile, "answers": answers, "never_fill": never,
                                      "resume_file": v.resume_file, "portfolio_file": v.portfolio_file,
                                      "locale": v.locale})
    except Exception as e:
        raise HTTPException(400, f"资料格式不对：{e}") from e
    knowledge.save_doc(profile_path(), with_flat(doc, v.id, new))
    return _state(v.id)


# ---------- 多份简历 ----------

class NewResumeIn(BaseModel):
    name: str = "新简历"
    locale: str = "cn"
    copy_from: str | None = None  # 复制某份简历的内容


def _new_id(doc: Profile) -> str:
    while True:
        rid = "r" + uuid.uuid4().hex[:6]
        if all(v.id != rid for v in doc.resumes):
            return rid


@router.post("/resumes")
def new_resume(body: NewResumeIn):
    if body.locale not in LOCALES:
        raise HTTPException(400, "简历类型只能是 cn 或 overseas")
    doc = _load_doc()
    base = _variant_or_404(doc, body.copy_from).model_copy(deep=True) if body.copy_from else ResumeVariant()
    base.id, base.name, base.locale = _new_id(doc), body.name.strip() or "新简历", body.locale
    base.resume_file = base.portfolio_file = None
    doc.resumes.append(base)
    knowledge.save_doc(profile_path(), doc)
    return _state(base.id, doc)


class EditResumeIn(BaseModel):
    name: str | None = None
    locale: str | None = None
    make_default: bool = False


@router.patch("/resumes/{rid}")
def edit_resume(rid: str, body: EditResumeIn):
    doc = _load_doc()
    v = _variant_or_404(doc, rid)
    if body.name is not None and body.name.strip():
        v.name = body.name.strip()
    if body.locale is not None:
        if body.locale not in LOCALES:
            raise HTTPException(400, "简历类型只能是 cn 或 overseas")
        v.locale = body.locale
    if body.make_default:
        doc.active_resume = v.id
    knowledge.save_doc(profile_path(), doc)
    return _state(rid, doc)


@router.delete("/resumes/{rid}")
def delete_resume(rid: str):
    doc = _load_doc()
    v = _variant_or_404(doc, rid)
    if len(doc.resumes) < 2:
        raise HTTPException(400, "至少要保留一份简历")
    doc.resumes = [r for r in doc.resumes if r.id != rid]
    if doc.active_resume == rid:
        doc.active_resume = doc.resumes[0].id
    knowledge.save_doc(profile_path(), doc)
    notes_path(rid).unlink(missing_ok=True)
    shutil.rmtree(resume_dir() / rid, ignore_errors=True)  # 只清理助手自己存的这份简历的文件夹
    return _state(None, doc)


# ---------- 知识库 ----------

class NotesIn(BaseModel):
    text: str
    resume: str | None = None


@router.put("/notes")
def put_notes(body: NotesIn):
    v = _variant_or_404(_load_doc(), body.resume)
    notes_path(v.id).write_text(body.text, encoding="utf-8")
    return {"ok": True}


class IngestIn(BaseModel):
    text: str
    mode: str = "ai"  # ai | quick
    overwrite: bool = False
    resume: str | None = None


@router.post("/ingest")
def ingest(body: IngestIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "知识库是空的：先写点内容或上传简历")
    doc = _load_doc()
    v = _variant_or_404(doc, body.resume)
    used = None
    client = build_client("auto") if body.mode == "ai" else None
    try:
        if client:
            extracted = knowledge.extract_with_llm(client, text)
            used = client.last_used
        else:
            extracted = knowledge.extract_quick(text)
    except (LLMError, ValueError) as e:
        raise HTTPException(502, f"AI 整理失败：{e}") from e
    merged, notes = knowledge.merge(flatten(doc, v.id, derive=False), extracted, overwrite=body.overwrite)
    doc = with_flat(doc, v.id, merged)
    knowledge.save_doc(profile_path(), doc)
    return {**_state(v.id, doc), "changes": notes, "used": used,
            "warning": None if client else "没有可用的 AI 接口，只识别了手机号 / 邮箱 / GitHub 等固定格式的信息"}


class ResumeIn(BaseModel):
    name: str
    b64: str
    resume: str | None = None


@router.post("/resume")
def upload_resume(body: ResumeIn):
    """保存简历附件（供网页上「上传简历」用），并抽出文字让界面接着走「AI 整理」。"""
    try:
        data = base64.b64decode(body.b64)
    except ValueError as e:
        raise HTTPException(400, "文件内容损坏") from e
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "文件超过 15MB")
    name = re.sub(r'[\\/:*?"<>|]', "_", Path(body.name).name) or "resume"
    try:
        text = knowledge.extract_text(name, data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"读取简历失败：{e.__class__.__name__} {e}") from e
    doc = _load_doc()
    v = _variant_or_404(doc, body.resume)
    folder = resume_dir() / v.id  # 每份简历一个文件夹：不同简历上传同名文件也不会互相覆盖
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    dest.write_bytes(data)
    v.resume_file = str(dest)
    knowledge.save_doc(profile_path(), doc)
    return {"name": name, "text": text, "chars": len(text)}


PORTFOLIO_MAX = 30 * 1024 * 1024  # 站点上限多为 50MB；插件把文件转成 base64 传给页面，太大会卡


class PortfolioIn(BaseModel):
    name: str
    b64: str
    resume: str | None = None


@router.post("/portfolio")
def upload_portfolio(body: PortfolioIn):
    """保存作品集文件（zip / pdf …）。zip 里的 README 等文字一并返回，界面可以加入知识库。"""
    try:
        data = base64.b64decode(body.b64)
    except ValueError as e:
        raise HTTPException(400, "文件内容损坏") from e
    if len(data) > PORTFOLIO_MAX:
        raise HTTPException(413, "作品集超过 30MB：请压缩或挑重点，其余用链接代替")
    name = re.sub(r'[\\/:*?"<>|]', "_", Path(body.name).name) or "portfolio.zip"
    if not name.lower().endswith(knowledge.PORTFOLIO_EXTS):
        raise HTTPException(400, "作品集支持 .zip / .pdf / .rar / .7z")
    text = ""
    if name.lower().endswith(".zip"):
        try:
            text = knowledge.extract_zip_text(data)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    doc = _load_doc()
    v = _variant_or_404(doc, body.resume)
    folder = resume_dir() / v.id / "portfolio"
    shutil.rmtree(folder, ignore_errors=True)  # 每份简历只留一个作品集
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    dest.write_bytes(data)
    v.portfolio_file = str(dest)
    knowledge.save_doc(profile_path(), doc)
    return {"name": name, "text": text, "chars": len(text), "size": len(data)}


@router.delete("/portfolio")
def delete_portfolio(resume: str | None = None):
    doc = _load_doc()
    v = _variant_or_404(doc, resume)
    shutil.rmtree(resume_dir() / v.id / "portfolio", ignore_errors=True)
    v.portfolio_file = None
    knowledge.save_doc(profile_path(), doc)
    return _state(v.id, doc)


@router.get("/llm")
def get_llm():
    return _llm_view()


class ProviderIn(BaseModel):
    id: str | None = None
    preset: str
    protocol: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None  # None / 空：沿用已保存的 key
    enabled: bool = True


class LLMIn(BaseModel):
    providers: list[ProviderIn]  # 顺序即故障转移顺序


def _resolve(p: ProviderIn, saved: dict[str, dict]) -> dict:
    preset = settings.PRESETS.get(p.preset)
    if not preset:
        raise HTTPException(400, f"未知厂商：{p.preset}")
    old = saved.get(p.id or "", {})
    return {"id": p.id or settings.new_id(p.preset), "preset": p.preset,
            "protocol": p.protocol if p.protocol in settings.PROTOCOLS else preset["protocol"],
            "base_url": (p.base_url or preset["base_url"]).strip(), "model": (p.model or preset["model"]).strip(),
            "api_key": p.api_key.strip() if p.api_key and p.api_key.strip() else old.get("api_key"),
            "enabled": p.enabled}


@router.put("/llm")
def put_llm(body: LLMIn):
    saved = {p["id"]: p for p in settings.load_providers() if p.get("id")}
    settings.save_providers([_resolve(p, saved) for p in body.providers])
    return _llm_view()


@router.post("/llm/test")
def test_llm(body: ProviderIn):
    saved = {p["id"]: p for p in settings.load_providers() if p.get("id")}
    cfg = _resolve(body, saved)
    if settings.needs_key(cfg) and not cfg.get("api_key"):
        return {"ok": False, "error": "还没填 API Key"}
    if not cfg["base_url"] or not cfg["model"]:
        return {"ok": False, "error": "接口地址和模型不能为空"}
    return check_provider(cfg)


# ---------- 上手向导 ----------

def edge_path() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base and (p := Path(base) / "Microsoft/Edge/Application/msedge.exe").exists():
            return str(p)
    return shutil.which("msedge")


class OpenIn(BaseModel):
    what: str = Field(pattern="^(extensions|folder|runs)$")


@router.post("/open")
def open_something(body: OpenIn):
    from .server import EXT_DIR, _runs_dir  # 延迟导入：server 也会导入本模块

    if body.what == "extensions":
        edge = edge_path()
        if not edge:
            raise HTTPException(404, "没找到 Edge，请手动在浏览器地址栏输入 edge://extensions")
        subprocess.Popen([edge, "edge://extensions"])
    else:
        target = EXT_DIR if body.what == "folder" else _runs_dir()
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(target)  # noqa: S606  仅 Windows；目标是固定的本地目录
    return {"ok": True}


@router.get("/setup")
def setup():
    from .server import EXT_DIR

    return {"extension_dir": str(EXT_DIR), "edge": bool(edge_path()), "profile_file": str(profile_path())}


# ---------- 桌面窗口 ----------

def _port_open(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_gui(port: int) -> None:
    """双击 .bat 时没有控制台（pythonw）：把输出写进日志，崩溃时弹窗而不是一闪而过。"""
    log = ROOT / "runs" / "gui.log"
    log.parent.mkdir(exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(log, "a", encoding="utf-8", buffering=1)
    try:
        _run_gui(port)
    except Exception:
        import traceback

        err = traceback.format_exc()
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%F %T')}] 启动失败\n{err}")
        try:
            import ctypes

            last = err.strip().splitlines()[-1]
            ctypes.windll.user32.MessageBoxW(0, f"网申助手启动失败：\n{last}\n\n详细信息见 {log}", "网申助手", 0x10)
        except Exception:
            pass
        raise


def _run_gui(port: int) -> None:
    """启动本地服务，并用 Edge 的应用模式开一个独立窗口；关掉窗口即停止服务。"""
    import uvicorn

    from .server import app, sync_extension_js

    url = f"http://127.0.0.1:{port}/ui/"
    own_server = not _port_open(port)
    if own_server:
        sync_extension_js()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(50):
            if _port_open(port):
                break
            time.sleep(0.1)
    edge = edge_path()
    chrome = shutil.which("chrome") or next((str(p) for p in (
        Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Google/Chrome/Application/chrome.exe") if p.exists()), None)
    browser = edge or chrome
    if not browser:
        import webbrowser

        webbrowser.open(url)
        print(f"没找到 Edge / Chrome，已用默认浏览器打开 {url}。按 Ctrl+C 退出。")
        _wait_forever()
        return
    print(f"网申自动填写助手已启动：{url}\n关掉助手窗口即可退出。")
    started = time.time()
    # 独立的 user-data-dir：窗口关闭时进程才会结束（否则会并进已开着的浏览器，立刻返回）
    proc = subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={ROOT / '.gui-data'}",
                             "--window-size=1180,820", "--no-first-run", "--no-default-browser-check"])
    proc.wait()
    if time.time() - started < 3:  # 进程立刻退出：窗口可能由已有实例接管，服务继续跑
        _wait_forever()


def _wait_forever() -> None:
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run_gui(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
