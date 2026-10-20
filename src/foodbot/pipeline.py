"""一張照片從收到到寫入紀錄的流程，以及「一次只做一張」的排隊。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from foodbot import analyzer, report, storage
from foodbot.analyzer import AnalysisError
from foodbot.config import Config
from foodbot.storage import ExcelWriteError, ImageError

log = logging.getLogger(__name__)


@dataclass
class PhotoJob:
    image_bytes: bytes
    uploader: str
    note: str
    sent_at: datetime  # 上傳時間，已換成這台電腦的本地時間
    attachment_id: int
    index: int
    count: int


@dataclass
class JobResult:
    ok: bool
    reply: str


async def process_photo(job: PhotoJob, cfg: Config, analyze=analyzer.analyze_photo) -> JobResult:
    """分析 → 存照片 → 寫 Excel → 組回覆。預期內的失敗都轉成 JobResult(ok=False)，不丟例外。"""
    staging = cfg.inbox_dir / f"{job.attachment_id}.jpg"
    try:
        cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
        try:
            staging.write_bytes(storage.convert_to_jpeg(job.image_bytes))
        except ImageError as exc:
            log.warning("照片無法使用：%s", exc)
            return JobResult(False, report.FAIL_TEXT)

        try:
            result = await analyze(staging, job.note, cfg)
        except AnalysisError as exc:
            log.warning("分析失敗（%s）：%s", job.uploader, exc)
            return JobResult(False, report.FAIL_TEXT)

        if result.is_food:
            description = result.name
            ingredients = "、".join(result.ingredients)
            file_name = result.name
        else:
            description = "不是食物"
            ingredients = result.name  # 成份欄寫「判定出來的東西」
            file_name = "不是食物"
        weight = round(result.weight_g) if result.weight_g is not None else None
        calories = round(result.calories_kcal or 0)

        photo_rel = storage.save_photo(staging, cfg.data_dir, cfg.photos_dir, job.sent_at, file_name)
        try:
            total = await storage.append_record(
                cfg.excel_path, job.sent_at, job.uploader, description, ingredients, weight, calories, photo_rel,
            )
        except ExcelWriteError as exc:
            log.error("Excel 寫入失敗，照片已存在 %s：%s", photo_rel, exc)
            return JobResult(False, report.EXCEL_FAIL_TEXT)

        return JobResult(True, report.format_reply(result, total, job.index, job.count))
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
