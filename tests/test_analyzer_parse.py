import json

import pytest

from foodbot.analyzer import AnalysisError, parse_analysis

FOOD = {"is_food": True, "name": "排骨便當", "ingredients": ["白飯", "炸排骨"], "weight_g": 520, "calories_kcal": 850}


def with_(**changes):
    return {**FOOD, **changes}


def test_valid_food_dict():
    a = parse_analysis(FOOD)
    assert a.is_food and a.name == "排骨便當" and a.weight_g == 520 and a.calories_kcal == 850
    assert a.ingredients == ["白飯", "炸排骨"]


def test_valid_json_string_and_code_fence():
    assert parse_analysis(json.dumps(FOOD)).name == "排骨便當"
    assert parse_analysis("```json\n" + json.dumps(FOOD) + "\n```").calories_kcal == 850


def test_not_food_ignores_numbers_and_sets_zero_calories():
    a = parse_analysis({"is_food": False, "name": "馬克杯", "ingredients": [], "weight_g": None, "calories_kcal": None})
    assert not a.is_food and a.name == "馬克杯" and a.calories_kcal == 0 and a.weight_g is None


@pytest.mark.parametrize("raw", ["這不是 JSON", "", "{\"is_food\": true", "[1, 2]", "123", None])
def test_not_valid_json_or_not_object(raw):
    with pytest.raises(AnalysisError):
        parse_analysis(raw)


@pytest.mark.parametrize("bad", ["約650", "很多", "", None, True, float("nan"), float("inf"), -5, 0, 99999, [1], {"a": 1}])
def test_weight_must_be_a_sane_number(bad):
    with pytest.raises(AnalysisError):
        parse_analysis(with_(weight_g=bad))


@pytest.mark.parametrize("bad", ["約850大卡", "abc", None, False, float("nan"), -1, 5001])
def test_calories_must_be_a_sane_number(bad):
    with pytest.raises(AnalysisError):
        parse_analysis(with_(calories_kcal=bad))


def test_plain_numeric_text_is_accepted():
    a = parse_analysis(with_(weight_g="520", calories_kcal="850.5"))
    assert a.weight_g == 520 and a.calories_kcal == 850.5


@pytest.mark.parametrize("field", ["is_food", "name", "ingredients", "weight_g", "calories_kcal"])
def test_missing_field(field):
    data = dict(FOOD)
    del data[field]
    with pytest.raises(AnalysisError):
        parse_analysis(data)


@pytest.mark.parametrize("changes", [
    {"is_food": "yes"}, {"is_food": 1}, {"name": ""}, {"name": 123}, {"ingredients": "白飯"}, {"ingredients": [1]},
])
def test_wrong_types(changes):
    with pytest.raises(AnalysisError):
        parse_analysis(with_(**changes))


def test_text_is_cleaned_and_truncated():
    a = parse_analysis(with_(name="排骨\n便當\x00" + "字" * 100, ingredients=["a\tb"] * 30))
    assert "\n" not in a.name and "\x00" not in a.name and len(a.name) <= 60
    assert len(a.ingredients) == 20 and a.ingredients[0] == "a b"
