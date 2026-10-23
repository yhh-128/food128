import asyncio
import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from helpers import SUBS, action, add, gift_analysis, png_bytes

from gift_agent import bot as bot_module
from gift_agent import pipeline, router
from gift_agent.analyzer import AnalysisError
from gift_agent.bot import DeleteConfirmView, GiftBot
from gift_agent.config import Config
from gift_agent.storage import ExcelWriteError, GiftStore

CHANNEL = 111
BOT_ID = 999


def attachment(aid, name="gift.png", ctype="image/png", data=None, size=None):
    data = png_bytes() if data is None else data
    return SimpleNamespace(id=aid, filename=name, content_type=ctype, size=size or len(data), read=AsyncMock(return_value=data))


def message(mid, attachments=(), channel=CHANNEL, bot=False, content="", author_id=1001, name="小明", mentions=(), reference=None):
    m = MagicMock()
    m.id = mid
    m.author = SimpleNamespace(id=author_id, bot=bot, display_name=name)
    m.channel = SimpleNamespace(id=channel, fetch_message=AsyncMock(), send=AsyncMock())
    m.attachments = list(attachments)
    m.content = content
    m.mentions = list(mentions)
    m.reference = reference
    m.created_at = datetime(2026, 10, 23, 6, 5, 33, tzinfo=timezone.utc)
    m.reply = AsyncMock(return_value=SimpleNamespace(id=5000 + mid, edit=AsyncMock()))
    m.add_reaction, m.remove_reaction = AsyncMock(), AsyncMock()
    return m


BOT_USER = SimpleNamespace(id=BOT_ID)


def run_bot(cfg, steps, analyze=None, interpret=None, rescore=None):
    """steps: async function (bot) → 送進去幾則訊息。回傳 bot，讓測試檢查 bot.store。"""
    b = GiftBot(dataclasses.replace(cfg, channel_id=CHANNEL))
    b._connection.user = BOT_USER
    orig_upload, orig_text = pipeline.process_upload, router.handle_text

    async def upload(job, cfg_, store):
        return await orig_upload(job, cfg_, store, analyze)

    async def text(job, cfg_, store):
        return await orig_text(job, cfg_, store, interpret=interpret, rescore=rescore) if rescore else await orig_text(job, cfg_, store, interpret=interpret)

    saved = bot_module.process_upload, bot_module.handle_text
    bot_module.process_upload, bot_module.handle_text = upload, text
    try:
        async def main():
            b.queue.start()
            await steps(b)
            await b.queue.join()
        asyncio.run(main())
    finally:
        bot_module.process_upload, bot_module.handle_text = saved
    return b


def ok_analyze(result=None):
    async def analyze(staging, caption, cfg):
        return result or gift_analysis()
    return analyze


def interpret_with(act):
    async def interpret(texts, cfg):
        if isinstance(act, Exception):
            raise act
        return act
    return interpret


def test_ignores_other_channels_bots_chat_and_non_images(cfg):
    msgs = [
        message(1, [attachment(1)], channel=999),
        message(2, [attachment(2)], bot=True),
        message(3, content="今天好冷"),
        message(4, [attachment(4, "a.pdf", "application/pdf")]),
        message(5, [attachment(5, "a.gif", "image/gif")]),
        message(6, content="@someone 嗨", mentions=[SimpleNamespace(id=42)]),
    ]

    async def go(b):
        for m in msgs:
            await b.on_message(m)

    run_bot(cfg, go, analyze=ok_analyze(), interpret=interpret_with(AnalysisError("不該被呼叫")))
    for m in msgs:
        m.add_reaction.assert_not_called()
        m.reply.assert_not_called()


def test_photo_gets_hourglass_then_reply_then_done_and_reply_is_bound_to_the_gift(cfg):
    m = message(10, [attachment(10)], content="送媽媽的生日禮物")
    order = []
    m.add_reaction.side_effect = lambda e: order.append(("add", e))
    m.reply.side_effect = lambda t, **k: (order.append(("reply", t)), SimpleNamespace(id=7777))[1]
    m.remove_reaction.side_effect = lambda e, u: order.append(("remove", e))

    async def go(b):
        await b.on_message(m)

    b = run_bot(cfg, go, analyze=ok_analyze())
    assert [o[0] for o in order] == ["add", "reply", "remove", "add"]
    assert order[0] == ("add", "⏳") and "手織圍巾" in order[1][1] and order[3] == ("add", "✅")
    assert m.reply.call_args.kwargs["allowed_mentions"].everyone is False
    gift = asyncio.run(b.store.find_gift_by_reply("1001", 7777))  # 之後回覆這則訊息就能找到這件禮物
    assert gift is not None and gift.name == "手織圍巾"
    assert asyncio.run(b.store.find_gift_by_reply("1002", 7777)) is None


def test_not_a_gift_marks_cross(cfg):
    from gift_agent.analyzer import GiftAnalysis
    m = message(11, [attachment(11)])

    async def go(b):
        await b.on_message(m)

    run_bot(cfg, go, analyze=ok_analyze(GiftAnalysis(status="not_a_gift", what_is_it="風景")))
    assert "看不出是禮物" in m.reply.call_args.args[0]
    assert m.add_reaction.call_args.args[0] == "❌"
    assert asyncio.run(GiftStore(cfg).list_gifts("1001")) == []  # 沒有建檔


def test_failure_replies_and_marks_cross(cfg):
    m = message(12, [attachment(12)])

    async def analyze(*a):
        raise AnalysisError("bad")

    async def go(b):
        await b.on_message(m)

    run_bot(cfg, go, analyze=analyze)
    assert m.reply.call_args.args[0] == "分析失敗，請再傳一次" and m.add_reaction.call_args.args[0] == "❌"


def test_two_photos_in_one_message_are_handled_one_by_one(cfg):
    m = message(13, [attachment(1), attachment(2)])

    async def go(b):
        await b.on_message(m)

    run_bot(cfg, go, analyze=ok_analyze())
    texts = [c.args[0] for c in m.reply.call_args_list]
    assert "第 1 張／共 2 張" in texts[0] and "第 2 張／共 2 張" in texts[1] and "#2" in texts[1]
    assert [c.args[0] for c in m.add_reaction.call_args_list] == ["⏳", "✅"]  # 全部做完才換圖示


def test_missing_reaction_permission_does_not_break_processing(cfg):
    m = message(14, [attachment(14)])
    m.add_reaction.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")

    async def go(b):
        await b.on_message(m)

    run_bot(cfg, go, analyze=ok_analyze())
    assert "已登錄到你的禮物櫃" in m.reply.call_args.args[0]


def test_broken_download_and_oversized_files_are_reported_not_fatal(cfg):
    bad = attachment(15)
    bad.read = AsyncMock(side_effect=discord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom"))
    m_bad, m_big, m_good = message(15, [bad]), message(16, [attachment(16, size=bot_module.MAX_IMAGE_BYTES + 1)]), message(17, [attachment(17)])

    async def go(b):
        for m in (m_bad, m_big, m_good):
            await b.on_message(m)

    run_bot(cfg, go, analyze=ok_analyze())
    assert m_bad.reply.call_args.args[0] == "分析失敗，請再傳一次" and m_big.reply.call_args.args[0] == "分析失敗，請再傳一次"
    assert "已登錄到你的禮物櫃" in m_good.reply.call_args.args[0]  # 後面的照常處理


# ---------------------------------------------------------------- 日常說話


def test_mention_with_a_question_is_interpreted(cfg):
    async def seed(b):
        await add(b.store, "1001", name="手織圍巾")
        await b.on_message(m)

    m = message(20, content=f"<@{BOT_ID}> 我送過哪些禮物？", mentions=[BOT_USER])
    run_bot(cfg, seed, interpret=interpret_with(action("list")))
    assert "手織圍巾" in m.reply.call_args.args[0]
    assert [c.args[0] for c in m.add_reaction.call_args_list] == ["⏳", "✅"]


def test_mention_only_gives_help_without_calling_the_ai(cfg):
    m = message(21, content=f"<@{BOT_ID}>", mentions=[BOT_USER])

    async def go(b):
        await b.on_message(m)

    run_bot(cfg, go, interpret=interpret_with(AnalysisError("不該被呼叫")))
    assert "禮物櫃助理" in m.reply.call_args.args[0]


def test_reply_to_the_bot_is_interpreted_even_without_mention(cfg):
    target = MagicMock(spec=discord.Message)
    target.id, target.author = 7777, BOT_USER
    m = message(22, content="這是送外婆的", reference=SimpleNamespace(message_id=7777, resolved=target))

    async def go(b):
        await b.on_message(m)

    seen = []

    async def interpret(texts, cfg_):
        seen.append(texts)
        return action("none")

    run_bot(cfg, go, interpret=interpret)
    assert len(seen) == 1 and seen[0]["user_message"] == "這是送外婆的"
    assert m.reply.call_count == 0  # 「none」：不是對 agent 說的，不回覆
    assert [c.args[0] for c in m.remove_reaction.call_args_list] == ["⏳"] and m.add_reaction.call_count == 1  # 只加過 ⏳


def test_reply_to_someone_elses_message_is_ignored(cfg):
    target = MagicMock(spec=discord.Message)
    target.id, target.author = 8888, SimpleNamespace(id=4242)
    m = message(23, content="謝謝", reference=SimpleNamespace(message_id=8888, resolved=target))

    async def go(b):
        await b.on_message(m)

    run_bot(cfg, go, interpret=interpret_with(AnalysisError("不該被呼叫")))
    m.add_reaction.assert_not_called()


def test_delete_flow_replies_with_buttons_and_does_not_delete_until_confirmed(cfg):
    async def go(b):
        await add(b.store, "1001", name="手織圍巾")
        await b.on_message(m)

    m = message(24, content=f"<@{BOT_ID}> 刪掉 #1", mentions=[BOT_USER])
    b = run_bot(cfg, go, interpret=interpret_with(action("delete", number=1)))
    assert isinstance(m.reply.call_args.kwargs["view"], DeleteConfirmView)
    assert asyncio.run(b.store.get_gift("1001", 1)).status == "有效"


# ---------------------------------------------------------------- 確認按鈕


def interaction(user_id):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
    )


def view_for(cfg, owner="1001"):
    async def build():
        store = bot_module.GiftStore(cfg)
        await add(store, "1001", name="手織圍巾")
        await add(store, "1002", name="B 的馬克杯")
        return store, DeleteConfirmView(store, int(owner), 1, "手織圍巾", "小明")
    return build


def test_only_the_requester_can_press_the_buttons(cfg):
    async def go():
        store, view = await view_for(cfg)()
        other = interaction(1002)
        assert await view.interaction_check(other) is False
        other.response.send_message.assert_awaited_once()
        assert await view.interaction_check(interaction(1001)) is True
        view.stop()
    asyncio.run(go())


def test_confirm_deletes_softly_and_cancel_keeps_it(cfg):
    async def go():
        store, view = await view_for(cfg)()
        cancel = interaction(1001)
        await view.cancel.callback(cancel)
        assert (await store.get_gift("1001", 1)).status == "有效" and "已取消" in cancel.response.edit_message.call_args.kwargs["content"]

        store, view = await view_for(cfg)()
        confirm = interaction(1001)
        await view.confirm.callback(confirm)
        assert await store.get_gift("1001", 1) is None
        assert (await store.get_gift("1001", 1, include_deleted=True)).status == "已刪除"
        assert "修改歷史仍保留" in confirm.response.edit_message.call_args.kwargs["content"]
    asyncio.run(go())


def test_confirm_uses_the_clickers_identity_so_another_users_gift_is_safe(cfg):
    """就算有人繞過「只有發起人能按」的檢查，刪除也是用按的人自己的身分去找，碰不到別人的禮物。"""
    async def go():
        store, view = await view_for(cfg)()
        mallory = interaction(1003)  # 這個人沒有任何禮物
        await view.confirm.callback(mallory)
        assert (await store.get_gift("1001", 1)).status == "有效"
        assert "找不到" in mallory.response.edit_message.call_args.kwargs["content"]
    asyncio.run(go())


# ---------------------------------------------------------------- /禮物櫃 斜線指令


def slash(user_id, channel=CHANNEL):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        channel_id=channel,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def cabinet_bot(cfg):
    return GiftBot(dataclasses.replace(cfg, channel_id=CHANNEL))


def test_cabinet_command_is_registered_with_the_right_name(cfg):
    b = GiftBot(cfg)
    assert [c.name for c in b.tree.get_commands()] == ["禮物櫃"]


def test_cabinet_lists_only_my_gifts_and_only_i_can_see_it(cfg):
    async def go():
        b = cabinet_bot(cfg)
        await add(b.store, "1001", name="我的圍巾")
        await add(b.store, "1001", name="我的馬克杯", subs={"value": 2, "effort": 2, "fit": 2, "uniqueness": 2})
        await add(b.store, "1002", name="別人的秘密禮物")
        mine = slash(1001)
        await b.show_cabinet(mine)
        mine.response.defer.assert_awaited_once()
        assert mine.response.defer.call_args.kwargs["ephemeral"] is True
        text = mine.followup.send.call_args.args[0]
        assert mine.followup.send.call_args.kwargs["ephemeral"] is True  # 只有本人看得到
        assert "我的圍巾" in text and "我的馬克杯" in text and "別人的秘密禮物" not in text
        assert "共 2 件，平均心意指數 4.9" in text
        other = slash(1002)
        await b.show_cabinet(other)
        text2 = other.followup.send.call_args.args[0]
        assert "別人的秘密禮物" in text2 and "我的圍巾" not in text2
    asyncio.run(go())


def test_cabinet_for_someone_without_gifts_and_deleted_gifts_are_hidden(cfg):
    async def go():
        b = cabinet_bot(cfg)
        g = await add(b.store, "1001", name="已刪除的禮物")
        await b.store.delete_gift("1001", "小明", g.number)
        for uid in (1001, 4242):
            i = slash(uid)
            await b.show_cabinet(i)
            text = i.followup.send.call_args.args[0]
            assert "還是空的" in text and "已刪除的禮物" not in text
    asyncio.run(go())


def test_cabinet_only_works_in_the_agents_channel(cfg):
    async def go():
        b = cabinet_bot(cfg)
        await add(b.store, "1001", name="我的圍巾")
        i = slash(1001, channel=555)
        await b.show_cabinet(i)
        i.response.send_message.assert_awaited_once()
        assert i.response.send_message.call_args.kwargs["ephemeral"] is True
        assert f"<#{CHANNEL}>" in i.response.send_message.call_args.args[0]
        i.followup.send.assert_not_called()
    asyncio.run(go())


def test_long_cabinet_is_trimmed_with_a_hint(cfg):
    async def go():
        b = cabinet_bot(cfg)
        for n in range(20):
            await add(b.store, "1001", name=f"禮物{n}")
        i = slash(1001)
        await b.show_cabinet(i)
        text = i.followup.send.call_args.args[0]
        assert "共 20 件" in text and "還有 5 件沒列出" in text and len(text) < 2000
    asyncio.run(go())


def test_cabinet_survives_a_read_error(cfg, monkeypatch):
    async def go():
        b = cabinet_bot(cfg)

        async def boom(uid):
            raise ExcelWriteError("壞掉了")

        monkeypatch.setattr(b.store, "list_gifts", boom)
        i = slash(1001)
        await b.show_cabinet(i)
        assert i.followup.send.call_args.args[0] == "紀錄暫時寫不進去，請稍後再試"
    asyncio.run(go())


def test_commands_are_synced_once_to_the_agents_server(cfg):
    async def go():
        b = cabinet_bot(cfg)
        guild = SimpleNamespace(id=777)
        b.get_channel = lambda cid: SimpleNamespace(guild=guild) if cid == CHANNEL else None
        b.tree.sync = AsyncMock(return_value=list(b.tree.get_commands()))
        await b.on_ready()
        await b.on_ready()  # 重新連線時 on_ready 會再觸發，不應該重複登記
        b.tree.sync.assert_awaited_once_with(guild=guild)
    asyncio.run(go())


def test_sync_failure_is_a_warning_not_a_crash(cfg, caplog):
    async def go():
        b = cabinet_bot(cfg)
        b.get_channel = lambda cid: SimpleNamespace(guild=SimpleNamespace(id=777))
        b.tree.sync = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "Missing Access"))
        await b.on_ready()
        assert b._commands_synced is False
    asyncio.run(go())
    assert "applications.commands" in caplog.text


# ---------------------------------------------------------------- 第一次出現的人：使用說明


def chat(mid, author_id=1001, name="小明", content="大家好", channel=CHANNEL, bot=False):
    return message(mid, content=content, author_id=author_id, name=name, channel=channel, bot=bot)


def test_first_message_gets_the_three_point_guide_once(cfg):
    first, second = chat(30), chat(31, content="再說一次")

    async def go(b):
        await b.on_message(first)
        await b.on_message(second)

    run_bot(cfg, go, interpret=interpret_with(AnalysisError("不該被呼叫")))
    first.channel.send.assert_awaited_once()
    text = first.channel.send.call_args.args[0]
    assert "<@1001>" in text
    assert "傳一張你送出去的禮物照片" in text and "1～10 分的心意指數" in text  # 1. 傳照片並評分
    assert "/禮物櫃" in text and "自己的禮物櫃" in text  # 2. /禮物櫃
    assert "送禮的對象" in text and "推薦" in text  # 3. 說對象就能推薦
    second.channel.send.assert_not_called()  # 同一個人第二次不再說明


def test_guide_only_pings_the_new_person(cfg):
    m = chat(32, author_id=1001)

    async def go(b):
        await b.on_message(m)

    run_bot(cfg, go)
    allowed = m.channel.send.call_args.kwargs["allowed_mentions"]
    assert allowed.everyone is False and allowed.roles is False
    assert [u.id for u in allowed.users] == [1001]


def test_each_new_person_gets_their_own_guide_and_bots_and_other_channels_do_not(cfg):
    a, b_, bot_msg, elsewhere = chat(33, 1001), chat(34, 1002), chat(35, 7777, bot=True), chat(36, 1003, channel=555)

    async def go(b):
        for m in (a, b_, bot_msg, elsewhere):
            await b.on_message(m)

    run_bot(cfg, go)
    assert a.channel.send.await_count == 1 and b_.channel.send.await_count == 1
    bot_msg.channel.send.assert_not_called()
    elsewhere.channel.send.assert_not_called()


def test_two_quick_messages_still_get_only_one_guide(cfg):
    one, two = chat(37), chat(38)

    async def go(b):
        await asyncio.gather(b.on_message(one), b.on_message(two))

    run_bot(cfg, go)
    assert one.channel.send.await_count + two.channel.send.await_count == 1


def test_guide_is_remembered_after_a_restart(cfg):
    first, after_restart = chat(39), chat(40)

    async def go1(b):
        await b.on_message(first)

    async def go2(b):
        await b.on_message(after_restart)

    run_bot(cfg, go1)
    run_bot(cfg, go2)  # 新的 GiftBot，讀的是同一個 Excel
    first.channel.send.assert_awaited_once()
    after_restart.channel.send.assert_not_called()


def test_guide_comes_before_the_normal_reply_to_a_first_photo(cfg):
    m = message(41, [attachment(41)])
    order = []
    m.channel.send.side_effect = lambda *a, **k: order.append("welcome")
    m.reply.side_effect = lambda *a, **k: (order.append("reply"), SimpleNamespace(id=1))[1]

    async def go(b):
        await b.on_message(m)

    run_bot(cfg, go, analyze=ok_analyze())
    assert order == ["welcome", "reply"]


def test_a_failing_excel_does_not_break_messages_or_spam_guides(cfg, monkeypatch):
    m = chat(42)

    async def go(b):
        async def boom(uid, name):
            raise ExcelWriteError("locked")

        monkeypatch.setattr(b.store, "mark_welcomed", boom)
        await b.on_message(m)

    run_bot(cfg, go)
    m.channel.send.assert_not_called()


def test_a_failing_send_does_not_break_the_normal_reply(cfg):
    m = message(43, [attachment(43)])
    m.channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="x"), "Missing Permissions")

    async def go(b):
        await b.on_message(m)

    run_bot(cfg, go, analyze=ok_analyze())
    assert "已登錄到你的禮物櫃" in m.reply.call_args.args[0]


def test_first_cabinet_command_puts_the_guide_in_the_private_reply(cfg):
    async def go():
        b = cabinet_bot(cfg)
        await add(b.store, "1001", name="我的圍巾")
        first, again = slash(1001), slash(1001)
        first.user.display_name = "小明"
        await b.show_cabinet(first)
        text = first.followup.send.call_args.args[0]
        assert first.followup.send.call_args.kwargs["ephemeral"] is True
        assert "/禮物櫃" in text and "我的圍巾" in text and "歡迎" in text and len(text) <= 2000
        await b.show_cabinet(again)
        assert "歡迎" not in again.followup.send.call_args.args[0]
    asyncio.run(go())


def test_someone_welcomed_by_the_cabinet_command_is_not_welcomed_again_in_chat(cfg):
    async def go():
        b = cabinet_bot(cfg)
        b._connection.user = BOT_USER
        await b.show_cabinet(slash(1001))
        m = chat(44, author_id=1001)
        await b.on_message(m)
        m.channel.send.assert_not_called()
    asyncio.run(go())


def test_long_cabinet_plus_guide_still_fits_in_one_message(cfg):
    async def go():
        b = cabinet_bot(cfg)
        for n in range(20):
            await add(b.store, "1001", name="很長的禮物名稱" * 4 + str(n), recipient="很長的對象名稱" * 3)
        i = slash(1001)
        await b.show_cabinet(i)
        assert len(i.followup.send.call_args.args[0]) <= 2000
    asyncio.run(go())
