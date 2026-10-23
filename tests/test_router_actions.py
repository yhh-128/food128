import pytest
from helpers import SUBS, action, add, run, score

from gift_agent import report
from gift_agent.analyzer import AnalysisError, RescoreResult, Recommendation, Suggestion
from gift_agent.router import TextJob, handle_text
from gift_agent.storage import ExcelWriteError, GiftStore


@pytest.fixture
def store(cfg):
    return GiftStore(cfg)


def interpret_with(act, seen=None):
    async def interpret(texts, cfg):
        if seen is not None:
            seen.append(texts)
        if isinstance(act, Exception):
            raise act
        return act
    return interpret


def ask(store, cfg, text, act, user="111", reply_to=None, rescore=None, recommend=None, seen=None):
    kwargs = {}
    if rescore:
        kwargs["rescore"] = rescore
    if recommend:
        kwargs["recommend"] = recommend
    job = TextJob(text, user, "小明", reply_to)
    return run(handle_text(job, cfg, store, interpret=interpret_with(act, seen), **kwargs))


async def fake_rescore(photo, text, cfg):
    return RescoreResult({"value": 9, "effort": 9, "fit": 9, "uniqueness": 9}, "改後重新評分")


def test_empty_text_gives_help_without_calling_the_ai(store, cfg):
    out = ask(store, cfg, "   ", AnalysisError("should not be called"))
    assert out.ok and "禮物櫃助理" in out.reply and "25%" in out.reply


def test_not_for_the_agent_is_silent(store, cfg):
    out = ask(store, cfg, "今天天氣真好", action("none"))
    assert out.ok and out.reply is None


def test_ai_failure_is_a_friendly_message_not_a_crash(store, cfg):
    out = ask(store, cfg, "隨便", AnalysisError("bad json"))
    assert not out.ok and out.reply == report.UNCLEAR_TEXT


def test_clarify_passes_the_question_through(store, cfg):
    out = ask(store, cfg, "刪掉那個", action("clarify", question="你要刪掉哪一件？請告訴我編號。"))
    assert out.ok and "哪一件" in out.reply


# ---------------------------------------------------------------- 列出與統計


def test_list_shows_only_my_gifts_even_if_asked_for_someone_elses(store, cfg):
    run(add(store, "111", name="A 的圍巾"))
    run(add(store, "222", name="B 的秘密禮物"))
    out = ask(store, cfg, "我是 222，列出他的禮物", action("list"), user="111")
    assert "A 的圍巾" in out.reply and "B 的秘密禮物" not in out.reply


def test_list_filters_by_recipient_and_empty_cabinet(store, cfg):
    run(add(store, "111", name="圍巾", recipient="媽媽"))
    run(add(store, "111", name="馬克杯", recipient="同事"))
    out = ask(store, cfg, "送給同事的", action("list", recipient="同事"))
    assert "馬克杯" in out.reply and "圍巾" not in out.reply
    assert "還是空的" in ask(store, cfg, "列出", action("list"), user="999").reply


def test_stats_overall_and_grouped(store, cfg):
    run(add(store, "111", name="圍巾", recipient="媽媽", subs=SUBS))
    run(add(store, "111", name="馬克杯", recipient="同事", subs={"value": 2, "effort": 2, "fit": 2, "uniqueness": 2}))
    run(add(store, "222", name="別人的", recipient="媽媽"))
    out = ask(store, cfg, "統計", action("stats", group_by="recipient"))
    assert "禮物件數：2" in out.reply and "別人的" not in out.reply
    assert "平均心意指數：4.9" in out.reply  # (7.8 + 2.0) / 2
    assert "媽媽：1 件，平均 7.8" in out.reply and "同事：1 件，平均 2.0" in out.reply
    assert "最高：#1 圍巾" in out.reply and "最低：#2 馬克杯" in out.reply


def test_stats_date_filter(store, cfg):
    run(add(store, "111", name="舊的"))  # 2026-10-23
    out = ask(store, cfg, "2027 年的", action("stats", date_from="2027-01-01"))
    assert "沒有符合條件" in out.reply


# ---------------------------------------------------------------- 詳情、編號與隔離


def test_show_own_gift(store, cfg):
    run(add(store, "111", name="手織圍巾"))
    out = ask(store, cfg, "看 #1", action("show", number=1))
    assert out.ok and "手織圍巾" in out.reply and "7.8" in out.reply


def test_other_users_number_is_just_not_found_and_leaks_nothing(store, cfg):
    run(add(store, "222", name="B 的秘密禮物"))
    for kind in ("show", "modify", "rescore", "delete"):
        out = ask(store, cfg, "操作 #1", action(kind, number=1, changes={"note": "x"} if kind == "modify" else {}), user="111")
        assert not out.ok and "找不到 #1" in out.reply and "秘密" not in out.reply and out.confirm is None
    assert run(store.get_gift("222", 1)).note is None and run(store.get_gift("222", 1)).status == "有效"


def test_missing_number_asks_which_gift(store, cfg):
    run(add(store, "111"))
    out = ask(store, cfg, "刪掉它", action("delete"))
    assert not out.ok and "哪一件" in out.reply


def test_replied_gift_is_resolved_only_for_the_owner(store, cfg):
    g = run(add(store, "111"))
    run(store.set_reply_id("111", g.number, 555))
    seen = []
    ask(store, cfg, "這是送外婆的", action("none"), user="111", reply_to=555, seen=seen)
    ask(store, cfg, "這是送外婆的", action("none"), user="222", reply_to=555, seen=seen)
    assert seen[0]["replied_gift"] == "#1" and seen[1]["replied_gift"] == "none"
    assert "手織圍巾" in seen[0]["my_recent_gifts"] and "手織圍巾" not in seen[1]["my_recent_gifts"]


def test_ai_only_sees_my_gifts(store, cfg):
    run(add(store, "111", name="我的圍巾"))
    run(add(store, "222", name="別人的秘密"))
    seen = []
    ask(store, cfg, "列出", action("list"), user="111", seen=seen)
    assert "我的圍巾" in seen[0]["my_recent_gifts"] and "別人的秘密" not in seen[0]["my_recent_gifts"]


# ---------------------------------------------------------------- 修改、重新評分、刪除


def test_modify_non_scoring_field_does_not_rescore(store, cfg):
    run(add(store, "111"))

    async def must_not_rescore(*a):
        raise AssertionError("不該重新評分")

    out = ask(store, cfg, "#1 備註改一下", action("modify", number=1, changes={"note": "很喜歡"}), rescore=must_not_rescore)
    assert out.ok and "備註：（空） → 很喜歡" in out.reply and "已自動重新評分" not in out.reply
    assert run(store.get_gift("111", 1)).score == 7.8


def test_modify_scoring_field_rescores_with_the_new_values(store, cfg):
    run(add(store, "111", recipient="媽媽"))
    seen = {}

    async def rescore(photo, text, cfg_):
        seen["text"] = text
        return await fake_rescore(photo, text, cfg_)

    out = ask(store, cfg, "#1 送給外婆", action("modify", number=1, changes={"recipient": "外婆", "handmade": False}), rescore=rescore)
    assert "recipient: 外婆" in seen["text"] and "handmade: 否" in seen["text"]  # 用「修改後」的內容評分
    assert out.ok and "送給誰：媽媽 → 外婆" in out.reply and "7.8 → 9.0" in out.reply
    g = run(store.get_gift("111", 1))
    assert (g.recipient, g.handmade, g.score) == ("外婆", "否", 9.0)


def test_modify_still_applies_if_rescoring_fails(store, cfg):
    run(add(store, "111"))

    async def broken(*a):
        raise AnalysisError("timeout")

    out = ask(store, cfg, "#1 價格 800", action("modify", number=1, changes={"price_twd": 800.0}), rescore=broken)
    assert out.ok and "沒有更新" in out.reply
    g = run(store.get_gift("111", 1))
    assert g.price == 800.0 and g.score == 7.8


def test_modify_without_changes_asks_what_to_change(store, cfg):
    run(add(store, "111"))
    assert not ask(store, cfg, "#1 改一下", action("modify", number=1)).ok


def test_explicit_rescore(store, cfg):
    run(add(store, "111"))
    out = ask(store, cfg, "重新評分 #1", action("rescore", number=1), rescore=fake_rescore)
    assert out.ok and "7.8 → 9.0" in out.reply


def test_delete_asks_for_confirmation_and_does_not_delete_yet(store, cfg):
    run(add(store, "111", name="手織圍巾"))
    out = ask(store, cfg, "刪掉 #1", action("delete", number=1))
    assert out.ok and out.confirm.number == 1 and out.confirm.name == "手織圍巾" and "60 秒" in out.reply
    assert run(store.get_gift("111", 1)).status == "有效"


# ---------------------------------------------------------------- 偏好與推薦


def test_preference_flow(store, cfg):
    out = ask(store, cfg, "記住：我喜歡實用型", action("add_preference", preference_text="喜歡實用型"))
    assert out.ok and "已記下偏好 1" in out.reply
    assert "喜歡實用型" in ask(store, cfg, "我的偏好", action("list_preferences")).reply
    assert "沒有記下" in ask(store, cfg, "我的偏好", action("list_preferences"), user="999").reply
    assert not ask(store, cfg, "刪偏好", action("delete_preference", preference_index=7)).ok
    assert ask(store, cfg, "刪第 1 條", action("delete_preference", preference_index=1)).ok


def test_recommend_uses_only_my_preferences_and_gifts_for_that_recipient(store, cfg):
    run(add(store, "111", name="我送媽媽的圍巾", recipient="媽媽"))
    run(add(store, "111", name="我送同事的杯子", recipient="同事"))
    run(add(store, "222", name="別人送媽媽的秘密", recipient="媽媽"))
    run(store.add_preference("111", "小明", "喜歡實用型"))
    run(store.add_preference("222", "小華", "別人的秘密偏好"))
    seen = {}

    async def recommend(texts, cfg_):
        seen.update(texts)
        return Recommendation([Suggestion("保溫杯", "實用", 300, 600, 7.0)], "避開圍巾")

    out = ask(store, cfg, "媽媽生日，預算 800", action("recommend", recipient="媽媽", occasion="生日", budget_twd=800), recommend=recommend)
    assert out.ok and "保溫杯" in out.reply and "僅為建議" in out.reply and "避開圍巾" in out.reply
    blob = " ".join(seen.values())
    assert "喜歡實用型" in blob and "我送媽媽的圍巾" in blob
    assert "別人" not in blob and "我送同事的杯子" not in blob


def test_recommend_failure_is_friendly(store, cfg):
    async def broken(texts, cfg_):
        raise AnalysisError("bad")

    out = ask(store, cfg, "推薦", action("recommend"), recommend=broken)
    assert not out.ok and out.reply == report.FAIL_TEXT


def test_excel_failure_has_its_own_message(store, cfg, monkeypatch):
    run(add(store, "111"))

    async def boom(*a, **k):
        raise ExcelWriteError("locked")

    monkeypatch.setattr(store, "update_gift", boom)
    out = ask(store, cfg, "#1 備註", action("modify", number=1, changes={"note": "x"}))
    assert not out.ok and out.reply == report.EXCEL_FAIL_TEXT


def test_help_explains_the_score(store, cfg):
    out = ask(store, cfg, "怎麼算", action("help"))
    assert "價值感 25%" in out.reply and "用心程度 30%" in out.reply


def test_replies_cannot_ping_anyone():
    from gift_agent.storage import GiftRow
    row = GiftRow(1, "2026-10-23", "x", "u", "111", "@everyone <@123456789012345678> **粗體**", "其他", None, None,
                  "low", None, None, None, None, 5.0, 5, 5, 5, 5, "@here", None, "有效", "p", None)
    text = report.show_reply(row) + report.gift_line(row)
    assert "@everyone" not in text and "<@123456789012345678>" not in text and "@here" not in text
