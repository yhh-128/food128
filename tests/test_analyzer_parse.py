import json

import pytest

from gift_agent.analyzer import (
    AnalysisError, normalize_change, parse_action, parse_gift_analysis, parse_recommendation, parse_rescore,
)

OK = {
    "status": "ok", "what_is_it": "", "item_name": "手織圍巾", "category": "手作品", "brand": None, "price_twd": 600,
    "confidence": "medium",
    "context": {"occasion": "生日", "recipient": "媽媽", "handmade": True, "budget_twd": 500},
    "sub_scores": {"value": 6, "effort": 9, "fit": 8, "uniqueness": 8}, "reasons": "手織耗時；對象喜好資料不足。",
}


def with_(**changes):
    return {**OK, **changes}


# ---------------------------------------------------------------- 辨識禮物


def test_valid_gift():
    g = parse_gift_analysis(OK)
    assert g.status == "ok" and g.item_name == "手織圍巾" and g.category == "手作品" and g.brand is None
    assert (g.occasion, g.recipient, g.handmade, g.budget_twd, g.price_twd) == ("生日", "媽媽", True, 500.0, 600.0)
    assert g.sub_scores == {"value": 6, "effort": 9, "fit": 8, "uniqueness": 8}


def test_json_string_and_code_fence():
    assert parse_gift_analysis(json.dumps(OK)).item_name == "手織圍巾"
    assert parse_gift_analysis("```json\n" + json.dumps(OK) + "\n```").price_twd == 600


@pytest.mark.parametrize("status", ["not_a_gift", "unreadable"])
def test_not_ok_statuses_skip_the_rest(status):
    g = parse_gift_analysis({"status": status, "what_is_it": "自拍照"})
    assert g.status == status and g.what_is_it == "自拍照"


@pytest.mark.parametrize("raw", ["這不是 JSON", "", "{\"status\": \"ok\"", "[1, 2]", "123", None])
def test_not_valid_json(raw):
    with pytest.raises(AnalysisError):
        parse_gift_analysis(raw)


@pytest.mark.parametrize("changes", [
    {"status": "maybe"}, {"item_name": ""}, {"item_name": 5}, {"category": "珠寶"}, {"confidence": "great"},
    {"sub_scores": "高"}, {"sub_scores": {"value": 6, "effort": 9, "fit": 8}},
    {"sub_scores": {"value": 6, "effort": 9, "fit": 8, "uniqueness": 11}},
    {"sub_scores": {"value": 6, "effort": 0, "fit": 8, "uniqueness": 8}},
    {"sub_scores": {"value": 6, "effort": "很高", "fit": 8, "uniqueness": 8}},
    {"sub_scores": {"value": 6, "effort": 9.5, "fit": 8, "uniqueness": 8}},
    {"price_twd": "約600"}, {"price_twd": -1}, {"price_twd": float("nan")}, {"price_twd": 10**9}, {"price_twd": True},
    {"context": "生日"}, {"context": {"occasion": None, "recipient": None, "handmade": "yes", "budget_twd": None}},
])
def test_invalid_gift_fields(changes):
    with pytest.raises(AnalysisError):
        parse_gift_analysis(with_(**changes))


def test_plain_numeric_text_accepted_and_text_cleaned():
    g = parse_gift_analysis(with_(price_twd="600", item_name="手織\n圍巾\x00" + "字" * 100))
    assert g.price_twd == 600 and "\n" not in g.item_name and len(g.item_name) <= 60


def test_missing_optional_context_values_are_none():
    ctx = {"occasion": None, "recipient": None, "handmade": None, "budget_twd": None}
    g = parse_gift_analysis(with_(context=ctx, price_twd=None, brand=""))
    assert (g.occasion, g.recipient, g.handmade, g.budget_twd, g.price_twd, g.brand) == (None,) * 6


def test_rescore():
    r = parse_rescore({"sub_scores": OK["sub_scores"], "reasons": "改了"})
    assert r.sub_scores["effort"] == 9
    with pytest.raises(AnalysisError):
        parse_rescore({"sub_scores": {"value": 1}, "reasons": "x"})


# ---------------------------------------------------------------- 動作


def test_valid_action_list_with_filters():
    a = parse_action({"action": "list", "recipient": "媽媽", "date_from": "2026-01-01", "category": "手作品"})
    assert (a.kind, a.recipient, a.date_from, a.category) == ("list", "媽媽", "2026-01-01", "手作品")


def test_action_modify_normalizes_values():
    a = parse_action({"action": "modify", "number": 3, "changes": [
        {"field": "handmade", "value": "是"}, {"field": "price_twd", "value": "800"},
        {"field": "gift_date", "value": "2025-12-25"}, {"field": "recipient", "value": "外婆"},
    ]})
    assert a.number == 3
    assert a.changes == {"handmade": True, "price_twd": 800.0, "gift_date": "2025-12-25", "recipient": "外婆"}


@pytest.mark.parametrize("raw", [
    {"action": "export_everything"},
    {"action": "modify", "number": 3, "changes": [{"field": "score", "value": "10"}]},
    {"action": "modify", "number": 3, "changes": [{"field": "user_id", "value": "999"}]},
    {"action": "modify", "number": 3, "changes": [{"field": "status", "value": "有效"}]},
    {"action": "modify", "number": 3, "changes": [{"field": "handmade", "value": "也許"}]},
    {"action": "modify", "number": 3, "changes": [{"field": "gift_date", "value": "昨天"}]},
    {"action": "modify", "number": 3, "changes": [{"field": "gift_date", "value": "2026-13-45"}]},
    {"action": "modify", "number": 3, "changes": [{"field": "price_twd", "value": "很貴"}]},
    {"action": "modify", "number": 3, "changes": [{"field": "category", "value": "珠寶"}]},
    {"action": "modify", "number": 3, "changes": "價格改 800"},
    {"action": "show", "number": "三"},
    {"action": "show", "number": 0},
    {"action": "list", "date_from": "last month"},
    {"action": "stats", "group_by": "everyone"},
])
def test_invalid_actions(raw):
    with pytest.raises(AnalysisError):
        parse_action(raw)


def test_there_is_no_way_to_pick_a_user_in_an_action():
    a = parse_action({"action": "list", "user_id": "999", "owner": "999"})  # 多餘的欄位直接被忽略
    assert not hasattr(a, "user_id") and not hasattr(a, "owner")


def test_numeric_text_number_is_accepted():
    assert parse_action({"action": "show", "number": "3"}).number == 3


@pytest.mark.parametrize("field,value", [("note", "=1+1"), ("item_name", "../../evil"), ("recipient", "@everyone")])
def test_text_changes_pass_through_for_later_sanitising(field, value):
    # 檔名、Excel、Discord 的清洗在後面的防護做；這裡只負責型態與長度
    assert normalize_change(field, value) == value


def test_normalize_change_rejects_unknown_fields():
    for name in ("score", "user_id", "number", "status", "photo", "reply_id"):
        with pytest.raises(AnalysisError):
            normalize_change(name, "x")


# ---------------------------------------------------------------- 推薦

REC = {"suggestions": [
    {"name": "多肉植物", "reason": "好照顧", "price_min_twd": 200, "price_max_twd": 500, "expected_score": 7.5},
], "avoid": None}


def test_valid_recommendation():
    r = parse_recommendation(REC)
    assert r.suggestions[0].name == "多肉植物" and r.suggestions[0].price_max == 500 and r.avoid is None


def test_recommendation_truncates_to_five():
    many = {**REC, "suggestions": REC["suggestions"] * 8}
    assert len(parse_recommendation(many).suggestions) == 5


@pytest.mark.parametrize("bad", [
    {"suggestions": [], "avoid": None},
    {"suggestions": "多肉植物", "avoid": None},
    {"suggestions": [{**REC["suggestions"][0], "price_min_twd": 900}], "avoid": None},
    {"suggestions": [{**REC["suggestions"][0], "expected_score": 11}], "avoid": None},
    {"suggestions": [{**REC["suggestions"][0], "name": ""}], "avoid": None},
])
def test_invalid_recommendation(bad):
    with pytest.raises(AnalysisError):
        parse_recommendation(bad)


@pytest.mark.parametrize("placeholder", ["null", "None", "N/A", "", "   ", None])
def test_placeholder_reasons_get_an_honest_default(placeholder):
    from gift_agent.analyzer import NO_REASON
    assert parse_gift_analysis(with_(reasons=placeholder)).reasons == NO_REASON
    assert parse_rescore({"sub_scores": OK["sub_scores"], "reasons": placeholder}).reasons == NO_REASON
