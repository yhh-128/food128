from helpers import add, run

from gift_agent import report
from gift_agent.storage import GiftStore


def all_texts(cfg):
    store = GiftStore(cfg)
    for n in range(20):
        run(add(store, "111", name=f"禮物{n}"))
    rows = run(store.list_gifts("111"))
    return {
        "welcome": report.welcome_text(),
        "welcome_public": report.welcome_text("<@1001>"),
        "help": report.help_reply(),
        "cabinet": report.cabinet_reply(rows),
    }


def test_before_login_the_texts_say_at_robot_not_at_me(cfg):
    for name, text in all_texts(cfg).items():
        assert "@機器人" in text, name
        assert "@我" not in text and "@agent" not in text, name


def test_after_login_the_texts_use_the_bots_real_mention(cfg):
    report.set_bot_mention(999)
    for name, text in all_texts(cfg).items():
        assert "<@999>" in text, name
        assert "@我" not in text and "@機器人" not in text and "@agent" not in text, name


def test_the_mention_can_be_reset(cfg):
    report.set_bot_mention(999)
    report.set_bot_mention(None)
    assert report.bot_mention() == "@機器人" and "<@999>" not in report.help_reply()


def test_public_welcome_mentions_both_the_new_person_and_the_bot():
    report.set_bot_mention(999)
    text = report.welcome_text("<@1001>")
    assert "歡迎 <@1001>！" in text and "<@999>" in text
    assert text.replace("歡迎 <@1001>！", "歡迎！") == report.welcome_text()  # 跟 /說明 只差有沒有 @人
