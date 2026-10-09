"""批量投递：按投递表逐家打开网申页，填写时带上该行的内推码。

投递表不改动；进度记在 runs/applications.csv，下次自动跳过已填过的公司。
"""

from __future__ import annotations

import csv
import html
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import openpyxl

# 没有表头时按位置取列（与 E:\Desktop\投递.xlsx 一致）：公司, 批次, 岗位, 行业, 链接, 内推码, 城市, 原文
DEFAULT_COLS = {"company": 0, "batch": 1, "positions": 2, "industry": 3, "url": 4, "code": 5, "cities": 6}
HEADER_NAMES = {
    "company": r"^(公司|企业)|公司名称|企业名称", "batch": r"批次|类型|面向人群", "positions": r"岗位|职位",
    "industry": r"行业", "url": r"链接|网址|url", "code": r"内推码|推荐码|邀请码|code", "cities": r"城市|地点|base",
    "deadline": r"截止",
}
ATS_PATTERNS = [("moka", r"mokahr"), ("北森", r"zhiye|beisen"), ("飞书", r"feishu"), ("hotjob", r"hotjob")]
LOG_FIELDS = ["time", "company", "ats", "url", "code", "status", "filled", "uncertain", "unfilled", "report"]


@dataclass
class Target:
    row: int
    company: str
    url: str
    code: str = ""
    industry: str = ""
    batch: str = ""
    cities: str = ""
    deadline: str = ""

    @property
    def ats(self) -> str:
        host = urlparse(self.url).netloc
        return next((name for name, pat in ATS_PATTERNS if re.search(pat, host)), host or "?")


def first_url(cell) -> str:
    """单元格里的第一个链接。表格常有脏数据：链接后面粘着 emoji（吉比特的「📌」）、& 被写成 &amp;（米哈游）。"""
    text = html.unescape(str(cell or ""))
    m = re.search(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", text)
    return m.group(0).rstrip(".,;:!?)'") if m else ""


def referral_from_url(url: str) -> str:
    """链接里自带的内推码参数（recommendCode / referralCode / referer_code …）。shareId 之类不是内推码。"""
    for k, v in parse_qsl(urlparse(url).query):
        if re.search(r"(refer|recommend|invite)\w*code|referral", k, re.I) and re.fullmatch(r"[A-Za-z0-9_-]{4,40}", v):
            return v
    return ""


def target_for_url(targets: list[Target], url: str) -> Target | None:
    """按域名找投递表里的那家公司。Moka / 飞书这类共享域名还要再比路径里的公司标识。"""
    u = urlparse(url)
    same_host = [t for t in targets if urlparse(t.url).netloc == u.netloc]
    if len(same_host) <= 1:
        return same_host[0] if same_host else None
    # 共享域名（app.mokahr.com/…/dji/…）：比较路径里的第一个非通用片段
    generic = {"m", "campus-recruitment", "campus_apply", "campus", "jobs", "s", "apply", "social-recruitment"}
    seg = lambda p: [x for x in p.split("/") if x and x not in generic and not x.isdigit()]  # noqa: E731
    want = seg(u.path)
    return next((t for t in same_host if want and seg(urlparse(t.url).path)[:1] == want[:1]), None)


def load_targets(path: str | Path) -> list[Target]:
    # 不用 read_only：它按文件里记录的表格尺寸截断每行，有的表记录不准（如有的表只记到 D 列）
    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    rows = [r for r in ws.iter_rows(values_only=True) if any(c not in (None, "") for c in r)]
    cols: dict[str, int] = dict(DEFAULT_COLS)
    # 表头可能不在第一行（上面常有一行说明文字）：前几行里，能对上至少两个列名、又不含链接的那行就是表头
    for h, row in enumerate(rows[:6]):
        cells = [str(c or "").strip() for c in row]
        hits = {key: next((i for i, c in enumerate(cells) if re.search(pat, c, re.I)), None) for key, pat in HEADER_NAMES.items()}
        hits = {k: v for k, v in hits.items() if v is not None}
        if len(hits) >= 2 and not any(first_url(c) for c in cells):
            cols.update(hits)
            rows = rows[h + 1:]
            break
    get = lambda r, k: str(r[cols[k]]).strip() if k in cols and cols[k] < len(r) and r[cols[k]] is not None else ""  # noqa: E731
    out = []
    for i, r in enumerate(rows, start=1):
        url = first_url(get(r, "url"))
        if not url:
            continue
        out.append(Target(row=i, company=get(r, "company"), url=url, code=get(r, "code") or referral_from_url(url),
                          industry=get(r, "industry"), batch=get(r, "batch"), cities=get(r, "cities"),
                          deadline=get(r, "deadline")))
    return out


def select(targets: list[Target], only: str = "", ats: str = "", industry: str = "") -> list[Target]:
    names = [n for n in re.split(r"[,，、\s]+", only) if n]
    return [t for t in targets
            if (not names or any(n in t.company for n in names))
            and (not ats or ats.lower() in t.ats.lower())
            and (not industry or industry in t.industry)]


def done_companies(log: Path) -> set[str]:
    if not log.exists():
        return set()
    with log.open(encoding="utf-8-sig") as f:
        return {r["company"] for r in csv.DictReader(f) if r.get("status") == "filled"}


def record(log: Path, t: Target, status: str, report: dict | None = None) -> None:
    new = not log.exists()
    log.parent.mkdir(parents=True, exist_ok=True)
    s = (report or {}).get("summary", {})
    with log.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "company": t.company, "ats": t.ats, "url": t.url,
                    "code": t.code, "status": status, "filled": s.get("filled", ""), "uncertain": s.get("uncertain", ""),
                    "unfilled": s.get("unfilled", ""), "report": (report or {}).get("file", "")})
