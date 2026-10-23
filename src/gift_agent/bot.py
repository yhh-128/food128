"""Discord 這一側：收訊息、加 ⏳ 反應、排隊、回覆、刪除確認按鈕。只聽 .env 裡 CHANNEL_ID 那一個頻道。

誰的訊息會被處理：
  ・有圖片附件 → 登錄一件禮物
  ・沒有圖片，但提及（@）機器人，或是回覆機器人的某則訊息 → 一句要理解的話
  ・其餘聊天、機器人的訊息、別的頻道 → 一律忽略（也不花 AI 額度）
"""

import logging
import re
from dataclasses import dataclass

import discord

from discord import app_commands

from gift_agent import report
from gift_agent.actions import ConfirmDelete, Outcome
from gift_agent.config import Config
from gift_agent.pipeline import JobQueue, PhotoJob, process_upload
from gift_agent.router import TextJob, handle_text
from gift_agent.storage import ExcelWriteError, GiftStore, NotFound, StorageError

log = logging.getLogger(__name__)

WAIT_EMOJI, DONE_EMOJI, FAIL_EMOJI = "⏳", "✅", "❌"
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_IMAGE_BYTES = 15 * 1024 * 1024
CONFIRM_SECONDS = 60


@dataclass
class _UploadTask:
    message: discord.Message
    attachment: discord.Attachment
    index: int
    count: int
    caption: str


@dataclass
class _TextTask:
    message: discord.Message
    text: str
    reply_to_id: int | None


@dataclass
class _Pending:
    remaining: int
    failed: bool = False


def _is_image(attachment: discord.Attachment) -> bool:
    kind = (attachment.content_type or "").split(";")[0].strip().lower()
    return kind in IMAGE_TYPES or attachment.filename.lower().endswith(IMAGE_SUFFIXES)


class DeleteConfirmView(discord.ui.View):
    """[確認刪除] [取消]：只有發起人按得動；按下去時仍用「按的人」的身分去刪（防護 5 會再擋一次）。"""

    def __init__(self, store: GiftStore, owner_id: int, number: int, name: str, uploader: str):
        super().__init__(timeout=CONFIRM_SECONDS)
        self.store, self.owner_id, self.number, self.name, self.uploader = store, owner_id, number, name, uploader
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("這個確認只有發起的人能按。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="確認刪除", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            row = await self.store.delete_gift(interaction.user.id, self.uploader, self.number, "按下確認刪除")
            text = report.deleted_reply(row)
        except NotFound:
            text = f"找不到 #{self.number}，可能已經刪除了。"
        except ExcelWriteError:
            text = report.EXCEL_FAIL_TEXT
        self.stop()
        await interaction.response.edit_message(content=text, view=None)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content=f"已取消，#{self.number} {report._safe(self.name)} 保持不變。", view=None)

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(content=f"確認逾時，#{self.number} 保持不變。", view=None)
            except discord.HTTPException:
                pass


class GiftBot(discord.Client):
    def __init__(self, cfg: Config):
        intents = discord.Intents.default()
        intents.message_content = True  # 要在 Developer Portal 也打開 Message Content Intent
        super().__init__(intents=intents)
        self.cfg = cfg
        self.store = GiftStore(cfg)
        self.queue = JobQueue(self._handle)
        self._pending: dict[int, _Pending] = {}
        self.tree = app_commands.CommandTree(self)
        self._commands_synced = False

        @self.tree.command(name="禮物櫃", description="查看你自己的禮物櫃（只有你看得到）")
        async def cabinet(interaction: discord.Interaction) -> None:
            await self.show_cabinet(interaction)

        @self.tree.command(name="說明", description="看這個助理怎麼操作（只有你看得到）")
        async def guide(interaction: discord.Interaction) -> None:
            await self.show_guide(interaction)

    async def setup_hook(self) -> None:
        self.queue.start()
        self._remember_own_mention()

    def _remember_own_mention(self) -> None:
        """說明文字裡的「@機器人」要換成機器人真正的提及（顯示成它現在的名字）。"""
        report.set_bot_mention(self.user.id if self.user else None)

    async def on_ready(self) -> None:
        self._remember_own_mention()
        log.info("已上線：%s，只聽頻道 %s", self.user, self.cfg.channel_id)
        channel = self.get_channel(self.cfg.channel_id)
        if channel is None:
            log.warning("看不到 CHANNEL_ID=%s 這個頻道：請確認 bot 已被邀進 Server，且 CHANNEL_ID 是頻道的 ID（不是 Server 的 ID）", self.cfg.channel_id)
            return
        await self._sync_commands(channel.guild)

    async def _sync_commands(self, guild) -> None:
        """把斜線指令登記到這個 Server（只登記一個 Server，所以很快就會出現在選單裡）。"""
        if self._commands_synced:  # on_ready 重新連線時會再觸發一次，不用重複登記
            return
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        except discord.HTTPException as exc:
            log.warning(
                "斜線指令登記失敗（%s）：邀請 bot 時要勾選 applications.commands 範圍，"
                "請照 docs/discord-bot-setup.md 第四節重新授權一次。沒有它仍可用 @機器人 查詢。", exc,
            )
            return
        self._commands_synced = True
        log.info("已登記斜線指令：%s", "、".join(f"/{c.name}" for c in synced) or "（無）")

    # ------------------------------------------------------------ 斜線指令 /禮物櫃

    async def show_cabinet(self, interaction: discord.Interaction) -> None:
        """只列出「按指令的這個人」自己的禮物；回覆只有本人看得到（ephemeral）。不呼叫 AI，不花額度。"""
        if interaction.channel_id != self.cfg.channel_id:
            await interaction.response.send_message(
                f"請到 <#{self.cfg.channel_id}> 使用這個指令。", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=False)
        try:
            rows = await self.store.list_gifts(interaction.user.id)  # 身分只來自 Discord（防護 5 會篩選）
            text = report.cabinet_reply(rows)
            # 第一次出現的人，順便把使用說明放在這則只有本人看得到的回覆裡（不必再公開歡迎一次）
            if await self.store.mark_welcomed(interaction.user.id, str(getattr(interaction.user, "display_name", ""))):
                intro = report.welcome_text() + "\n\n"
                text = intro + text[: report.DISCORD_LIMIT - len(intro)]
        except StorageError as exc:
            log.error("讀取禮物櫃失敗：%s", exc)
            text = report.EXCEL_FAIL_TEXT
        await interaction.followup.send(text, ephemeral=True)

    async def show_guide(self, interaction: discord.Interaction) -> None:
        """/說明：給「跟第一次進來的人一模一樣」的使用說明；回覆只有本人看得到。不呼叫 AI，不花額度。"""
        if interaction.channel_id != self.cfg.channel_id:
            await interaction.response.send_message(
                f"請到 <#{self.cfg.channel_id}> 使用這個指令。", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=False)
        try:  # 已經看過說明了，就不用再在頻道裡公開歡迎一次
            await self.store.mark_welcomed(interaction.user.id, str(getattr(interaction.user, "display_name", "")))
        except StorageError as exc:
            log.warning("無法記錄歡迎狀態（說明仍會照常顯示）：%s", exc)
        await interaction.followup.send(report.welcome_text(), ephemeral=True)

    # ------------------------------------------------------------ 收訊息

    def _strip_mention(self, content: str) -> str:
        return re.sub(rf"<@!?{self.user.id}>", "", content or "").strip()

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.channel.id != self.cfg.channel_id:
            return

        await self._welcome_if_first_time(message)
        caption = self._strip_mention(message.content)
        images = [a for a in message.attachments if _is_image(a)]
        if images:
            self._pending[message.id] = _Pending(remaining=len(images))
            await self._react(message, WAIT_EMOJI)
            for i, attachment in enumerate(images, 1):
                self.queue.put(_UploadTask(message, attachment, i, len(images), caption))
            return

        mentioned = any(u.id == self.user.id for u in message.mentions)
        reply_to_id = await self._replied_bot_message_id(message)
        if not mentioned and reply_to_id is None:
            return  # 一般聊天：完全不理
        self._pending[message.id] = _Pending(remaining=1)
        await self._react(message, WAIT_EMOJI)
        self.queue.put(_TextTask(message, caption, reply_to_id))

    async def _welcome_if_first_time(self, message: discord.Message) -> None:
        """人第一次在這個頻道發言時，說明這個助理怎麼用（每個人只說一次，記在 Excel 的「已歡迎」工作表）。
        用「第一次發言」而不是「加入頻道」，這樣不需要在 Developer Portal 另外打開 Server Members Intent。"""
        author = message.author
        try:
            first = await self.store.mark_welcomed(author.id, author.display_name)
        except StorageError as exc:
            log.warning("無法記錄歡迎狀態，這次先不歡迎：%s", exc)
            return
        if not first:
            return
        try:
            await message.channel.send(
                report.welcome_text(f"<@{author.id}>"),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=[discord.Object(id=author.id)], replied_user=False,
                ),
            )
        except discord.HTTPException:
            log.warning("歡迎訊息送出失敗（請確認 bot 有 Send Messages 權限）")

    async def _replied_bot_message_id(self, message: discord.Message) -> int | None:
        """如果這則訊息是在回覆 agent 自己的某則訊息，回傳那則訊息的編號。"""
        ref = message.reference
        if ref is None or ref.message_id is None:
            return None
        target = ref.resolved if isinstance(ref.resolved, discord.Message) else None
        if target is None:
            try:
                target = await message.channel.fetch_message(ref.message_id)
            except discord.HTTPException:
                return None
        return target.id if target.author.id == self.user.id else None

    # ------------------------------------------------------------ 工作者呼叫的處理

    async def _handle(self, task) -> None:
        message = task.message
        try:
            if isinstance(task, _UploadTask):
                outcome = await self._do_upload(task)
            else:
                outcome = await handle_text(
                    TextJob(task.text, str(message.author.id), message.author.display_name, task.reply_to_id),
                    self.cfg, self.store,
                )
        except Exception:
            log.exception("處理訊息時發生未預期的錯誤")
            outcome = Outcome(False, report.FAIL_TEXT)

        sent = await self._reply(message, outcome)
        if sent is not None and outcome.bind_gift is not None:
            try:
                await self.store.set_reply_id(message.author.id, outcome.bind_gift, sent.id)
            except Exception:
                log.exception("記錄回覆訊息編號失敗（之後回覆這則訊息時找不到對應的禮物）")
        await self._finish(message, outcome)

    async def _do_upload(self, task: _UploadTask) -> Outcome:
        message, attachment = task.message, task.attachment
        if attachment.size > MAX_IMAGE_BYTES:
            return Outcome(False, report.FAIL_TEXT)
        job = PhotoJob(
            image_bytes=await attachment.read(),
            user_id=str(message.author.id),
            uploader=message.author.display_name,
            caption=task.caption,
            sent_at=message.created_at.astimezone(),  # 換成這台電腦的本地時間
            attachment_id=attachment.id,
            index=task.index,
            count=task.count,
        )
        return await process_upload(job, self.cfg, self.store)

    async def _reply(self, message: discord.Message, outcome: Outcome) -> discord.Message | None:
        if outcome.reply is None:
            return None
        view = None
        if outcome.confirm is not None:
            c: ConfirmDelete = outcome.confirm
            view = DeleteConfirmView(self.store, message.author.id, c.number, c.name, message.author.display_name)
        try:
            kwargs = {"view": view} if view is not None else {}
            sent = await message.reply(
                outcome.reply,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=False, replied_user=True),
                **kwargs,
            )
        except discord.HTTPException:
            log.exception("回覆訊息失敗（請確認 bot 有 Send Messages 與 Read Message History 權限）")
            return None
        if view is not None:
            view.message = sent
        return sent

    async def _finish(self, message: discord.Message, outcome: Outcome) -> None:
        pending = self._pending.get(message.id)
        if pending is None:
            return
        pending.remaining -= 1
        pending.failed = pending.failed or not outcome.ok
        if pending.remaining > 0:
            return
        del self._pending[message.id]
        try:
            await message.remove_reaction(WAIT_EMOJI, self.user)
        except discord.HTTPException:
            pass
        if outcome.reply is None and not pending.failed:
            return  # 這句話根本不是對 agent 說的：只拿掉 ⏳，不留 ✅
        await self._react(message, FAIL_EMOJI if pending.failed else DONE_EMOJI)

    async def _react(self, message: discord.Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            log.warning("無法加上 %s 反應：請確認 bot 有 Add Reactions 權限（見 docs/discord-bot-setup.md）", emoji)
