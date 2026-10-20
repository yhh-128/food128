import asyncio
import io
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from PIL import Image

from foodbot import bot as bot_module
from foodbot import pipeline
from foodbot.analyzer import Analysis, AnalysisError
from foodbot.bot import FoodBot
from foodbot.config import Config

CHANNEL = 111
FOOD = Analysis(True, "排骨便當", ["白飯"], 520, 850)


def png():
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), "orange").save(buf, "PNG")
    return buf.getvalue()


def attachment(aid, name="food.png", ctype="image/png", data=None, size=None):
    data = png() if data is None else data
    return SimpleNamespace(id=aid, filename=name, content_type=ctype, size=size or len(data), read=AsyncMock(return_value=data))


def message(mid, attachments, channel=CHANNEL, bot=False, content=""):
    m = MagicMock()
    m.id = mid
    m.author = SimpleNamespace(bot=bot, display_name="小明")
    m.channel = SimpleNamespace(id=channel)
    m.attachments = attachments
    m.content = content
    m.created_at = datetime(2026, 10, 20, 4, 30, 15, tzinfo=timezone.utc)
    m.reply, m.add_reaction, m.remove_reaction = AsyncMock(), AsyncMock(), AsyncMock()
    return m


def run(coro_factory, tmp_path, analyze):
    cfg = Config("x", CHANNEL, "sonnet", tmp_path / "data")
    b = FoodBot(cfg)
    orig = pipeline.process_photo

    async def patched(job, cfg_, analyze_=None):
        return await orig(job, cfg_, analyze)

    bot_module.process_photo, saved = patched, bot_module.process_photo
    try:
        async def main():
            b.queue.start()
            await coro_factory(b)
            await b.queue.join()
        asyncio.run(main())
    finally:
        bot_module.process_photo = saved
    return cfg


def fake(result):
    async def analyze(staging, note, cfg):
        if isinstance(result, Exception):
            raise result
        return result
    return analyze


def test_ignores_other_channels_bots_and_non_images(tmp_path):
    msgs = [
        message(1, [attachment(1)], channel=999),
        message(2, [attachment(2)], bot=True),
        message(3, []),
        message(4, [attachment(4, "a.pdf", "application/pdf")]),
        message(5, [attachment(5, "a.gif", "image/gif")]),
    ]

    async def go(b):
        for m in msgs:
            await b.on_message(m)

    run(go, tmp_path, fake(FOOD))
    for m in msgs:
        m.add_reaction.assert_not_called()
        m.reply.assert_not_called()


def test_photo_gets_hourglass_then_reply_then_done(tmp_path):
    m = message(10, [attachment(10)])
    order = []
    m.add_reaction.side_effect = lambda e: order.append(("add", e))
    m.reply.side_effect = lambda t, **k: order.append(("reply", t))
    m.remove_reaction.side_effect = lambda e, u: order.append(("remove", e))

    async def go(b):
        await b.on_message(m)

    run(go, tmp_path, fake(FOOD))
    assert order[0] == ("add", "⏳")
    kinds = [o[0] for o in order]
    assert kinds == ["add", "reply", "remove", "add"]
    assert "當日已攝取熱量" in order[1][1] and order[2] == ("remove", "⏳") and order[3] == ("add", "✅")
    assert m.reply.call_args.kwargs["allowed_mentions"].everyone is False


def test_failure_replies_and_marks_cross(tmp_path):
    m = message(11, [attachment(11)])

    async def go(b):
        await b.on_message(m)

    run(go, tmp_path, fake(AnalysisError("bad")))
    assert m.reply.call_args.args[0] == "分析失敗，請再傳一次"
    assert m.add_reaction.call_args.args[0] == "❌"


def test_two_images_in_one_message_are_processed_one_by_one(tmp_path):
    m = message(12, [attachment(1), attachment(2)])

    async def go(b):
        await b.on_message(m)

    cfg = run(go, tmp_path, fake(FOOD))
    texts = [c.args[0] for c in m.reply.call_args_list]
    assert "第 1 張／共 2 張" in texts[0] and "第 2 張／共 2 張" in texts[1] and "1,700" in texts[1]
    assert [c.args[0] for c in m.add_reaction.call_args_list] == ["⏳", "✅"]  # 全部做完才換圖示
    assert len(list((cfg.photos_dir).glob("*.jpg"))) == 2


def test_missing_reaction_permission_does_not_break_processing(tmp_path):
    m = message(13, [attachment(13)])
    m.add_reaction.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")

    async def go(b):
        await b.on_message(m)

    run(go, tmp_path, fake(FOOD))
    assert "這是食物" in m.reply.call_args.args[0]


def test_broken_download_is_reported_not_fatal(tmp_path):
    bad = attachment(14)
    bad.read = AsyncMock(side_effect=discord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom"))
    good = message(15, [attachment(15)])
    m = message(14, [bad])

    async def go(b):
        await b.on_message(m)
        await b.on_message(good)

    run(go, tmp_path, fake(FOOD))
    assert m.reply.call_args.args[0] == "分析失敗，請再傳一次"
    assert "這是食物" in good.reply.call_args.args[0]  # 後面的照片照常處理


def test_oversized_attachment_rejected(tmp_path):
    m = message(16, [attachment(16, size=bot_module.MAX_IMAGE_BYTES + 1)])

    async def go(b):
        await b.on_message(m)

    run(go, tmp_path, fake(FOOD))
    assert m.reply.call_args.args[0] == "分析失敗，請再傳一次"
