import pytest

from gift_agent import scoring


def test_weights_add_up_to_one():
    assert sum(w for _, w, _ in scoring.WEIGHTS) == pytest.approx(1.0)


@pytest.mark.parametrize("subs,expected", [
    ({"value": 10, "effort": 10, "fit": 10, "uniqueness": 10}, 10.0),
    ({"value": 1, "effort": 1, "fit": 1, "uniqueness": 1}, 1.0),
    ({"value": 6, "effort": 9, "fit": 8, "uniqueness": 8}, 7.8),
    ({"value": 5, "effort": 5, "fit": 5, "uniqueness": 5}, 5.0),
    ({"value": 10, "effort": 1, "fit": 1, "uniqueness": 1}, 3.2),  # 2.5 + 0.3 + 0.25 + 0.2 = 3.25，算到小數第一位
])
def test_final_score(subs, expected):
    assert scoring.final_score(subs) == pytest.approx(expected)


def test_same_sub_scores_always_give_same_total():
    subs = {"value": 7, "effort": 4, "fit": 9, "uniqueness": 3}
    assert len({scoring.final_score(subs) for _ in range(5)}) == 1


def test_result_is_clamped_and_one_decimal():
    s = scoring.final_score({"value": 7, "effort": 4, "fit": 9, "uniqueness": 3})
    assert 1.0 <= s <= 10.0 and s == round(s, 1)


def test_explain_mentions_every_weight():
    text = scoring.explain()
    for label in ("價值感 25%", "用心程度 30%", "貼近對方 25%", "獨特性 20%"):
        assert label in text
