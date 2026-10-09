"""资料覆盖率：资料里有、但这张表上没有地方填的内容。

很多表没有「项目经历」「科研经历」「自我评价」栏，最有分量的内容就这么漏了。
报告把它们列出来，提醒你放进简历附件、Github 链接框或附加信息里。
"""

from __future__ import annotations

from .profile import Profile
from .rules import KEY_CATALOG, LIST_SECTIONS

SECTION_NAMES = {"education": "教育经历", "work": "实习/工作经历", "research": "科研经历",
                 "project": "项目经历", "paper": "论文"}
# 这些资料本来就不是给表单逐项填的，或者已经在别处体现，不算遗漏
SKIP_KEYS = {"basic.name_en", "basic.phone_alt", "basic.email_alt", "basic.id_number", "basic.job_intention",
             "basic.accept_adjustment", "basic.expected_salary", "basic.id_type",
             "basic.first_name", "basic.last_name", "basic.address", "basic.country", "basic.state",
             "basic.postal_code", "basic.nationality", "basic.work_authorization", "basic.authorized_to_work",
             "basic.need_sponsorship", "basic.linkedin"}
SKIP_ATTRS = {"school_en", "country", "is_overseas", "degree_name", "courses", "tech", "url", "kind", "year",
              "citation", "role"}


def _title(section: str, item) -> str:
    for attr in ("name", "school", "company", "title"):
        v = getattr(item, attr, None)
        if v:
            return str(v)
    return f"第 {section} 段"


def unused_content(profile: Profile, used: set[tuple[str, int]]) -> list[str]:
    """used：已经填进表单的 (资料键, 段落序号)。返回人能看懂的遗漏清单。"""
    out: list[str] = []
    used_keys = {k for k, _ in used}

    for key in ("basic." + a for a in type(profile.basic).model_fields):
        if key in SKIP_KEYS or key in profile.never_fill or key in used_keys:
            continue
        v = profile.get(key)
        if v not in (None, ""):
            out.append(f"{KEY_CATALOG.get(key, key)}：{v}")

    for key, name in (("skills", "专业技能"), ("self_intro", "自我评价"), ("awards", "获奖情况")):
        if getattr(profile, key) and key not in used_keys:
            out.append(name)

    for sec in sorted(LIST_SECTIONS):
        items = profile.section_items(sec)
        if not items:
            continue
        used_idx = {i for k, i in used if k.startswith(sec + ".")}
        missing = [i for i in range(len(items)) if i not in used_idx]
        if missing:
            titles = "、".join(_title(sec, items[i]) for i in missing)
            out.append(f"{SECTION_NAMES[sec]} {len(missing)} 段整段没填：{titles}")
        # 填了这一段，但段内有资料没地方放（如 GPA）
        for i in sorted(used_idx):
            if i >= len(items):
                continue
            left = [a for a, v in items[i].model_dump().items()
                    if v not in (None, "") and a not in SKIP_ATTRS and (f"{sec}.{a}", i) not in used]
            if left:
                names = "、".join(KEY_CATALOG.get(f"{sec}.{a}", a) for a in left)
                out.append(f"{SECTION_NAMES[sec]}「{_title(sec, items[i])}」里没填的：{names}")
    return out
