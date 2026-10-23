"""Excel（禮物櫃／修改歷史／偏好）與照片的存取。

本檔案裡有三段防護：
  防護 3：禮物名稱當檔名前先清洗
  防護 4：寫進 Excel 的文字不能被當成公式
  防護 5：個人隔離（visible_rows）
每一段都用「開始／結束」註解包起來，註解掉整段，程式仍能執行（只是少了那層保護）。

資料列只能透過 visible_rows(rows, user_id) 取得：這個檔案裡的其他程式、以及 actions／router
拿到的禮物與偏好，全部都已經被它篩過。
"""

import asyncio
import copy
import io
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from PIL import Image, ImageOps

from gift_agent.config import Config

log = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 50_000_000  # 超過就當成壞圖（擋解壓縮炸彈）

EXCEL_RETRIES = 10
EXCEL_RETRY_SECONDS = 3
SHORT_NAME_MAX = 12
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

GIFTS_SHEET, HISTORY_SHEET, PREFS_SHEET, WELCOME_SHEET = "禮物櫃", "修改歷史", "偏好", "已歡迎"
ACTIVE, DELETED = "有效", "已刪除"

# (程式裡的欄位名稱, Excel 表頭)
GIFT_COLUMNS = [
    ("number", "編號"), ("gift_date", "送出日期"), ("logged_at", "登錄時間"), ("uploader", "上傳者"),
    ("user_id", "使用者ID"), ("name", "禮物名稱"), ("category", "種類"), ("brand", "品牌"),
    ("price", "估計價格(新台幣)"), ("confidence", "辨識信心"), ("occasion", "場合"), ("recipient", "送給誰"),
    ("handmade", "是否手作"), ("budget", "預算(新台幣)"), ("score", "心意指數"), ("s_value", "價值感"),
    ("s_effort", "用心程度"), ("s_fit", "貼近對方"), ("s_uniq", "獨特性"), ("reasons", "評分理由"),
    ("note", "備註"), ("status", "狀態"), ("photo", "照片存放位置"), ("reply_id", "回覆訊息ID"),
]
HISTORY_HEADERS = ["時間", "使用者ID", "上傳者", "編號", "動作", "欄位", "舊值", "新值", "使用者原話"]
PREF_COLUMNS = [
    ("user_id", "使用者ID"), ("uploader", "上傳者"), ("number", "序號"),
    ("text", "偏好內容"), ("logged_at", "登錄時間"), ("status", "狀態"),
]

# 使用者可以修改的欄位：英文代號 → (GiftRow 的屬性, 中文名稱)
CHANGE_FIELDS = {
    "item_name": ("name", "禮物名稱"), "category": ("category", "種類"), "brand": ("brand", "品牌"),
    "price_twd": ("price", "估計價格"), "occasion": ("occasion", "場合"), "recipient": ("recipient", "送給誰"),
    "handmade": ("handmade", "是否手作"), "budget_twd": ("budget", "預算"),
    "gift_date": ("gift_date", "送出日期"), "note": ("note", "備註"),
}


class StorageError(Exception):
    pass


class ImageError(StorageError):
    pass


class ExcelWriteError(StorageError):
    pass


class NotFound(StorageError):
    """找不到這件禮物（或這條偏好）。對使用者來說，別人的編號也等於「找不到」。"""


@dataclass
class GiftRow:
    number: int
    gift_date: str
    logged_at: str
    uploader: str
    user_id: str
    name: str
    category: str
    brand: str | None
    price: float | None
    confidence: str
    occasion: str | None
    recipient: str | None
    handmade: str | None
    budget: float | None
    score: float
    s_value: int
    s_effort: int
    s_fit: int
    s_uniq: int
    reasons: str
    note: str | None
    status: str
    photo: str
    reply_id: str | None
    row_index: int = 0


@dataclass
class PrefRow:
    user_id: str
    uploader: str
    number: int
    text: str
    logged_at: str
    status: str
    row_index: int = 0


WELCOME_COLUMNS = [("user_id", "使用者ID"), ("uploader", "上傳者"), ("welcomed_at", "歡迎時間")]


@dataclass
class WelcomeRow:
    """「這個人已經收過使用說明了」的紀錄；status 固定是有效，讓它也能經過 visible_rows。"""
    user_id: str
    uploader: str
    welcomed_at: str
    status: str = "有效"


@dataclass
class ScoreUpdate:
    sub_scores: dict[str, int]  # value / effort / fit / uniqueness
    total: float
    reasons: str


# ---------------------------------------------------------------- 照片


def convert_to_jpeg(data: bytes) -> bytes:
    """確認真的是圖片，統一轉成 JPEG（檔名一律 .jpg，不能把 png 偷偷改副檔名）。"""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
            return buf.getvalue()
    except Exception as exc:
        raise ImageError(f"不是可用的圖片：{exc}") from exc


def make_photo_path(user_dir: Path, logged_at: datetime, raw_name: str) -> Path:
    """決定照片要存哪裡：`YYYY-MM-DD hh-mm-ss 名稱.jpg`；同一秒撞名就加 -2、-3。
    會先建立一個空檔把名字佔住，所以不會覆蓋既有的照片。"""
    stamp = logged_at.strftime("%Y-%m-%d %H-%M-%S")
    raw_name = raw_name.strip()[:SHORT_NAME_MAX]  # 「簡稱」：只取前面幾個字
    safe_name = raw_name  # 沒有防護 3：名稱原封不動拿來當檔名

    # ==== 防護 3：禮物名稱當檔名前先清洗 開始 ====
    # 清掉 / \ : * ? " < > | 與控制字元，把 .. 換掉，去掉結尾的點和空白，
    # 避開 Windows 保留名（CON、NUL…），最後確認完整路徑真的落在這位使用者的照片資料夾裡。
    safe_name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", raw_name)
    safe_name = safe_name.replace("..", "_").strip(" .")
    if safe_name.split(".")[0].strip().upper() in _WINDOWS_RESERVED:
        safe_name = "_" + safe_name
    if not safe_name:
        safe_name = "未命名"
    probe = (user_dir / f"{stamp} {safe_name}.jpg").resolve()
    if probe.parent != user_dir.resolve():
        safe_name = "未命名"
    # ==== 防護 3 結束 ====

    user_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        suffix = "" if n == 1 else f"-{n}"
        path = user_dir / f"{stamp} {safe_name}{suffix}.jpg"
        try:
            with open(path, "xb"):
                return path
        except FileExistsError:
            n += 1


def save_photo(staging: Path, cfg: Config, user_id: str, logged_at: datetime, raw_name: str) -> str:
    """把暫存的照片搬到 photos/<使用者ID>/，回傳相對於 DATA_DIR 的路徑（寫進 Excel 用）。"""
    if not str(user_id).isdigit():  # 資料夾名稱只能是 Discord 的數字 ID
        raise StorageError("使用者 ID 格式不對")
    path = make_photo_path(cfg.photos_dir / str(user_id), logged_at, raw_name)
    os.replace(staging, path)
    try:
        return path.relative_to(cfg.data_dir).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- 個人隔離


def visible_rows(rows: list, user_id, include_deleted: bool = False) -> list:
    """所有人的資料列 → 只剩「這位使用者自己」的。禮物櫃與偏好的每一條讀取路徑都必須經過這裡。
    user_id 只能來自 Discord（訊息作者或按鈕的點擊者），不能來自使用者打的字或 AI 的回答。"""
    visible = rows  # 沒有防護 5：回傳所有人的資料

    # ==== 防護 5：個人隔離（只能存取自己的禮物櫃） 開始 ====
    visible = [row for row in rows if row.user_id == str(user_id)]
    # ==== 防護 5 結束 ====

    if not include_deleted:
        visible = [row for row in visible if row.status != DELETED]
    return visible


# ---------------------------------------------------------------- Excel 欄位轉換


def _prepare_cells(values: list) -> list:
    # 基本清理（一直開著，不屬於防護 4）：移除 Excel 檔不允許的看不見字元，免得一個壞字元讓整次寫入失敗
    cells = [ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in values]

    # ==== 防護 4：寫進 Excel 的文字不能被當成公式 開始 ====
    # 文字若以 = + - @（或 Tab、換行）開頭，前面加一個 ' 變成純文字。
    cells = [("'" + v) if isinstance(v, str) and v.startswith(_FORMULA_PREFIXES) else v for v in cells]
    # ==== 防護 4 結束 ====
    return cells


def _str(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _float(value) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _gift_from_values(row_index: int, values: tuple) -> GiftRow | None:
    cells = dict(zip((key for key, _ in GIFT_COLUMNS), values))
    if cells.get("number") in (None, "") or cells.get("user_id") in (None, ""):
        return None
    return GiftRow(
        number=_int(cells["number"]), gift_date=_str(cells["gift_date"]) or "", logged_at=_str(cells["logged_at"]) or "",
        uploader=_str(cells["uploader"]) or "", user_id=_str(cells["user_id"]) or "", name=_str(cells["name"]) or "",
        category=_str(cells["category"]) or "其他", brand=_str(cells["brand"]), price=_float(cells["price"]),
        confidence=_str(cells["confidence"]) or "", occasion=_str(cells["occasion"]), recipient=_str(cells["recipient"]),
        handmade=_str(cells["handmade"]), budget=_float(cells["budget"]), score=_float(cells["score"]) or 0.0,
        s_value=_int(cells["s_value"]), s_effort=_int(cells["s_effort"]), s_fit=_int(cells["s_fit"]),
        s_uniq=_int(cells["s_uniq"]), reasons=_str(cells["reasons"]) or "", note=_str(cells["note"]),
        status=_str(cells["status"]) or ACTIVE, photo=_str(cells["photo"]) or "", reply_id=_str(cells["reply_id"]),
        row_index=row_index,
    )


def _gift_values(row: GiftRow) -> list:
    return [getattr(row, key) for key, _ in GIFT_COLUMNS]


def _all_gifts(wb: Workbook) -> list[GiftRow]:
    """【內部用】所有人的禮物列。外部一律不要直接用，要經過 visible_rows。"""
    ws = wb[GIFTS_SHEET]
    rows = (_gift_from_values(i, tuple(v)) for i, v in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2))
    return [r for r in rows if r is not None]


def _all_prefs(wb: Workbook) -> list[PrefRow]:
    """【內部用】所有人的偏好列。外部一律不要直接用，要經過 visible_rows。"""
    ws = wb[PREFS_SHEET]
    rows = []
    for i, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        cells = dict(zip((key for key, _ in PREF_COLUMNS), values))
        if cells.get("number") in (None, "") or cells.get("user_id") in (None, ""):
            continue
        rows.append(PrefRow(
            user_id=_str(cells["user_id"]) or "", uploader=_str(cells["uploader"]) or "", number=_int(cells["number"]),
            text=_str(cells["text"]) or "", logged_at=_str(cells["logged_at"]) or "",
            status=_str(cells["status"]) or ACTIVE, row_index=i,
        ))
    return rows


def _write_gift(wb: Workbook, row: GiftRow) -> None:
    ws = wb[GIFTS_SHEET]
    for col, value in enumerate(_prepare_cells(_gift_values(row)), start=1):
        ws.cell(row=row.row_index, column=col, value=value)


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _hist_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _log(wb: Workbook, user_id: str, uploader: str, number, action: str, field: str, old, new, said: str) -> None:
    wb[HISTORY_SHEET].append(_prepare_cells([
        _now(), user_id, uploader, number, action, field, _hist_text(old), _hist_text(new), (said or "")[:200],
    ]))


def _open(path: Path) -> Workbook:
    if path.exists():
        wb = load_workbook(path)
    else:
        wb = Workbook()
        wb.remove(wb.active)
    for title, headers in (
        (GIFTS_SHEET, [h for _, h in GIFT_COLUMNS]),
        (HISTORY_SHEET, HISTORY_HEADERS),
        (PREFS_SHEET, [h for _, h in PREF_COLUMNS]),
        (WELCOME_SHEET, [h for _, h in WELCOME_COLUMNS]),
    ):
        if title not in wb.sheetnames:
            wb.create_sheet(title).append(headers)
    return wb


def _score_text(update: ScoreUpdate) -> str:
    s = update.sub_scores
    return f"{update.total:.1f}（價值{s['value']} 用心{s['effort']} 貼近{s['fit']} 獨特{s['uniqueness']}）"


# ---------------------------------------------------------------- 對外的資料存取


class GiftStore:
    """所有 Excel 的讀寫都在這裡。每個方法都需要 user_id（只能來自 Discord）。"""

    def __init__(self, cfg: Config):
        self.path = cfg.excel_path
        self._lock = asyncio.Lock()  # 同一時間只有一個人在改檔案（工作者與按鈕可能同時來）

    # ---- 交易：讀檔 → 修改 → 先寫暫存檔再換掉正式檔；被 Excel 打開時印警告並重試 ----

    def _transact_once(self, fn, save: bool):
        if not save and not self.path.exists():
            return fn(_open(self.path))  # 還沒有檔案：當成空的，不要為了讀取去建檔
        self.path.parent.mkdir(parents=True, exist_ok=True)
        wb = _open(self.path)
        result = fn(wb)
        if save:
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                wb.save(tmp)
                os.replace(tmp, self.path)
            finally:
                tmp.unlink(missing_ok=True)
        return result

    async def _transact(self, fn, save: bool = True):
        async with self._lock:
            for attempt in range(1, EXCEL_RETRIES + 1):
                try:
                    return await asyncio.to_thread(self._transact_once, fn, save)
                except PermissionError:
                    log.warning(
                        "⚠️ %s 目前寫不進去（可能正被 Excel 打開，請先關掉它）。第 %d/%d 次嘗試失敗%s",
                        self.path.name, attempt, EXCEL_RETRIES,
                        f"，{EXCEL_RETRY_SECONDS} 秒後重試…" if attempt < EXCEL_RETRIES else "，放棄這一次。",
                    )
                    if attempt < EXCEL_RETRIES:
                        await asyncio.sleep(EXCEL_RETRY_SECONDS)
                except StorageError:
                    raise
                except Exception as exc:
                    raise ExcelWriteError(f"讀寫 Excel 時發生錯誤：{exc}") from exc
            raise ExcelWriteError("Excel 一直無法寫入")

    # ---- 禮物 ----

    async def add_gift(
        self, user_id, uploader: str, logged_at: datetime, name: str, category: str, brand: str | None,
        price: float | None, confidence: str, occasion: str | None, recipient: str | None, handmade: bool | None,
        budget: float | None, score: ScoreUpdate, photo: str, said: str = "",
    ) -> GiftRow:
        uid = str(user_id)

        def fn(wb):
            mine = visible_rows(_all_gifts(wb), uid, include_deleted=True)
            number = max((r.number for r in mine), default=0) + 1
            when = logged_at.strftime("%Y-%m-%d %H:%M:%S")
            row = GiftRow(
                number=number, gift_date=logged_at.strftime("%Y-%m-%d"), logged_at=when, uploader=uploader, user_id=uid,
                name=name, category=category, brand=brand, price=price, confidence=confidence, occasion=occasion,
                recipient=recipient, handmade=None if handmade is None else ("是" if handmade else "否"), budget=budget,
                score=score.total, s_value=score.sub_scores["value"], s_effort=score.sub_scores["effort"],
                s_fit=score.sub_scores["fit"], s_uniq=score.sub_scores["uniqueness"], reasons=score.reasons,
                note=None, status=ACTIVE, photo=photo, reply_id=None,
            )
            row.row_index = wb[GIFTS_SHEET].max_row + 1
            _write_gift(wb, row)
            _log(wb, uid, uploader, number, "新增", "", "", f"{name}（心意指數 {_score_text(score)}）", said)
            return row

        return await self._transact(fn)

    async def list_gifts(self, user_id) -> list[GiftRow]:
        return await self._transact(lambda wb: visible_rows(_all_gifts(wb), user_id), save=False)

    async def get_gift(self, user_id, number: int, include_deleted: bool = False) -> GiftRow | None:
        rows = await self._transact(lambda wb: visible_rows(_all_gifts(wb), user_id, include_deleted), save=False)
        return next((r for r in rows if r.number == number), None)

    async def find_gift_by_reply(self, user_id, reply_id: int | str) -> GiftRow | None:
        rows = await self._transact(lambda wb: visible_rows(_all_gifts(wb), user_id), save=False)
        return next((r for r in rows if r.reply_id == str(reply_id)), None)

    async def set_reply_id(self, user_id, number: int, reply_id: int | str) -> None:
        def fn(wb):
            row = next((r for r in visible_rows(_all_gifts(wb), user_id, True) if r.number == number), None)
            if row is None:
                raise NotFound(f"找不到 #{number}")
            row.reply_id = str(reply_id)
            _write_gift(wb, row)

        await self._transact(fn)

    async def update_gift(
        self, user_id, uploader: str, number: int, changes: dict[str, object],
        scores: ScoreUpdate | None, said: str,
    ) -> tuple[GiftRow, GiftRow]:
        """修改欄位和（或）重新評分，並在同一次存檔裡寫好修改歷史。回傳（修改前, 修改後）。"""
        uid = str(user_id)

        def fn(wb):
            row = next((r for r in visible_rows(_all_gifts(wb), uid) if r.number == number), None)
            if row is None:
                raise NotFound(f"找不到 #{number}")
            old = copy.copy(row)
            for key, value in changes.items():
                attr, label = CHANGE_FIELDS[key]
                if key == "handmade":
                    value = None if value is None else ("是" if value else "否")
                before = getattr(row, attr)
                setattr(row, attr, value)
                if before != value:
                    _log(wb, uid, uploader, number, "修改", label, before, value, said)
            if scores is not None:
                before = f"{row.score:.1f}（價值{row.s_value} 用心{row.s_effort} 貼近{row.s_fit} 獨特{row.s_uniq}）"
                row.score, row.reasons = scores.total, scores.reasons
                row.s_value, row.s_effort = scores.sub_scores["value"], scores.sub_scores["effort"]
                row.s_fit, row.s_uniq = scores.sub_scores["fit"], scores.sub_scores["uniqueness"]
                _log(wb, uid, uploader, number, "重新評分", "心意指數", before, _score_text(scores), said)
            _write_gift(wb, row)
            return old, row

        return await self._transact(fn)

    async def delete_gift(self, user_id, uploader: str, number: int, said: str = "") -> GiftRow:
        """軟刪除：把狀態改成「已刪除」，列與照片都保留，修改歷史記一筆。"""
        uid = str(user_id)

        def fn(wb):
            row = next((r for r in visible_rows(_all_gifts(wb), uid) if r.number == number), None)
            if row is None:
                raise NotFound(f"找不到 #{number}")
            row.status = DELETED
            _write_gift(wb, row)
            _log(wb, uid, uploader, number, "刪除", "狀態", ACTIVE, DELETED, said)
            return row

        return await self._transact(fn)

    # ---- 第一次出現的人：只歡迎一次 ----

    async def mark_welcomed(self, user_id, uploader: str) -> bool:
        """第一次呼叫回傳 True 並記下來；之後都回傳 False。檢查與記錄在同一次存檔裡完成，所以同時來兩則訊息也只會歡迎一次。"""
        uid = str(user_id)

        def fn(wb):
            ws = wb[WELCOME_SHEET]
            rows = [WelcomeRow(_str(v[0]) or "", _str(v[1]) or "", _str(v[2]) or "")
                    for v in ws.iter_rows(min_row=2, values_only=True) if v and v[0] not in (None, "")]
            if visible_rows(rows, uid):
                return False
            ws.append(_prepare_cells([uid, uploader, _now()]))
            return True

        return await self._transact(fn)

    # ---- 偏好 ----

    async def add_preference(self, user_id, uploader: str, text: str, said: str = "") -> PrefRow:
        uid = str(user_id)

        def fn(wb):
            mine = visible_rows(_all_prefs(wb), uid, include_deleted=True)
            row = PrefRow(uid, uploader, max((r.number for r in mine), default=0) + 1, text, _now(), ACTIVE)
            wb[PREFS_SHEET].append(_prepare_cells([row.user_id, row.uploader, row.number, row.text, row.logged_at, row.status]))
            _log(wb, uid, uploader, "", "新增偏好", f"偏好 {row.number}", "", text, said)
            return row

        return await self._transact(fn)

    async def list_preferences(self, user_id) -> list[PrefRow]:
        return await self._transact(lambda wb: visible_rows(_all_prefs(wb), user_id), save=False)

    async def delete_preference(self, user_id, uploader: str, number: int, said: str = "") -> PrefRow:
        uid = str(user_id)

        def fn(wb):
            row = next((r for r in visible_rows(_all_prefs(wb), uid) if r.number == number), None)
            if row is None:
                raise NotFound(f"找不到偏好 {number}")
            row.status = DELETED
            ws = wb[PREFS_SHEET]
            ws.cell(row=row.row_index, column=[k for k, _ in PREF_COLUMNS].index("status") + 1, value=DELETED)
            _log(wb, uid, uploader, "", "刪除偏好", f"偏好 {number}", row.text, "", said)
            return row

        return await self._transact(fn)
