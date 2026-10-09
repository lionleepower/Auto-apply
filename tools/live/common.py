"""真实站点实测的公用部分：站点配置、已登录的浏览器、等表单出现、测试用本地服务、插件副本。

所有脚本都用 .browser-data-msedge（`autofill login` 登录过的独立 Edge 配置），不碰你日常用的 Edge。
窗口默认放在屏幕外（不打扰你用电脑），加 --show 可以看着它填。
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "runs" / "live"  # 实测报告、截图、日志都放这里（runs/ 已在 .gitignore）
SITES_FILE = Path(__file__).with_name("sites.json")  # 你的真实站点（含内推码，已在 .gitignore）
SITES_EXAMPLE = Path(__file__).with_name("sites.example.json")
TEST_PORT = 8766  # 测试用本地服务的端口，和日常用的 8765 分开，互不影响
EXT_COPY = ROOT / ".live-ext"  # 改成连 8766 的插件副本


def load_sites() -> dict:
    f = SITES_FILE if SITES_FILE.exists() else SITES_EXAMPLE
    return json.loads(f.read_text(encoding="utf-8"))


def site(name: str) -> dict:
    sites = load_sites()
    if name not in sites:
        sys.exit(f"没有站点「{name}」。可用：{', '.join(sites)}（在 {SITES_FILE.name} 里加）")
    return sites[name]


def out_dir() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    return OUT


def launch(p, show: bool = False, extension: Path | None = None):
    """打开已登录的独立 Edge 配置。extension：加载这个解压缩的插件目录。"""
    args = [] if show else ["--window-position=-2400,0"]
    kw = {}
    if extension:
        kw["ignore_default_args"] = ["--disable-extensions"]
        args += [f"--disable-extensions-except={extension}", f"--load-extension={extension}"]
    return p.chromium.launch_persistent_context(
        str(ROOT / ".browser-data-msedge"), channel="msedge", headless=False, chromium_sandbox=True,
        viewport={"width": 1400, "height": 900}, args=args, **kw)


def open_form(page, url: str, settle_ms: int = 1500) -> None:
    """打开网申页并等表单真正渲染出来（讯飞要十几秒）。"""
    page.goto(url, wait_until="commit", timeout=60000)
    page.wait_for_timeout(5000)
    for _ in range(40):
        n = page.evaluate("() => [...document.querySelectorAll('input, textarea')]"
                          ".filter(e => e.getClientRects().length).length")
        if n >= 5:
            break
        page.wait_for_timeout(1000)
    page.wait_for_timeout(settle_ms)
    if "login" in page.url.lower() or "passport" in page.url.lower():
        print("!! 跳到了登录页：登录状态过期了，先运行 `autofill login` 重新登录", file=sys.stderr)


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2):
            return True
    except Exception:
        return False


@contextlib.contextmanager
def test_server(port: int = TEST_PORT):
    """在测试端口起一个本地服务（报告写到 runs/live/），用完关掉。"""
    if _healthy(port):
        sys.exit(f"端口 {port} 已经有服务在跑（上次的测试没退出？）。先关掉它再测。")
    env = dict(os.environ, AUTOFILL_RUNS=str(out_dir()), PYTHONIOENCODING="utf-8")
    exe = ROOT / ".venv" / "Scripts" / ("autofill.exe" if os.name == "nt" else "autofill")
    log = open(out_dir() / f"server-{port}.log", "w", encoding="utf-8")
    proc = subprocess.Popen([str(exe), "serve", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
    try:
        for _ in range(60):
            if _healthy(port):
                break
            if proc.poll() is not None:
                sys.exit(f"本地服务没起来，看 {log.name}")
            time.sleep(0.5)
        yield port
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(10)
        log.close()


def extension_copy(port: int = TEST_PORT) -> Path:
    """把 extension/ 复制一份、改连测试端口。插件代码和你装的那份完全一样，只是端口不同。"""
    from autofill.server import sync_extension_js
    sync_extension_js()  # 先把最新的页面脚本同步进 extension/
    shutil.rmtree(EXT_COPY, ignore_errors=True)
    shutil.copytree(ROOT / "extension", EXT_COPY)
    for name in ("sidepanel.js", "manifest.json"):
        f = EXT_COPY / name
        f.write_text(f.read_text(encoding="utf-8").replace("8765", str(port)), encoding="utf-8")
    return EXT_COPY


def extension_id(ctx) -> str:
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
    return sw.url.split("/")[2]


def summarize(rep: dict) -> list[str]:
    """报告里值得看的部分：汇总、分区保存结果、没填上 / 待确认的字段。"""
    lines = [f"summary: {rep['summary']}  耗时 {rep.get('elapsed_s')}s"]
    for s in rep.get("sections") or []:
        state = "已保存" if s.get("saved") else "未保存"
        extra = "" if s.get("saved") else "：" + ("、".join(sorted(set(s.get("errors") or []))) or "原因不明")
        dialogs = f"  弹框：{s['dialogs']}" if s.get("dialogs") else ""
        lines.append(f"  分区「{s['section']}」{state}{extra}{dialogs}")
    for it in rep["items"]:
        if it["status"] in ("filled", "kept"):
            continue
        lines.append(f"  {it['status'][:4]} {it['kind'][:11]:11} {it['section'][:8]:8} {it['label'][:20]:20} "
                     f"| {str(it['key'])[:22]:22} | {(it.get('note') or '')[:80]}")
    return lines
