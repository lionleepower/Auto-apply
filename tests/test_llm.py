import json

from autofill.llm import build_user_prompt, llm_map, parse_mapping
from autofill.pipeline import plan


class FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


FIELDS = [
    {"id": "f1", "label": "Your hobbies and interests", "section": "Personal", "kind": "text", "placeholder": "", "options": []},
    {"id": "f2", "label": "Why us?", "section": "Questions", "kind": "textarea", "placeholder": "", "options": []},
]


def test_parse_valid_and_rejects_unknown_keys():
    m = parse_mapping(json.dumps({"f1": "basic.name", "f2": "basic.made_up_key"}), {"f1", "f2"})
    assert m == {"f1": "basic.name", "f2": None}


def test_parse_bad_json_gives_none():
    assert parse_mapping("我觉得 f1 是姓名", {"f1"}) == {"f1": None}
    assert parse_mapping("[1, 2]", {"f1"}) == {"f1": None}


def test_parse_code_fence():
    assert parse_mapping('```json\n{"f1": "basic.email"}\n```', {"f1"}) == {"f1": "basic.email"}


def test_prompt_contains_no_profile_values(profile):
    prompt = build_user_prompt(FIELDS)
    for secret in (profile.basic.name, profile.basic.phone, profile.basic.email, profile.basic.id_number):
        assert secret not in prompt


def test_llm_only_called_for_unmapped_fields(profile):
    fields = [dict(f, section_kind="basic", index=0) for f in FIELDS]
    fields.append({"id": "f3", "label": "姓名", "section": "基本信息", "section_kind": "basic", "kind": "text",
                   "placeholder": "", "options": [], "index": 0})
    fake = FakeLLM(json.dumps({"f1": "basic.name", "f2": None}))
    items, err = plan(fields, profile, fake)
    assert err is None
    sent = json.loads(fake.calls[0][1])["字段"]
    assert {f["id"] for f in sent} == {"f1", "f2"}
    by_id = {it.id: it for it in items}
    assert (by_id["f1"].key, by_id["f1"].source) == ("basic.name", "llm")
    assert by_id["f2"].key is None
    assert (by_id["f3"].key, by_id["f3"].source) == ("basic.name", "rule")


def test_llm_failure_falls_back_and_is_reported(profile):
    class Boom:
        def complete(self, system, user):
            raise RuntimeError("network down")

    fields = [dict(FIELDS[0], section_kind="basic", index=0)]
    items, err = plan(fields, profile, Boom())
    assert "network down" in err
    assert items[0].key is None


def test_llm_map_empty_does_not_call():
    fake = FakeLLM("{}")
    assert llm_map(fake, []) == {}
    assert fake.calls == []


def _plan_one(profile, label, kind="custom_select", section_kind="basic", options=(), index=0):
    from autofill.pipeline import prepare
    f = {"id": "f1", "label": label, "section": "", "section_kind": section_kind, "kind": kind,
         "placeholder": "", "options": list(options), "value": "", "index": index, "hint": ""}
    items, _ = prepare([f], profile)
    return items[0]


def test_numbered_list_fields_take_successive_values(profile):
    """同一个资料项的第 n 个框取第 n 个值，按出现顺序算。
    讯飞：「擅长的开发语言1」「擅长的其他开发语言2」（编号接着主字段数）；OPPO 式：「意向城市」「其他意向城市1」。"""
    from autofill.pipeline import prepare
    p = profile.model_copy(update={"basic": profile.basic.model_copy(
        update={"expected_city": "苏州，上海，北京", "languages": "Python, C++, C"})})

    def field(i, label):
        return {"id": f"f{i}", "label": label, "section": "", "section_kind": "basic", "kind": "custom_select",
                "placeholder": "", "options": [], "value": "", "index": 0, "hint": ""}

    items, _ = prepare([field(1, "意向工作地点"), field(2, "其他意向城市1"), field(3, "其他意向城市2"),
                        field(4, "擅长的开发语言1"), field(5, "擅长的其他开发语言2")], p)
    got = {it.label: it.candidates for it in items}
    assert got["意向工作地点"] == ["苏州", "上海", "北京"]
    assert got["其他意向城市1"] == ["上海"] and got["其他意向城市2"] == ["北京"]
    assert got["擅长的开发语言1"][0] == "Python" and got["擅长的其他开发语言2"] == ["C++"]


def test_yes_no_options(profile):
    """OPPO：「语言能力：有 / 没有」，资料里有英语成绩就选「有」。"""
    it = _plan_one(profile, "语言能力", kind="radio", options=["没有", "有"])
    assert it.candidates[0] == "有"


def test_fixed_answers_take_priority(profile):
    p = profile.model_copy(update={"answers": {"是否有.*亲属": "否", "国籍": "中国"}})
    it = _plan_one(p, "是否有OPPO亲属", kind="radio", options=["是", "否"])
    assert it.candidates[0] == "否" and it.action == "fill"
    assert _plan_one(p, "国籍（国家/地区）").candidates[0] == "中国"
    # 固定答案的键不会出现在发给 LLM 的目录里
    from autofill.rules import KEY_CATALOG
    assert not any(k.startswith("answer:") for k in KEY_CATALOG)


def test_is_highest_follows_education_order(profile):
    assert _plan_one(profile, "是否为最高学历", kind="radio", section_kind="education", options=["是", "否"],
                     index=0).candidates[0] == "是"
    assert _plan_one(profile, "是否为最高学历", kind="radio", section_kind="education", options=["是", "否"],
                     index=1).candidates[0] == "否"
