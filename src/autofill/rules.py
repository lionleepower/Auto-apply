"""规则映射：分区识别 + 标签 → 资料键，以及把资料值转成某个字段能接受的候选值。"""

from __future__ import annotations

import re
from typing import Any

from .profile import Profile

# 顺序有意义：先判更具体的分区
SECTION_PATTERNS: list[tuple[str, str]] = [
    ("research", r"科研|研究经历|学术经历|在校实践|校内实践|实践经历|社会实践|research (experience|history)"),
    ("project", r"项目|\bprojects?\b"),
    ("education", r"教育|学历|院校|学校|education|academic|universit|\bschool\b"),
    ("work", r"实习|工作经历|工作经验|任职|职业经历|experience|employment|internship|work history|professional"),
    ("paper", r"论文|发表|出版|学术成果|publication|paper"),
    ("award", r"竞赛|获奖|荣誉|奖项|awards?\b|honou?rs"),
    ("certificate", r"证书|资格认证|certificat|licen[sc]e"),
    ("patent", r"专利"),
    ("basic", r"基本|基础|个人|联系|求职意向|意向|志愿|调剂|其他|附加|补充|语言|技能|personal|contact|general|additional|links|application|diversity|eeo|demographic|basic"),
]

# input id / name 里的结构提示，如 resumeVo_resumeEducationInfo_0_startDate
HINT_SECTIONS: list[tuple[str, str]] = [
    ("education", r"educat|school"),
    ("work", r"work|intern|job|employ|career"),
    ("project", r"project"),
    ("paper", r"paper|publication"),
    ("award", r"award|prize|competition|honou?r"),
]

LIST_SECTIONS = {"education", "work", "research", "project", "paper"}

IGNORE = "__ignore__"  # 明确不该用资料填的字段（紧急联系人电话等）

# 资料键说明：给 LLM 看的「目录」，只有键名和含义，没有值
KEY_CATALOG: dict[str, str] = {
    "basic.name": "中文姓名",
    "basic.name_en": "英文名/拼音名",
    "basic.gender": "性别",
    "basic.birth_date": "出生日期",
    "basic.phone": "手机号",
    "basic.email": "邮箱",
    "basic.id_number": "身份证/证件号码",
    "basic.political_status": "政治面貌",
    "basic.ethnicity": "民族",
    "basic.hometown": "籍贯/生源地/户口所在地",
    "basic.current_city": "现居城市",
    "basic.expected_city": "期望工作城市",
    "basic.expected_salary": "期望薪资",
    "basic.job_intention": "求职意向/意向岗位",
    "basic.available": "到岗时间/可实习时间",
    "basic.english": "英语水平/外语成绩",
    "basic.github": "GitHub/代码仓库链接",
    "basic.homepage": "个人主页/博客",
    "basic.portfolio": "作品集链接（缺省用 GitHub）",
    "portfolio.desc": "作品集 / 代表作品的文字说明",
    "ctx.referral": "内推码/推荐码",
    "basic.first_name": "名 / First name / Given name",
    "basic.last_name": "姓 / Last name / Family name",
    "basic.linkedin": "LinkedIn 链接",
    "basic.address": "详细地址 / Street address",
    "basic.country": "现居国家 / Country of residence",
    "basic.state": "州 / 省 / State / Province",
    "basic.postal_code": "邮编 / ZIP / Postal code",
    "basic.nationality": "国籍 / Nationality / Citizenship",
    "basic.work_authorization": "工作许可状态 / Work authorization status",
    "basic.authorized_to_work": "是否有合法工作资格（Yes/No）",
    "basic.need_sponsorship": "是否需要公司担保签证（Yes/No）",
    "skills": "专业技能",
    "awards": "获奖情况",
    "self_intro": "自我评价/个人优势",
    "education.school": "学校名称",
    "education.country": "学校所在国家/地区",
    "education.is_overseas": "是否海外/境外院校",
    "education.college": "学院/院系",
    "education.major": "专业",
    "education.degree": "学历（本科/硕士/博士）",
    "education.degree_name": "学位",
    "education.start": "入学时间",
    "education.end": "毕业时间",
    "education.gpa": "GPA/绩点/成绩",
    "education.gpa_scale": "GPA 制式（4分制 / 5分制 / 百分制）",
    "education.rank": "专业排名",
    "education.thesis": "毕业论文题目",
    "education.courses": "主修课程",
    "education.city": "学校所在城市",
    "education.is_highest": "是否为最高学历",
    "education.is_exchange": "是否为交流学习",
    "education.is_joint": "是否为联合办学/联合培养",
    "education.study_type": "受教育类型（全日制等）",
    "education.major_category": "专业类别",
    "basic.languages": "擅长的开发语言",
    "basic.foreign_language": "外语语种",
    "basic.english_score": "外语成绩分数",
    "education.location": "学校所在地（国家/省/市）",
    "paper.date": "发表日期",
    "education.advisor": "导师姓名",
    "education.lab": "所在实验室",
    "education.research_area": "研究方向",
    "basic.accept_adjustment": "是否服从调剂",
    "work.kind": "工作性质（实习/全职）",
    "work.department": "所在部门",
    "work.company": "公司名称",
    "work.title": "职位名称",
    "work.city": "工作城市",
    "work.start": "开始时间",
    "work.end": "结束时间",
    "work.description": "工作内容描述",
    "research.company": "科研单位/实验室",
    "research.title": "科研角色",
    "research.start": "科研开始时间",
    "research.end": "科研结束时间",
    "research.description": "科研内容描述",
    "project.name": "项目名称",
    "project.role": "项目角色",
    "project.start": "项目开始时间",
    "project.end": "项目结束时间",
    "project.url": "项目链接",
    "project.description": "项目描述",
    "paper.title": "论文题目",
    "paper.venue": "发表期刊/会议",
    "paper.year": "发表年份",
    "paper.author_order": "作者顺序",
    "paper.citation": "论文完整引用信息",
}

# (分区, 标签正则, 键)；分区写 "*" 表示任何分区都适用。先匹配分区专属规则。
LABEL_RULES: list[tuple[str, str, str]] = [
    # 「是否海外院校」「院校所在国家」这类要排在「院校」之前
    ("education", r"是否.*最高学历|最高学历.*是否", "education.is_highest"),
    ("education", r"交流", "education.is_exchange"),
    ("education", r"联合办学|联合培养|中外合作", "education.is_joint"),
    ("education", r"受教育类型|学历类型|学习形式|培养方式|全日制", "education.study_type"),
    ("education", r"专业类别|专业大类|学科门类", "education.major_category"),
    ("education", r"入学|开始", "education.start"),
    ("education", r"毕业时间|结束|离校", "education.end"),
    ("education", r"海外|境外|留学", "education.is_overseas"),
    ("education", r"城市|所在地", "education.city"),
    ("education", r"导师", "education.advisor"),
    ("education", r"实验室", "education.lab"),
    ("education", r"研究方向", "education.research_area"),
    ("education", r"排名", "education.rank"),
    ("education", r"国家|地区", "education.country"),
    ("education", r"学校|院校", "education.school"),
    ("education", r"学院|院系", "education.college"),
    ("education", r"专业(?!排名)", "education.major"),
    ("education", r"学历", "education.degree"),
    ("education", r"学位", "education.degree_name"),
    ("education", r"(gpa|绩点|成绩).*(类型|制)|(类型|制).*(gpa|绩点)|绩点制|分制", "education.gpa_scale"),
    ("education", r"gpa|绩点|成绩", "education.gpa"),
    ("education", r"论文|毕设|毕业设计", "education.thesis"),
    ("education", r"课程", "education.courses"),
    ("work", r"性质|类型", "work.kind"),
    ("work", r"年薪|月薪|薪资|薪酬|收入", IGNORE),  # 上一份工作的薪资：资料里没有，也不该编（百度「职位年薪」曾被当成职位名称）
    ("work", r"部门", "work.department"),
    ("work", r"公司|单位|企业", "work.company"),
    ("work", r"职位|岗位|职务", "work.title"),
    ("work", r"城市|地点", "work.city"),
    ("work", r"开始|入职", "work.start"),
    ("work", r"结束|离职", "work.end"),
    ("work", r"描述|内容|职责|工作", "work.description"),
    ("research", r"单位|机构|学校|实验室|项目名称|名称", "research.company"),
    ("research", r"职位|角色|岗位|担任", "research.title"),
    ("research", r"开始", "research.start"),
    ("research", r"结束", "research.end"),
    ("research", r"描述|内容|职责|经历", "research.description"),
    ("project", r"名称|项目名", "project.name"),
    ("project", r"角色|担任|职务", "project.role"),
    ("project", r"开始", "project.start"),
    ("project", r"结束", "project.end"),
    ("project", r"链接|地址|url", "project.url"),
    ("project", r"描述|内容|介绍|职责|经历", "project.description"),
    ("paper", r"粘贴|论文信息|成果描述|描述|简介|详情", "paper.citation"),
    ("paper", r"作者顺序|作者排序|第几作者|作者排名", "paper.author_order"),
    ("paper", r"题目|标题|名称", "paper.title"),
    ("paper", r"渠道|期刊|会议|刊物", "paper.venue"),
    ("award", r"作品.*(链接|地址|网址|url)|作品集.*(链接|地址|网址)|portfolio", "basic.portfolio"),
    ("award", r"作品(描述|说明|简介|介绍)", "portfolio.desc"),
    ("award", r"描述|情况", "awards"),
    ("award", r".", IGNORE),
    ("certificate", r".", IGNORE),
    ("patent", r".", IGNORE),
    ("paper", r"发布时间|发表时间|日期", "paper.date"),
    ("paper", r"年份|时间", "paper.year"),
    ("*", r"推荐码|内推码|推荐编码|内推编码|邀请码|referral|refer code", "ctx.referral"),
    ("*", r"紧急|联系人|推荐人|内推人|内推|验证码|星级|\bstar\b|粉丝|follower", IGNORE),
    ("*", r"推荐方式|内推方式|推荐渠道", IGNORE),  # 站点按分享链接预选好的单选（无 / 内推 / 大使推荐），不动
    ("*", r"调剂", "basic.accept_adjustment"),
    ("*", r"身份证|证件号", "basic.id_number"),
    ("*", r"毕业院校|最高学历.*学校", "education.school"),
    ("*", r"最高学历|学历", "education.degree"),
    ("*", r"^专业$|所学专业", "education.major"),
    # 标签后面常跟提示语（OPPO：「姓名请确保填写的姓名与证件姓名一致」）
    ("*", r"^(姓名|名字|真实姓名|中文名|中文姓名)(?!.*(拼音|英文|联系人|紧急))", "basic.name"),
    ("*", r"英文(姓)?名|拼音|english name", "basic.name_en"),
    ("*", r"性别", "basic.gender"),
    ("*", r"出生|生日", "basic.birth_date"),
    ("*", r"手机|电话|联系方式|^\+\d{1,3}$", "basic.phone"),  # 讯飞的手机号标签只有「+86」
    ("*", r"邮箱|e-?mail|电子邮件", "basic.email"),
    ("*", r"政治面貌", "basic.political_status"),
    ("*", r"民族", "basic.ethnicity"),
    ("*", r"籍贯|生源地|户口|户籍", "basic.hometown"),
    ("*", r"期望.*(城市|地点)|意向.*(城市|地点)|目标.*(城市|地点)|工作地点", "basic.expected_city"),
    ("*", r"现居|所在城市|居住|现住", "basic.current_city"),
    ("*", r"期望薪资|薪资", "basic.expected_salary"),
    ("*", r"到岗|入职时间|可实习", "basic.available"),
    ("*", r"^语种|外语语种|语言种类", "basic.foreign_language"),
    # 语言能力表单里的「语言证书」「等级/分数」：从「雅思 6.5」里拆
    ("*", r"语言证书|证书类型|考试类型|考试名称", "basic.english"),
    ("*", r"^(等级|分数|成绩|得分)(\s*/\s*(等级|分数|成绩))?$|语言.*(等级|分数|成绩)", "basic.english_score"),
    ("*", r"英语|外语|雅思|托福|ielts|toefl|语言能力|语言水平", "basic.english"),
    ("*", r"作品.*(链接|地址|网址)|作品集.*(链接|地址|网址)|portfolio|个人作品", "basic.portfolio"),
    ("*", r"github|代码仓库", "basic.github"),
    ("*", r"主页|博客|作品链接|个人网站", "basic.homepage"),
    ("*", r"求职意向|意向岗位|应聘岗位|目标职位|职位类别|岗位类别|应聘职位", "basic.job_intention"),
    ("*", r"开发语言|编程语言", "basic.languages"),
    ("*", r"技能|特长", "skills"),
    ("*", r"获奖|荣誉|奖项", "awards"),
    ("*", r"自我评价|自我介绍|个人优势|个人评价", "self_intro"),
    # ---- 英文表单（海外申请）。放在中文规则后面：中文标签里不会出现这些词 ----
    ("education", r"gpa|cgpa|grade point", "education.gpa"),
    ("education", r"degree|qualification|level of education", "education.degree"),
    ("education", r"major|field of study|discipline|concentration|specializ|area of study", "education.major"),
    ("education", r"start|from|begin|enrol", "education.start"),
    ("education", r"graduat|end|until|expected|completion|\bto\b", "education.end"),
    ("education", r"country", "education.country"),
    ("education", r"city|location", "education.city"),
    ("education", r"school|universit|institution|college|academy", "education.school"),
    ("work", r"employment type|job type|type of", "work.kind"),
    ("work", r"department|division|\bteam\b", "work.department"),
    ("work", r"company|employer|organi[sz]ation|firm", "work.company"),
    ("work", r"title|position|role|\bjob\b", "work.title"),
    ("work", r"location|city", "work.city"),
    ("work", r"start|from|begin", "work.start"),
    ("work", r"\bend|until|through|\bto\b", "work.end"),
    ("work", r"description|responsibilit|dut(y|ies)|summary|accomplish|detail", "work.description"),
    ("research", r"institution|lab|organi[sz]ation|university", "research.company"),
    ("research", r"title|role|position", "research.title"),
    ("research", r"start|from", "research.start"),
    ("research", r"\bend|until|\bto\b", "research.end"),
    ("research", r"description|summary|detail", "research.description"),
    ("project", r"role|position", "project.role"),
    ("project", r"name|title|project", "project.name"),
    ("project", r"link|url|website|repo", "project.url"),
    ("project", r"start|from", "project.start"),
    ("project", r"\bend|until|\bto\b", "project.end"),
    ("project", r"description|summary|detail", "project.description"),
    ("paper", r"journal|conference|venue|publisher", "paper.venue"),
    ("paper", r"title", "paper.title"),
    ("paper", r"year|date", "paper.year"),
    ("award", r"description|detail", "awards"),
    ("*", r"address line 2|address 2|apartment|\bapt\b|suite|\bunit\b|emergency contact|reference|referee|ssn|social security|"
          r"cover letter|signature|today.?s date|race|ethnic|veteran|disabilit|sexual orientation|pronoun|"
          r"transgender|protected", IGNORE),
    ("*", r"first\s*name|given\s*name|forename|preferred\s*(first\s*)?name", "basic.first_name"),
    ("*", r"last\s*name|family\s*name|surname", "basic.last_name"),
    ("*", r"^(your |full |legal )*name\b(?!.*(company|school|employer|project|reference))", "basic.name_en"),
    ("*", r"date of birth|birth\s*date|\bdob\b|birthday", "basic.birth_date"),
    ("*", r"\bgender\b|\bsex\b", "basic.gender"),
    ("*", r"phone|mobile|telephone|\bcell\b", "basic.phone"),
    ("*", r"linked\s*in", "basic.linkedin"),
    ("*", r"website|portfolio|personal (site|page)|homepage|blog", "basic.homepage"),
    ("*", r"street|address", "basic.address"),
    ("*", r"\bcity\b|\btown\b", "basic.current_city"),
    ("*", r"\bstate\b|province", "basic.state"),
    ("*", r"zip|postal|post\s*code", "basic.postal_code"),
    ("*", r"nationality|citizenship", "basic.nationality"),
    ("*", r"country|region", "basic.country"),
    ("*", r"authori[sz]ed to work|legally.*work|eligible to work|right to work|work eligibility", "basic.authorized_to_work"),
    ("*", r"sponsor|require.*visa|need.*visa", "basic.need_sponsorship"),
    ("*", r"work authori[sz]ation|visa status|immigration status|employment authori[sz]ation", "basic.work_authorization"),
    ("*", r"salary|compensation|pay expectation", "basic.expected_salary"),
    ("*", r"available|availability|earliest|start date|notice period|when can you start", "basic.available"),
    ("*", r"passport|national id|identification number|id number", "basic.id_number"),
    ("*", r"skills?\b", "skills"),
    ("*", r"awards?\b|honou?rs", "awards"),
    ("*", r"professional summary|about (you|yourself|me)|summary|personal statement", "self_intro"),
    ("*", r"current location|where are you (located|based)|location", "basic.current_city"),
    ("*", r"预计毕业|毕业日期|毕业时间", "education.end"),
    ("*", r"国籍", "basic.nationality"),
]

DEGREE_SYNONYMS = {
    "本科": ["本科", "学士", "大学本科", "Bachelor"],
    "硕士": ["硕士", "硕士研究生", "研究生", "Master"],
    "博士": ["博士", "博士研究生", "PhD"],
}

DEGREE_EN = {
    "本科": ["Bachelor's Degree", "Bachelor's", "Bachelor", "Bachelors", "B.S.", "BS", "Undergraduate"],
    "硕士": ["Master's Degree", "Master's", "Master", "Masters", "M.S.", "MS", "MSc"],
    "博士": ["Doctorate", "Doctor of Philosophy", "PhD", "Ph.D."],
}


def canon_degree(value: str) -> str | None:
    """中文或英文的学历说法 → 本科 / 硕士 / 博士。"""
    v = value.strip().lower()
    for cn, words in (("本科", ("本科", "学士", "bachelor", "b.s", "bsc", "undergrad")),
                      ("硕士", ("硕士", "master", "m.s", "msc", "mba", "研究生")),
                      ("博士", ("博士", "phd", "ph.d", "doctor"))):
        if any(w in v for w in words):
            return cn
    return None


DATE_KEYS = ("start", "end", "birth_date", "date")  # date：论文发表日期


def classify_section(text: str, default: str | None = "basic") -> str | None:
    t = (text or "").lower()
    for name, pat in SECTION_PATTERNS:
        if re.search(pat, t):
            return name
    return default


def classify_trail(trail: list[str], default: str | None = "basic") -> str | None:
    """trail 为字段之前的分区标题，最近的在前；取第一个能识别的。"""
    for t in trail:
        k = classify_section(t, default=None)
        if k:
            return k
    return default


def parse_hint(hint: str) -> tuple[str | None, int | None, str | None]:
    """从 input id 里读出（分区, 段落序号, start/end）。"""
    h = hint or ""
    sec = next((name for name, pat in HINT_SECTIONS if re.search(pat, h, re.I)), None)
    m = re.search(r"[_.\[](\d+)[_.\]]", h)
    idx = int(m.group(1)) if m else None
    attr = "start" if re.search(r"start|begin|from", h, re.I) else "end" if re.search(r"end|to$|finish", h, re.I) else None
    return sec, idx, attr


PROMPT_NOISE = re.compile(r"请?(输入|选择|填写|选填|上传)")  # 「请输入学校」里的「入学」不是入学时间
LONG_LABEL = 30  # 超过这个长度的多半是问句 / 说明文字
BRACKETS = re.compile(r"[（(].*?(?:[）)]|$)")  # 括号里是举例，不是在问它（「…工具（编码工具：Cursor、GitHub Copilot…」）


def match_label(label: str, section: str) -> str | None:
    t = PROMPT_NOISE.sub(" ", (label or "").strip().lower()).strip()
    if len(t) > LONG_LABEL:
        t = BRACKETS.sub(" ", t).strip()
    if not t:
        return None

    def hit(pat: str) -> bool:
        return bool(re.search(pat, t))

    for sec, pat, key in LABEL_RULES:
        if sec == section and hit(pat):
            return key
    for sec, pat, key in LABEL_RULES:
        if sec == "*" and hit(pat):
            return key
    return None


def format_date(value: str, kind: str, placeholder: str = "") -> str:
    """value 是 YYYY-MM 或 YYYY-MM-DD；按控件类型和占位符换格式。"""
    m = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$", value.strip())
    if not m:
        return value
    y, mo, d = m.group(1), m.group(2).zfill(2), (m.group(3) or "01").zfill(2)
    if kind == "date":
        return f"{y}-{mo}-{d}"
    if kind == "month":
        return f"{y}-{mo}"
    p = placeholder or ""
    m_us = re.match(r"\s*m{1,2}(\W)(?:d{1,2}\W)?y{2,4}", p, re.I)  # 海外常见的 MM/YYYY、MM/DD/YYYY
    if m_us:
        sep = m_us.group(1)
        return sep.join([mo] + ([d] if re.search(r"d{1,2}", p, re.I) else []) + [y])
    has_day = bool(re.search(r"dd|日|\d{4}\D\d{1,2}\D\d{1,2}", p, re.I))
    if "年" in p:
        return f"{y}年{mo}月" + (f"{d}日" if has_day else "")
    for sep in ("/", "."):
        if sep in p:
            return sep.join([y, mo] + ([d] if has_day else []))
    return f"{y}-{mo}" + (f"-{d}" if has_day else "")


PROVINCES = [
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建",
    "江西", "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海",
    "台湾", "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
]
MUNICIPALITIES = {"北京", "天津", "上海", "重庆"}
REGION_KEYS = {"basic.hometown", "basic.current_city", "basic.expected_city", "work.city"}


# 只写了城市时补上省份：地区选择器要先选省才看得到市（北森）
CITY_PROVINCE = {
    "苏州": "江苏", "南京": "江苏", "无锡": "江苏", "常州": "江苏", "南通": "江苏", "扬州": "江苏", "徐州": "江苏",
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江", "绍兴": "浙江", "嘉兴": "浙江",
    "广州": "广东", "深圳": "广东", "东莞": "广东", "佛山": "广东", "珠海": "广东",
    "合肥": "安徽", "芜湖": "安徽", "武汉": "湖北", "长沙": "湖南", "成都": "四川", "西安": "陕西",
    "郑州": "河南", "济南": "山东", "青岛": "山东", "福州": "福建", "厦门": "福建", "南昌": "江西",
    "沈阳": "辽宁", "大连": "辽宁", "长春": "吉林", "哈尔滨": "黑龙江", "石家庄": "河北", "太原": "山西",
    "昆明": "云南", "贵阳": "贵州", "南宁": "广西", "海口": "海南", "兰州": "甘肃", "呼和浩特": "内蒙古",
}


def split_alternatives(value: str) -> list[str]:
    """「新加坡 / 苏州」「新加坡或苏州」是备选（二选一），不是上下级路径。不带空格的「江苏/苏州」仍是路径。"""
    parts = [x.strip() for x in re.split(r"\s+/\s+|\s*或\s*|\s*\|\s*", value or "") if x.strip()]
    return parts or [value]


def split_region(value: str) -> list[str]:
    """「江苏省苏州市」「江苏苏州」「江苏 / 苏州」→ ["江苏…", "苏州…"]，供级联选择逐级匹配。"""
    v = value.strip()
    parts = [p for p in re.split(r"\s*[/／,，\s-]\s*", v) if p]
    if len(parts) > 1:
        return parts
    if v.removesuffix("市") in MUNICIPALITIES:
        return [v.removesuffix("市")]
    found = re.findall(r".+?(?:省|自治区|特别行政区|市|自治州|地区|盟|县|区)", v)
    if found and "".join(found) == v:
        return found
    city = v.removesuffix("市")
    if city in CITY_PROVINCE:
        return [CITY_PROVINCE[city], city]
    for p in sorted(PROVINCES, key=len, reverse=True):
        if v.startswith(p):
            rest = re.sub(r"^(省|市|自治区|壮族自治区|回族自治区|维吾尔自治区|特别行政区)", "", v[len(p):])
            if p in MUNICIPALITIES and (not rest or rest == "市"):
                return [p]
            return [p] + ([rest] if rest else [])
    return [v]


def gpa_scale_candidates(gpa: str) -> list[str]:
    """「3.8/4.0」→ 4分制；「90/100」→ 百分制；写不出分母就只留「其他」。"""
    m = re.search(r"/\s*(\d+(?:\.\d+)?)", str(gpa))
    if not m:
        return ["其他"]
    top = float(m.group(1))
    names = {4: ["4分制", "4.0分制", "4"], 5: ["5分制", "5.0分制", "5"], 100: ["百分制", "100分制", "100"],
             10: ["10分制", "10"]}
    return names.get(int(top) if top == int(top) else round(top), []) + ["其他"]


def candidates_for(key: str, raw: Any, kind: str, placeholder: str = "") -> list[str]:
    """把资料值变成候选列表：文本框取第一个，下拉/单选按顺序去匹配选项，级联选择为逐级路径。"""
    if raw is None or raw == "":
        return []
    attr = key.rpartition(".")[2]
    if key == "basic.expected_city" and isinstance(raw, str):
        cities = [c for c in re.split(r"[，,、/;；\s]+", raw) if c]
        if kind == "cascader":
            return split_region(cities[0])
        return cities if kind in ("select", "radio", "custom_select") else ["、".join(cities)]
    if kind == "cascader" and key in REGION_KEYS:
        return split_region(str(raw))
    if isinstance(raw, bool):
        return ["是", "Yes"] if raw else ["否", "No"]
    value = str(raw)
    if attr in DATE_KEYS:
        return [format_date(value, kind, placeholder)]
    if key == "education.gpa_scale":
        return gpa_scale_candidates(value)
    if attr == "degree":
        cn = canon_degree(value)
        if cn:
            return list(dict.fromkeys([value] + DEGREE_SYNONYMS[cn] + DEGREE_EN[cn]))
        return [value]
    if attr == "study_type":
        return [value, "全日制", "统招", "全日制统招"] if "全日制" in value else [value]
    if key in ("education.major_category", "basic.languages"):
        return [c for c in re.split(r"[，,、/;；\s]+", value) if c]
    if attr == "political_status" and value in ("无", "群众", "无党派"):
        return ["群众", "无党派人士", "无"]
    if attr == "author_order" and value in ("独立作者", "第一作者"):
        return [value, "第一作者", "独立作者", "第一"]
    if attr == "kind" and value == "实习":
        return ["实习", "实习生", "日常实习"]
    if attr == "gender":
        return [value, {"男": "Male", "女": "Female"}.get(value, value)]
    if attr == "english" and kind in ("select", "radio", "custom_select"):
        parts = re.findall(r"[A-Za-z一-鿿\-]+", value)
        return [value] + parts + (["IELTS"] if "雅思" in value else [])
    if attr == "available" and kind in ("select", "radio", "custom_select"):
        return [value, "随时", "立即", "一周内"]
    return [value]


# 分区编辑入口：这些分区资料里没有对应内容，不去打开
SKIP_EXPAND = r"紧急|干部|校内|活动|作品|专利|证书|竞赛|获奖|亲属|附件|照片"
