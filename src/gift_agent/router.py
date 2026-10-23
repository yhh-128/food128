"""把「一句話」變成「動作」：AI 只負責聽懂，程式才負責檢查與執行。"""

import logging
from dataclasses import dataclass

from gift_agent import actions, analyzer, report
from gift_agent.actions import Actor, Outcome
from gift_agent.analyzer import AnalysisError
from gift_agent.config import Config
from gift_agent.storage import GiftStore, StorageError

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 500


@dataclass
class TextJob:
    text: str  # 已經拿掉 @agent 的使用者原話
    user_id: str  # 只來自 Discord
    uploader: str
    reply_to_id: int | None  # 使用者回覆的是哪一則訊息（若是 agent 的訊息）


async def handle_text(
    job: TextJob, cfg: Config, store: GiftStore,
    interpret=analyzer.interpret_message, rescore=analyzer.rescore_gift, recommend=analyzer.recommend_gifts,
) -> Outcome:
    """一句話 → 動作 → 執行。預期內的失敗都轉成 Outcome(ok=False)，不丟例外。"""
    text = job.text.strip()[:MAX_TEXT_CHARS]
    if not text:
        return Outcome(True, report.help_reply())  # 只有 @agent、沒說別的：不呼叫 AI，直接給說明

    try:
        gifts = await store.list_gifts(job.user_id)  # 經過防護 5：只有這位使用者自己的
        replied = None
        if job.reply_to_id is not None:
            replied = await store.find_gift_by_reply(job.user_id, job.reply_to_id)
        context = {
            "user_message": text,
            "my_recent_gifts": actions.recent_gifts_text(gifts),
            "replied_gift": f"#{replied.number}" if replied else "none",
            "today": actions.today_text(),
        }
        action = await interpret(context, cfg)
    except AnalysisError as exc:
        log.warning("理解訊息失敗（%s）：%s", job.uploader, exc)
        return Outcome(False, report.UNCLEAR_TEXT)
    except StorageError as exc:
        log.error("讀取禮物櫃失敗：%s", exc)
        return Outcome(False, report.EXCEL_FAIL_TEXT)

    actor = Actor(user_id=job.user_id, uploader=job.uploader, said=text)
    return await actions.execute(action, actor, store, cfg, rescore=rescore, recommend=recommend)
