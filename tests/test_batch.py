import openpyxl

from autofill.batch import done_companies, load_targets, record, referral_from_url, select


def _xlsx(path, rows):
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    wb.save(path)
    return path


ROW_DJI = ["大疆", "27届秋招", "算法类", "科技",
           "https://app.mokahr.com/m/campus-recruitment/dji/143359?recommendCode=DS8x6aqH#/jobs", "DS8x6aqH", "深圳", "原文"]
ROW_OPPO = ["OPPO", "27届秋招", "开发", "科技", "https://careers.oppo.com/university/oppo/campus/post?shareId=17994",
            "REF123456", "东莞", "原文"]
ROW_BAIDU = ["百度", "27届秋招", "AI", "互联网", "https://dwz.cn/XewbN9xK\n", "IZH1SK", "北京", "原文"]
ROW_NOURL = ["某公司", "27届秋招", "", "科技", None, "ABC", "", ""]
ROW_FEISHU = ["元气森林", "27届秋招", "", "快消", "https://k11pnjpvz1.jobs.feishu.cn/s/EEJSjayEnz0", None, "", ""]


def test_positional_columns_like_users_sheet(tmp_path):
    ts = load_targets(_xlsx(tmp_path / "a.xlsx", [ROW_DJI, ROW_OPPO, ROW_BAIDU, ROW_NOURL, ROW_FEISHU]))
    assert [t.company for t in ts] == ["大疆", "OPPO", "百度", "元气森林"]  # 没链接的跳过
    assert ts[0].ats == "moka" and ts[0].code == "DS8x6aqH"
    assert ts[1].code == "REF123456"  # shareId 不是内推码，用表里的
    assert ts[2].url == "https://dwz.cn/XewbN9xK"  # 单元格里的换行去掉
    assert ts[3].ats == "飞书" and ts[3].code == ""


def test_header_row_is_detected(tmp_path):
    rows = [["链接", "公司名称", "内推码"], [ROW_DJI[4], "大疆", "DS8x6aqH"]]
    ts = load_targets(_xlsx(tmp_path / "b.xlsx", rows))
    assert (ts[0].company, ts[0].code) == ("大疆", "DS8x6aqH")


def test_referral_from_url():
    assert referral_from_url(ROW_DJI[4]) == "DS8x6aqH"
    assert referral_from_url("https://job.xiaohongshu.com/a?referer_code=VEFMJR0CEAUB") == "VEFMJR0CEAUB"
    assert referral_from_url("https://campus.game.163.com/?referralCode=vkL41H") == "vkL41H"
    assert referral_from_url(ROW_OPPO[4]) == ""


def test_select_and_progress_log(tmp_path):
    ts = load_targets(_xlsx(tmp_path / "a.xlsx", [ROW_DJI, ROW_OPPO, ROW_FEISHU]))
    assert [t.company for t in select(ts, ats="moka")] == ["大疆"]
    assert [t.company for t in select(ts, only="OPPO，元气")] == ["OPPO", "元气森林"]
    log = tmp_path / "applications.csv"
    record(log, ts[0], "filled", {"summary": {"filled": 30}, "file": "runs/x.json"})
    record(log, ts[1], "skipped")
    assert done_companies(log) == {"大疆"}


def test_dirty_url_cells():
    from autofill.batch import first_url
    assert first_url("https://campus.g-bits.com📌") == "https://campus.g-bits.com"
    assert first_url("https://jobs.mihoyo.com/?recommendationCode=N4AG&amp;isRecommendation=true#/campus/position") \
        == "https://jobs.mihoyo.com/?recommendationCode=N4AG&isRecommendation=true#/campus/position"
    assert first_url("👉内推链接：\nhttps://dwz.cn/XewbN9xK ，速投") == "https://dwz.cn/XewbN9xK"
    assert referral_from_url("https://jobs.mihoyo.com/?recommendationCode=N4AG&isRecommendation=true") == "N4AG"


def test_title_row_above_header(tmp_path):
    """某份投递表：第一行是一句说明，第二行才是表头，还多了「截止时间」列。"""
    rows = [["本表格提供27届校招内推，在投递过程中务必使用内推"],
            ["企业", "面向人群", "岗位", "行业", "内推链接（直接点击网申即可）", "内推码", "base地", "企业介绍", "截止时间"],
            ROW_DJI + ["2026-10-31"]]
    t = load_targets(_xlsx(tmp_path / "c.xlsx", rows))[0]
    assert (t.company, t.code, t.cities, t.deadline) == ("大疆", "DS8x6aqH", "深圳", "2026-10-31")


def test_target_for_job_url(tmp_path):
    """具体职位的申请链接不带内推码，按域名（共享域名再按公司标识）回表里找。"""
    from autofill.batch import target_for_url
    tesla = ["特斯拉", "", "", "", "https://app.mokahr.com/campus_apply/tesla/41460?recommendCode=DSpMASvh", "DSpMASvh"]
    iflytek = ["科大讯飞", "", "", "", "https://iflytek.zhiye.com/5/jobs?shareId=e623ff68", "ESVT3R"]
    ts = load_targets(_xlsx(tmp_path / "a.xlsx", [ROW_DJI, ROW_OPPO, tesla, iflytek]))
    oppo_job = "https://careers.oppo.com/university/oppo/center/resume?positionId=1880"
    assert target_for_url(ts, oppo_job).code == "REF123456"
    assert target_for_url(ts, "https://iflytek.zhiye.com/form?fromPage=job&jobAdId=71ae").code == "ESVT3R"
    assert target_for_url(ts, "https://app.mokahr.com/campus_apply/dji/143359#/job/abc").company == "大疆"
    assert target_for_url(ts, "https://app.mokahr.com/campus_apply/unknownco/1").__class__ is type(None)
    assert target_for_url(ts, "https://example.com/x") is None
