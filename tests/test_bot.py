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

CHANNEL = 111
BOT_ID = 999


def attachment(aid, name="gift.png", ctype="image/png", data=None, size=None):
    data = png_bytes() if data is None else data
    return SimpleNamespace(id=aid, filename=name, content_type=ctype, size=size or len(data), read=AsyncMock(return_value=data))


def message(mid, attachments=(), channel=CHANNEL, bot=False, content="", author_id=1001, name="小明", mentions=(), reference=None):
    m = MagicMock()
    m.id = mid
    m.author = SimpleNamespace(id=author_id, bot=bot, display_name=name)
    m.channel = SimpleNamespace(id=channel, fetch_message=AsyncMock())
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
    assert not cfg.excel_path.exists()


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
