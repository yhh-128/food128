"""Discord 這一側：收訊息、加 ⏳ 反應、排隊、回覆。只聽 .env 裡 CHANNEL_ID 那一個頻道。"""

import logging
from dataclasses import dataclass

import discord

from foodbot import report
from foodbot.config import Config
from foodbot.pipeline import JobQueue, JobResult, PhotoJob, process_photo

log = logging.getLogger(__name__)

WAIT_EMOJI, DONE_EMOJI, FAIL_EMOJI = "⏳", "✅", "❌"
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_IMAGE_BYTES = 15 * 1024 * 1024


@dataclass
class _Task:
    message: discord.Message
    attachment: discord.Attachment
    index: int
    count: int


@dataclass
class _Pending:
    remaining: int
    failed: bool = False


def _is_image(attachment: discord.Attachment) -> bool:
    kind = (attachment.content_type or "").split(";")[0].strip().lower()
    return kind in IMAGE_TYPES or attachment.filename.lower().endswith(IMAGE_SUFFIXES)


class FoodBot(discord.Client):
    def __init__(self, cfg: Config):
        intents = discord.Intents.default()
        intents.message_content = True  # 要在 Developer Portal 也打開 Message Content Intent
        super().__init__(intents=intents)
        self.cfg = cfg
        self.queue = JobQueue(self._handle)
        self._pending: dict[int, _Pending] = {}

    async def setup_hook(self) -> None:
        self.queue.start()

    async def on_ready(self) -> None:
        log.info("已上線：%s，只聽頻道 %s", self.user, self.cfg.channel_id)
        if self.get_channel(self.cfg.channel_id) is None:
            log.warning("看不到 CHANNEL_ID=%s 這個頻道：請確認 bot 已被邀進 Server，且 CHANNEL_ID 沒填錯", self.cfg.channel_id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.channel.id != self.cfg.channel_id:
            return
        images = [a for a in message.attachments if _is_image(a)]
        if not images:
            return

        self._pending[message.id] = _Pending(remaining=len(images))
        await self._react(message, WAIT_EMOJI)
        for i, attachment in enumerate(images, 1):
            self.queue.put(_Task(message, attachment, i, len(images)))

    # ------------------------------------------------------------ 工作者呼叫的處理

    async def _handle(self, task: _Task) -> None:
        message, attachment = task.message, task.attachment
        try:
            if attachment.size > MAX_IMAGE_BYTES:
                result = JobResult(False, report.FAIL_TEXT)
            else:
                job = PhotoJob(
                    image_bytes=await attachment.read(),
                    uploader=message.author.display_name,
                    note=message.content or "",
                    sent_at=message.created_at.astimezone(),  # 換成這台電腦的本地時間
                    attachment_id=attachment.id,
                    index=task.index,
                    count=task.count,
                )
                result = await process_photo(job, self.cfg)
        except Exception:
            log.exception("處理照片時發生未預期的錯誤")
            result = JobResult(False, report.FAIL_TEXT)

        await self._reply(message, result.reply)
        await self._finish(message, result.ok)

    async def _reply(self, message: discord.Message, text: str) -> None:
        try:
            await message.reply(
                text,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=False, replied_user=True),
            )
        except discord.HTTPException:
            log.exception("回覆訊息失敗（請確認 bot 有 Send Messages 與 Read Message History 權限）")

    async def _finish(self, message: discord.Message, ok: bool) -> None:
        pending = self._pending.get(message.id)
        if pending is None:
            return
        pending.remaining -= 1
        pending.failed = pending.failed or not ok
        if pending.remaining > 0:
            return
        del self._pending[message.id]
        try:
            await message.remove_reaction(WAIT_EMOJI, self.user)
        except discord.HTTPException:
            pass
        await self._react(message, FAIL_EMOJI if pending.failed else DONE_EMOJI)

    async def _react(self, message: discord.Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            log.warning("無法加上 %s 反應：請確認 bot 有 Add Reactions 權限（見 docs/discord-bot-setup.md）", emoji)
