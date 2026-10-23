"""一張禮物照片從收到到寫入禮物櫃的流程，以及「一次只做一件」的排隊。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gift_agent import analyzer, report, scoring, storage
from gift_agent.actions import Outcome
from gift_agent.analyzer import AnalysisError
from gift_agent.config import Config
from gift_agent.storage import ExcelWriteError, GiftStore, ImageError, ScoreUpdate, StorageError

log = logging.getLogger(__name__)


@dataclass
class PhotoJob:
    image_bytes: bytes
    user_id: str  # 只來自 Discord
    uploader: str
    caption: str  # 上傳者附的說明文字（已拿掉 @agent）
    sent_at: datetime  # 傳照片的時間，已換成這台電腦的本地時間
    attachment_id: int
    index: int
    count: int


async def process_upload(job: PhotoJob, cfg: Config, store: GiftStore, analyze=analyzer.analyze_gift) -> Outcome:
    """辨識 → 算分 → 存照片 → 寫禮物櫃 → 組回覆。預期內的失敗都轉成 Outcome(ok=False)，不丟例外。"""
    staging = cfg.inbox_dir / f"{job.attachment_id}.jpg"
    try:
        cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
        try:
            staging.write_bytes(storage.convert_to_jpeg(job.image_bytes))
        except ImageError as exc:
            log.warning("照片無法使用：%s", exc)
            return Outcome(False, report.FAIL_TEXT)

        try:
            result = await analyze(staging, job.caption, cfg)
        except AnalysisError as exc:
            log.warning("辨識失敗（%s）：%s", job.uploader, exc)
            return Outcome(False, report.FAIL_TEXT)

        if result.status != "ok":
            return Outcome(False, report.not_a_gift_reply(result))

        total = scoring.final_score(result.sub_scores)  # 總分由程式算，不用 AI 給的
        score = ScoreUpdate(result.sub_scores, total, result.reasons)
        photo_rel = storage.save_photo(staging, cfg, job.user_id, job.sent_at, result.item_name)
        try:
            gift = await store.add_gift(
                user_id=job.user_id, uploader=job.uploader, logged_at=job.sent_at, name=result.item_name,
                category=result.category, brand=result.brand, price=result.price_twd, confidence=result.confidence,
                occasion=result.occasion, recipient=result.recipient, handmade=result.handmade,
                budget=result.budget_twd, score=score, photo=photo_rel, said=job.caption,
            )
            mine = await store.list_gifts(job.user_id)
        except ExcelWriteError as exc:
            log.error("Excel 寫入失敗，照片已存在 %s：%s", photo_rel, exc)
            return Outcome(False, report.EXCEL_FAIL_TEXT)

        average = sum(g.score for g in mine) / len(mine)
        reply = report.upload_reply(gift, result, len(mine), average, job.index, job.count)
        return Outcome(True, reply, bind_gift=gift.number)
    except StorageError as exc:
        log.error("儲存時發生錯誤：%s", exc)
        return Outcome(False, report.FAIL_TEXT)
    finally:
        staging.unlink(missing_ok=True)


class JobQueue:
    """先進先出，同一時間只有一個工作者，所以不會同時開很多個 claude -p。"""

    def __init__(self, handler: Callable[[Any], Awaitable[None]]):
        self._handler = handler
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def put(self, job: Any) -> None:
        self._queue.put_nowait(job)

    async def join(self) -> None:
        await self._queue.join()

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._handler(job)
            except Exception:  # 單一工作出錯不能讓整個工作者停下來
                log.exception("處理工作時發生未預期的錯誤")
            finally:
                self._queue.task_done()
