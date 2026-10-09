"""命令行入口。

autofill fill <url>         打开网申页（复用登录态），你登录后按回车开始填写
autofill demo [name]        在本地 demo 表单上演示
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from .batch import done_companies, load_targets, record, referral_from_url, select, target_for_url
from .llm import PROVIDERS, OpenAICompatClient
from .pipeline import run
from .profile import load_profile

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "demo"
ICONS = {"filled": "✅", "kept": "✅", "uncertain": "🟡", "unfilled": "❌", "sensitive": "🔒", "planned": "📝"}


def print_report(report: dict) -> None:
    s = report["summary"]
    if report.get("dry_run"):
        print(f"\n[dry-run] 计划填写 {s['planned']} 个字段，页面未被修改")
    print(f"\n已填 {s['filled']}｜保留原值 {s['kept']}｜待确认 {s['uncertain']}｜未填 {s['unfilled']}｜敏感 {s['sensitive']}"
          f"｜耗时 {report['elapsed_s']}s")
    if report.get("upload"):
        print(f"📎 {report['upload']}")
    if report["llm_error"]:
        print(f"⚠ LLM 调用失败，本次只用了规则：{report['llm_error']}")
    for it in report["items"]:
        if it["status"] in ("filled", "kept"):
            continue
        print(f"  {ICONS[it['status']]} [{it['section'] or '-'}] {it['label'] or '(无标签)'}  {it['note']}")
    if report.get("unused"):
        print("\n⚠ 资料里有、但这张表没地方填的内容（考虑放进简历附件、Github 链接框或附加信息）：")
        for u in report["unused"]:
            print(f"  · {u}")
    print("\n请在浏览器里逐项检查，确认无误后自己点提交。")


def make_llm(name: str):
    return None if name == "none" else OpenAICompatClient(name)


# 每个浏览器一个配置目录：Chrome 和 Edge 的 Cookie 加密密钥不同，共用目录会丢登录态甚至损坏配置
DATA_DIRS = {"chrome": ".browser-data", "msedge": ".browser-data-msedge"}


def open_browser(p, args):
    data_dir = ROOT / DATA_DIRS.get(args.browser, f".browser-data-{args.browser}")
    if not data_dir.exists():
        print(f"首次用 {args.browser}：登录状态是独立的，需要在弹出的浏览器里重新登录一次")
    return p.chromium.launch_persistent_context(
        str(data_dir), channel=args.browser, headless=False, no_viewport=True,
        # Playwright 默认带 --no-sandbox（浏览器会提示「不受支持的命令行标志」），显式开回沙箱
        chromium_sandbox=True, args=["--start-maximized"],
    )


def safe_goto(page, url: str) -> None:
    """服务器一响应就把页面交给你，不等所有资源加载完：网申页常挂着很慢的第三方资源，等 load 会超时报错退出。"""
    try:
        page.goto(url, wait_until="commit", timeout=60000)
    except PlaywrightTimeout:
        print("页面打开得很慢，已经交给浏览器继续加载；好了以后照常操作即可")


def cmd_fill(args) -> None:
    profile = load_profile(args.profile)
    llm = make_llm(args.llm)
    referral = args.referral or referral_from_url(args.url)
    targets_file = os.environ.get("AUTOFILL_TARGETS")
    if not referral and targets_file and Path(targets_file).exists():
        # 具体职位的申请链接通常不带内推码：按域名回投递表里找这家公司的码
        t = target_for_url(load_targets(targets_file), args.url)
        if t and t.code:
            referral = t.code
            print(f"内推码：{referral}（投递表里「{t.company}」那一行）")
    elif referral:
        print(f"内推码：{referral}")
    with sync_playwright() as p:
        ctx = open_browser(p, args)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        safe_goto(page, args.url)
        while True:
            cmd = input("\n登录并打开申请表后按回车开始填写（多页表单翻页后再按回车；输入 q 退出）：").strip().lower()
            if cmd == "q":
                break
            page = ctx.pages[-1]  # 有的网站会在新标签页打开申请表
            print_report(run(page, profile, llm=llm, overwrite=args.overwrite, dry_run=args.dry_run,
                             upload=args.upload_resume, ctx={"referral": referral}))
        ctx.close()


def cmd_batch(args) -> None:
    log = ROOT / "runs" / "applications.csv"
    if not args.xlsx:
        sys.exit("没有指定投递表：命令里写路径，或在 .env 里设置 AUTOFILL_TARGETS")
    print(f"投递表：{args.xlsx}")
    targets = select(load_targets(args.xlsx), args.only, args.ats, args.industry)
    done = done_companies(log)
    if args.list:
        for t in targets:
            print(f"{t.row:>3}  {'✅' if t.company in done else '  '} {t.company:<12} {t.ats:<10} {t.code:<12} "
                  f"{(t.deadline or '-'):<10} {t.url[:60]}")
        print(f"共 {len(targets)} 家，已填 {sum(t.company in done for t in targets)} 家")
        return
    todo = [t for t in targets if args.redo or t.company not in done]
    print(f"本次 {len(todo)} 家（已填过的 {len(targets) - len(todo)} 家跳过，--redo 可重填）")
    profile = load_profile(args.profile)
    llm = make_llm(args.llm)
    with sync_playwright() as p:
        ctx = open_browser(p, args)
        for i, t in enumerate(todo, 1):
            page = ctx.new_page()
            safe_goto(page, t.url)
            print(f"\n[{i}/{len(todo)}] {t.company}（{t.ats}）内推码 {t.code or '无'}"
                  + (f"，截止 {t.deadline}" if t.deadline else ""))
            report = None
            while True:
                cmd = input("  登录、选好岗位、打开申请表后按回车填写；s 跳过这家；n 下一家；q 退出：").strip().lower()
                if cmd in ("s", "n", "q"):
                    break
                report = run(ctx.pages[-1], profile, llm=llm, overwrite=args.overwrite,
                             upload=args.upload_resume, ctx={"referral": t.code})
                print_report(report)
                print("  （多页表单翻页后再按回车；填好并自己提交后按 n 去下一家）")
            record(log, t, "filled" if report else "skipped", report)
            if cmd == "q":
                break
        ctx.close()


def cmd_demo(args) -> None:
    profile = load_profile(args.profile)
    path = DEMO_DIR / f"{args.name}.html"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.browser, headless=False, chromium_sandbox=True)
        page = browser.new_page()
        page.goto(path.as_uri())
        print_report(run(page, profile, llm=make_llm(args.llm)))
        input("\n按回车关闭浏览器")
        browser.close()


def cmd_login(args) -> None:
    """打开独立的浏览器配置，让你登录各网申站点一次；之后自动化测试复用这里的登录状态。"""
    urls = args.urls or [
        "https://careers.oppo.com/university/oppo/center/resume",
        "https://iflytek.zhiye.com/",
    ]
    with sync_playwright() as p:
        ctx = open_browser(p, args)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for i, u in enumerate(urls):
            safe_goto(page if i == 0 else ctx.new_page(), u)
        print("在打开的窗口里逐个登录；全部登录好以后，直接关掉这个浏览器窗口即可。")
        try:
            while ctx.pages:
                ctx.pages[0].wait_for_event("close", timeout=0)
        except Exception:
            pass
        print("已保存登录状态。")


def cmd_audit(args) -> None:
    from .audit import run_audit

    files = [Path(f) for f in args.files] or sorted((ROOT / "runs").glob("diag-*.json"))
    if not files:
        print("runs/ 里还没有诊断文件：在插件侧边栏点「导出诊断」生成")
        return
    run_audit(files, Path(args.profile), args.verbose)


def cmd_serve(args) -> None:
    from .server import serve

    serve(args.port)


def main(argv: list[str] | None = None) -> None:
    load_dotenv(ROOT / ".env")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="autofill", description="网申表单自动填写（只填不交）")
    ap.add_argument("--profile", default=str(ROOT / "profile.yaml"))
    ap.add_argument("--browser", default=os.environ.get("AUTOFILL_BROWSER") or "msedge", choices=["msedge", "chrome"],
                    help="默认 Edge；也可在 .env 里写 AUTOFILL_BROWSER=chrome")
    ap.add_argument("--llm", default="none", choices=["none", "auto", *PROVIDERS])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fill", help="填写真实网申页")
    f.add_argument("url")
    f.add_argument("--overwrite", action="store_true", help="覆盖页面上已有的值")
    f.add_argument("--dry-run", action="store_true", help="只识别、不填写，看看每个字段打算填什么")
    f.add_argument("--upload-resume", action="store_true",
                   help="先上传 profile.yaml 里 resume_file 指定的简历（会触发站点解析，之后再填写）")
    f.add_argument("--referral", default="", help="内推码；不写时从链接参数里找（recommendCode 等）")
    f.set_defaults(func=cmd_fill)
    b = sub.add_parser("batch", help="按投递表逐家打开并填写（带内推码）")
    b.add_argument("xlsx", nargs="?", default=os.environ.get("AUTOFILL_TARGETS") or None,
                   help="投递表；不写时用 .env 里的 AUTOFILL_TARGETS")
    b.add_argument("--only", default="", help="只投这些公司，逗号分隔，如 大疆,腾讯")
    b.add_argument("--ats", default="", help="只投某个系统：moka / 北森 / 飞书")
    b.add_argument("--industry", default="", help="只投某个行业，如 科技")
    b.add_argument("--list", action="store_true", help="只列出公司和进度，不打开浏览器")
    b.add_argument("--redo", action="store_true", help="已填过的也重新打开")
    b.add_argument("--overwrite", action="store_true", help="覆盖页面上已有的值")
    b.add_argument("--upload-resume", action="store_true", help="每家先上传简历再填写")
    b.set_defaults(func=cmd_batch)
    lg = sub.add_parser("login", help="在独立的浏览器配置里登录各网申站点（一次即可）")
    lg.add_argument("urls", nargs="*")
    lg.set_defaults(func=cmd_login)
    sv = sub.add_parser("serve", help="启动浏览器插件用的本地服务")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(func=cmd_serve)
    g = sub.add_parser("gui", help="打开桌面界面（资料、AI 接口、上手向导）")
    g.add_argument("--port", type=int, default=8765)
    g.set_defaults(func=lambda a: __import__("autofill.gui", fromlist=["run_gui"]).run_gui(a.port))
    au = sub.add_parser("audit", help="离线审计：用 runs/diag-*.json 重跑字段识别，看哪些字段还认不出")
    au.add_argument("files", nargs="*", help="诊断文件，缺省为 runs/ 下全部 diag-*.json")
    au.add_argument("-v", "--verbose", action="store_true", help="同时列出已识别的字段")
    au.set_defaults(func=cmd_audit)
    d = sub.add_parser("demo", help="在本地 demo 表单上演示")
    d.add_argument("name", nargs="?", default="feishu_style")
    d.set_defaults(func=cmd_demo)
    args = ap.parse_args(argv)
    args.func(args)
