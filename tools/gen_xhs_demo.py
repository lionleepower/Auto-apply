"""生成 demo/xhs_style.html：复刻 2026-10-07 在小红书网申页上实测到的结构与怪癖。"""
from pathlib import Path


def item(label, fid, control, req=False):
    lab = ""
    if label:
        lab = (f'<div class="ant-col ant-form-item-label"><label for="{fid}" '
               f'class="{"ant-form-item-required" if req else ""}" title="{label}">{label}</label></div>')
    return (f'<div class="ant-col ant-col-12"><div class="ant-row ant-form-item">{lab}<div class="ant-col ant-form-item-control">'
            f'<div class="ant-form-item-control-input"><div class="ant-form-item-control-input-content">{control}</div></div></div></div></div>')


def text(fid, ph="请输入", attr=""):
    return f'<input class="ant-input" id="{fid}" placeholder="{ph}" {attr}>'


def textarea(fid, ph="请输入", attr=""):
    return f'<textarea class="ant-input" id="{fid}" placeholder="{ph}" {attr}></textarea>'


def select(fid, options, ph="请选择", attr="", cls="", search=False):
    ro = "" if search else "readonly"
    inp = f'<span class="ant-select-selection-search"><input type="search" id="{fid}" autocomplete="off" {ro}></span>'
    return (f'<div class="ant-select w-full ant-select-single {cls}" data-options="{options}" {attr}><div class="ant-select-selector">{inp}'
            f'<span class="ant-select-selection-placeholder">{ph}</span></div></div>')


def picker(fid, ph, attr="", mode="month"):
    return (f'<div class="ant-picker w-full" data-mode="{mode}" {attr}><div class="ant-picker-input">'
            f'<input id="{fid}" readonly placeholder="{ph}"></div></div>')


def section(title, body, add=None):
    head = f'<div class="mb-4"><div class="flex items-center text-h6 font-medium"><div class="bar"></div>{title}</div></div>'
    btn = ""
    if add:
        btn = (f'<button class="ant-btn" type="button" data-add-for="{add}">'
               f'<div class="text-brand-06">添加{title.split("/")[-1]}</div></button>')
    return f'<div class="sec">{head}<div class="flex flex-col gap-4">{body}{btn}</div></div>'


def period(prefix, n, f0, f1, ph0, ph1):
    # 小红书实测：两个日期框共用一个 label，而且 id 都是 ..._startDate
    fid = f"form_item_resumeVo_{prefix}_{n}_startDate"
    # 「至今」勾选后结束框禁用（小红书实测：简历解析会把它勾上）
    ctl = (f'<div class="ant-row"><div class="ant-col ant-col-12">{picker(fid, ph0, f"data-f={f0}")}</div>'
           f'<div class="ant-col ant-col-12">{picker(fid, ph1, f"data-f={f1}")}</div>'
           '<label class="ant-checkbox-wrapper"><input type="checkbox" class="present-toggle"> 至今</label></div>')
    return item("起止时间", fid, ctl, req=True)


def edu_block(n):
    p = f"form_item_resumeVo_resumeEducationInfo_{n}_"
    return ('<div class="block"><div class="ant-row">'
            + period("resumeEducationInfo", n, "start", "end", "入学时间", "毕业时间")
            + item("学历", p + "degree", select(p + "degree", "本科,硕士,博士", attr="data-f=degree"), True)
            + item("学校", p + "school", text(p + "school", "请输入学校全称", "data-f=school"), True)
            + item("学校所在城市", p + "city", text(p + "city", attr="data-f=city"))
            + item("院系", p + "college", text(p + "college", attr="data-f=college"))
            + item("专业", p + "major", text(p + "major", attr="data-f=major"))
            + item("成绩排名", p + "rank", select(p + "rank", "前5%,前10%,前20%,前50%,其他", attr="data-f=rank"))
            + item("导师", p + "tutor", text(p + "tutor", "请输入导师姓名", "data-f=advisor"))
            + item("实验室", p + "lab", text(p + "lab", attr="data-f=lab"))
            + item("研究方向", p + "direction", text(p + "direction", attr="data-f=research"))
            + '</div></div>')


def work_block(n):
    p = f"form_item_resumeVo_resumeInternshipInfo_{n}_"
    return ('<div class="block"><div class="ant-row">'
            + period("resumeInternshipInfo", n, "start", "end", "开始时间", "结束时间")
            + item("公司", p + "company", text(p + "company", attr="data-f=company"))
            + item("部门", p + "department", text(p + "department", attr="data-f=department"))
            + item("职位", p + "position", text(p + "position", attr="data-f=title"))
            + item("工作性质", p + "type", select(p + "type", "实习,全职,兼职", attr="data-f=kind"))
            + item("工作描述", p + "desc", textarea(p + "desc", attr="data-f=desc"))
            + '</div></div>')


def paper_block(n):
    p = f"form_item_resumeVo_resumePaperInfo_{n}_"
    return ('<div class="block"><div class="ant-row">'
            + item("论文名称", p + "name", text(p + "name", attr="data-f=title"))
            + item("发布渠道", p + "channel", select(p + "channel", "Nature,Science,NeurIPS", attr="data-f=venue",
                                                     cls="ant-select-show-search ant-select-auto-complete", search=True))
            + item("作者顺序", p + "order", select(p + "order", "第一作者,第二作者,通讯作者,其他", attr="data-f=order"))
            + '</div></div>')


def award_block(n):
    # 小红书实测：竞赛/奖项的 id 也叫 resumePaperInfo_0_name，和论文重复
    p = f"form_item_resumeVo_resumePaperInfo_{n}_"
    return ('<div class="block"><div class="ant-row">'
            + item("竞赛/奖项名称", p + "name", select(p + "name", "ACM-ICPC,数学建模", attr="data-f=name",
                                                       cls="ant-select-show-search ant-select-auto-complete", search=True))
            + item("获奖名次", p + "rank", select(p + "rank", "一等奖,二等奖,三等奖", attr="data-f=rank"))
            + item("获奖时间", p + "date", picker(p + "date", "获奖时间", "data-f=date"))
            + '</div></div>')


def prefixed_select(key, options, value):
    # 左侧无标签的小下拉（证件类型 / 区号），默认已有值
    return (f'<div class="ant-select ant-select-single" data-options="{options}" data-key="{key}" data-value="{value}">'
            '<div class="ant-select-selector"><span class="ant-select-selection-search"><input type="search" readonly></span>'
            f'<span class="ant-select-selection-item">{value}</span></div></div>')


basic = ('<div class="ant-row">'
         + item("意向工作城市", "form_item_intentionVoList_0_workplaces",
                select("form_item_intentionVoList_0_workplaces", "北京,上海,杭州,苏州,广州", ph="请选择意向城市",
                       attr="data-key=exp_city", cls="ant-select-multiple ant-select-show-search", search=True), True)
         + item("推荐码", "form_item_referCode", text("form_item_referCode", attr="data-key=refer"))
         + item("姓名", "form_item_name", text("form_item_name", attr="data-key=name"), True)
         + item("证件号码（用于实名认证）", "form_item_cardNo",
                '<div style="display:flex;gap:8px">' + prefixed_select("id_type", "身份证,护照,港澳通行证", "身份证")
                + text("form_item_cardNo", "请输入证件号码", "data-key=id_number") + '</div>', True)
         + item("性别", "form_item_gender", select("form_item_gender", "男,女", ph="请选择性别", attr="data-key=gender"), True)
         + item("出生年月", "form_item_birthday", picker("form_item_birthday", "请选择日期", "data-key=birth"), True)
         + item("手机号", "form_item_phone",
                '<div style="display:flex;gap:8px">' + prefixed_select("area", "中国大陆,中国香港,新加坡", "中国大陆")
                + text("form_item_phone", attr='data-key=phone value="13900000000"') + '</div>', True)
         + item("邮箱", "form_item_email", text("form_item_email", attr="data-key=email"), True)
         + '</div>')

edu = f'<div id="edu-list" data-list="edu">{edu_block(0)}</div>'
work = '<div id="work-list" data-list="work"><template>' + work_block(0) + '</template></div>'
paste = textarea("paste", "粘贴论文信息到此处，包括发布渠道、作者顺序、论文名称等，输入后进行内容识别。", "data-key=paste")
paper = f'<div class="paste">{paste}</div><div id="paper-list" data-list="paper">{paper_block(0)}</div>'
award = f'<div id="award-list" data-list="award">{award_block(0)}</div>'
extra = ('<div class="ant-row">'
         + item("Github 链接", "form_item_github", textarea("form_item_github", "请输入所有 Github 链接", "data-key=github"))
         + item("Github 星级", "form_item_star", text("form_item_star", "请输入github 获得过最高的星级", "data-key=star"))
         + '</div>')
cert = '<div class="ant-row">' + item("证书名称", "form_item_cert", text("form_item_cert", "请输入证书名称", "data-key=cert")) + '</div>'
adjust = ('<div role="alert" class="ant-alert"><div class="ant-alert-message">选择服从调剂后，表示愿意接受公司内部其他职位的调剂</div></div>'
          '<div class="ant-row ant-form-item"><div class="ant-col ant-form-item-control">'
          '<div class="ant-radio-group" id="form_item_allowAdjustment" data-key="adjust">'
          '<label class="ant-radio-wrapper"><span class="ant-radio"><input type="radio" class="ant-radio-input" value="true" checked></span><span>是，服从调剂</span></label>'
          '<label class="ant-radio-wrapper"><span class="ant-radio"><input type="radio" class="ant-radio-input" value="false"></span><span>否，不调剂</span></label>'
          '</div></div></div>')

body = (section("基本信息", basic) + section("教育经历", edu, "edu-list") + section("工作/实习经历", work, "work-list")
        + section("论文", paper, "paper-list") + section("竞赛/奖项", award, "award-list") + section("其他", extra)
        + section("证书", cert) + section("是否服从调剂", adjust))

CSS = """
  body { font: 14px/1.6 system-ui, sans-serif; background: #f5f5f5; margin: 0; }
  .page { max-width: 900px; margin: 20px auto; background: #fff; padding: 24px; }
  .font-medium { font-weight: 500; } .text-h6 { font-size: 16px; } .text-h2 { font-size: 22px; }
  .bar { width: 3px; height: 13px; background: #ff2442; margin-right: 8px; display: inline-block; }
  .flex { display: flex; } .flex-col { flex-direction: column; } .gap-4 { gap: 12px; } .items-center { align-items: center; }
  .mb-4 { margin: 20px 0 10px; }
  .ant-row { display: flex; flex-wrap: wrap; gap: 8px 0; } .ant-col-12 { width: 50%; box-sizing: border-box; padding: 0 8px; }
  .ant-form-item-label { font-size: 13px; color: #333; } .ant-form-item-required::before { content: "*"; color: #ff2442; }
  .ant-input { width: 100%; box-sizing: border-box; padding: 4px 8px; border: 1px solid #d9d9d9; border-radius: 4px; font: inherit; }
  .ant-picker, .ant-select { display: flex; flex: 1; align-items: center; border: 1px solid #d9d9d9; border-radius: 4px;
    padding: 4px 8px; cursor: pointer; min-height: 22px; background: #fff; }
  .ant-picker input, .ant-select input { border: 0; outline: 0; font: inherit; width: 100%; background: transparent; }
  .ant-select-selection-search { flex: 1; } .ant-select-selection-placeholder { color: #aaa; }
  .block { background: #fafafa; padding: 12px; border-radius: 6px; }
  .dd { position: absolute; z-index: 1000; background: #fff; border: 1px solid #ddd; box-shadow: 0 6px 16px rgba(0,0,0,.12); padding: 6px; }
  .ant-picker-header { display: flex; gap: 4px; } .ant-picker-header-view { flex: 1; text-align: center; }
  .ant-picker-content td { padding: 4px 6px; cursor: pointer; } .ant-select-item-option { padding: 4px 10px; cursor: pointer; }
  .ant-btn { border: 1px dashed #ccc; background: #fff; padding: 6px; cursor: pointer; }
"""

html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Demo · 小红书结构复刻（Tailwind 标题 / 重复 id / 共用 label）</title>
<!-- 由 tools/gen_xhs_demo.py 生成，不要手改 -->
<style>{CSS}</style>
</head>
<body>
<div class="page">
  <div class="text-h2 font-medium">提交志愿</div>
  <div class="text-h6 font-medium">2027 校园招聘</div>
  {body}
  <button type="button" data-submit>提交</button>
</div>
<script src="antd-widgets.js"></script>
</body>
</html>
"""

if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "demo" / "xhs_style.html"
    out.write_text(html, encoding="utf-8")
    print("written", out)
