"""个人资料模型。资料只存在本地 profile.yaml，从不整份发给 LLM。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class Basic(BaseModel):
    name: str | None = None
    name_en: str | None = None
    gender: str | None = None
    birth_date: str | None = None  # YYYY-MM-DD
    phone: str | None = None
    phone_alt: str | None = None
    email: str | None = None
    email_alt: str | None = None
    id_type: str | None = None  # 身份证 / 护照 …
    id_number: str | None = None
    political_status: str | None = None
    ethnicity: str | None = None
    hometown: str | None = None
    current_city: str | None = None
    expected_city: str | None = None
    expected_salary: str | None = None
    job_intention: str | None = None
    available: str | None = None  # 如「随时到岗」
    accept_adjustment: bool | None = None  # 是否服从调剂
    english: str | None = None  # 如「雅思 6.5」
    languages: str | None = None  # 擅长的开发语言，逗号分隔，按熟练度排
    foreign_language: str | None = None  # 外语语种，如「英语」
    github: str | None = None
    homepage: str | None = None
    # 海外申请（英文表单）
    first_name: str | None = None  # 留空时由 name_en 拆出
    last_name: str | None = None
    linkedin: str | None = None
    address: str | None = None
    country: str | None = None  # 现居国家 / 地区
    state: str | None = None  # 州 / 省
    postal_code: str | None = None
    nationality: str | None = None
    work_authorization: str | None = None  # 如「需要签证」「美国公民」
    authorized_to_work: bool | None = None  # 是否有当地合法工作资格
    need_sponsorship: bool | None = None  # 是否需要公司担保签证


class Education(BaseModel):
    school: str | None = None
    school_en: str | None = None
    country: str | None = None
    is_overseas: bool | None = None
    college: str | None = None
    major: str | None = None
    degree: str | None = None  # 本科 / 硕士 / 博士
    degree_name: str | None = None  # 理学硕士
    start: str | None = None  # YYYY-MM
    end: str | None = None
    gpa: str | None = None
    rank: str | None = None
    thesis: str | None = None
    courses: str | None = None
    city: str | None = None  # 学校所在城市
    location: str | None = None  # 学校所在地，国家 / 省 / 市，如「中国 江苏省 苏州市」
    is_exchange: bool | None = None  # 是否为交流学习
    is_joint: bool | None = None  # 是否为联合办学 / 联合培养
    study_type: str | None = None  # 全日制 / 非全日制
    major_category: str | None = None  # 专业类别，逗号分隔多个说法，如「计算机类、工学」
    advisor: str | None = None
    lab: str | None = None
    research_area: str | None = None


class Experience(BaseModel):
    kind: str = "实习"  # 实习 / 工作 / 科研
    company: str | None = None
    title: str | None = None
    department: str | None = None
    city: str | None = None
    start: str | None = None
    end: str | None = None
    description: str | None = None


class Project(BaseModel):
    name: str | None = None
    role: str | None = None
    start: str | None = None
    end: str | None = None
    tech: str | None = None
    url: str | None = None
    description: str | None = None


class Paper(BaseModel):
    title: str | None = None
    venue: str | None = None
    year: str | None = None
    author_order: str | None = None  # 独立作者 / 第一作者 …
    date: str | None = None  # 发表日期 YYYY-MM
    citation: str | None = None  # 完整引用，给「粘贴论文信息」这类框


VARIANT_FIELDS = ("education", "experience", "projects", "papers", "skills", "awards", "self_intro", "resume_file")
LOCALES = {"cn": "中文（国内校招）", "overseas": "海外（英文表单）"}


class ResumeVariant(BaseModel):
    """一份简历：基本信息共用，其余内容每份各一套。"""

    id: str = "default"
    name: str = "默认简历"
    locale: str = "cn"  # cn | overseas
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    papers: list[Paper] = Field(default_factory=list)
    skills: str | None = None
    awards: str | None = None
    self_intro: str | None = None
    resume_file: str | None = None  # 简历附件路径


class Profile(BaseModel):
    basic: Basic = Field(default_factory=Basic)
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    papers: list[Paper] = Field(default_factory=list)
    skills: str | None = None
    awards: str | None = None
    self_intro: str | None = None
    resume_file: str | None = None  # 简历附件路径，--upload-resume 时上传
    # 以上 education … resume_file 是「当前选用那份简历」的展开视图（load_profile 填充），文件里存在 resumes 中
    locale: str = "cn"
    resumes: list[ResumeVariant] = Field(default_factory=list)
    active_resume: str | None = None  # 默认简历的 id
    # 每家都问、答案固定的问题：{标签正则: 答案}，优先于其他规则。如 {"是否有.*亲属": "否"}
    answers: dict[str, str] = Field(default_factory=dict)
    # 这些键永远不自动填写，只在报告里提醒
    never_fill: list[str] = Field(default_factory=lambda: ["basic.id_number"])

    def section_items(self, section: str) -> list[Any]:
        if section == "education":
            return self.education
        if section == "work":
            return [e for e in self.experience if e.kind != "科研"]
        if section == "research":
            return [e for e in self.experience if e.kind == "科研"]
        if section == "project":
            return self.projects
        if section == "paper":
            return self.papers
        return []

    def get(self, key: str, index: int = 0) -> Any:
        """key 形如 basic.name / education.school / self_intro。"""
        head, _, attr = key.partition(".")
        if not attr:
            return getattr(self, head, None)
        if head == "basic":
            return getattr(self.basic, attr, None)
        items = self.section_items(head)
        if index >= len(items):
            return None
        return getattr(items[index], attr, None)


def _normalize(data: dict) -> dict:
    """旧格式（教育 / 经历等直接写在顶层）迁移成一份「默认简历」。"""
    data = dict(data)
    if not data.get("resumes"):
        legacy = {k: data.pop(k) for k in VARIANT_FIELDS if k in data}
        data["resumes"] = [{"id": "default", "name": "默认简历", "locale": "cn", **legacy}]
        data["active_resume"] = "default"
    else:
        for k in VARIANT_FIELDS:
            data.pop(k, None)
    return data


def parse_doc(data: dict | None) -> Profile:
    """读出完整文档：基本信息 + 全部简历变体（顶层的展开视图为空）。"""
    return Profile.model_validate(_normalize(data or {}))


def find_variant(doc: Profile, rid: str | None = None) -> ResumeVariant | None:
    for want in (rid, doc.active_resume):
        v = next((v for v in doc.resumes if want and v.id == want), None)
        if v:
            return v
    return doc.resumes[0] if doc.resumes else None


def flatten(doc: Profile, rid: str | None = None, derive: bool = True) -> Profile:
    """展开成「基本信息 + 某一份简历」的平铺视图，填表流程只认这种。derive：补出可推导的字段（名 / 姓）。"""
    v = find_variant(doc, rid) or ResumeVariant()
    flat = doc.model_copy(deep=True)
    for k in VARIANT_FIELDS:
        setattr(flat, k, getattr(v, k))
    flat.locale = v.locale
    flat.active_resume = v.id
    if derive:
        b = flat.basic
        parts = (b.name_en or "").split()
        if not b.first_name and len(parts) >= 2:
            b.first_name = parts[0]
        if not b.last_name and len(parts) >= 2:
            b.last_name = " ".join(parts[1:])
    return flat


def with_flat(doc: Profile, rid: str, flat: Profile) -> Profile:
    """把平铺视图里改过的内容写回 doc 的基本信息和指定那份简历。"""
    out = doc.model_copy(deep=True)
    out.basic, out.answers, out.never_fill = flat.basic, flat.answers, flat.never_fill
    v = find_variant(out, rid)
    for k in VARIANT_FIELDS:
        setattr(v, k, getattr(flat, k))
    return out


def dump_doc(doc: Profile) -> dict:
    """落盘格式：basic / active_resume / resumes / answers / never_fill。"""
    data = doc.model_dump(exclude=set(VARIANT_FIELDS) | {"locale"}, exclude_none=True)
    data["basic"] = {k: v for k, v in data.get("basic", {}).items() if v not in ("", None)}
    for r in data["resumes"]:
        for sec in ("education", "experience", "projects", "papers"):
            r[sec] = [{k: v for k, v in x.items() if v not in ("", None)} for x in r.get(sec, [])]
    return data


def load_profile(path: str | Path, resume: str | None = None) -> Profile:
    """填表用：基本信息 + resume 指定的那份简历（缺省为默认简历）。"""
    doc = parse_doc(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    return flatten(doc, resume)
