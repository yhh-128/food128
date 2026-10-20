"""Excel 與照片的存取。

本檔案裡有兩段防護：
  防護 3：食物名稱當檔名前先清洗
  防護 4：寫進 Excel 的文字不能被當成公式
每一段都用「開始／結束」註解包起來，註解掉整段，程式仍能執行（只是少了那層保護）。
"""

import asyncio
import io
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from PIL import Image, ImageOps



log = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 50_000_000  # 超過就當成壞圖（擋解壓縮炸彈）

HEADERS = [
    "日期時間", "上傳者", "食物敘述", "成份",
    "重量(公克)", "熱量(大卡)", "當日已攝取熱量(大卡)", "照片存放位置",
]
EXCEL_RETRIES = 10
EXCEL_RETRY_SECONDS = 3
SHORT_NAME_MAX = 12
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class ImageError(Exception):
    pass


class ExcelWriteError(Exception):
    pass


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


def make_photo_path(photos_dir: Path, sent_at: datetime, raw_name: str) -> Path:
    """決定照片要存哪裡：`YYYY-MM-DD hh-mm-ss 名稱.jpg`；同一秒撞名就加 -2、-3。
    會先建立一個空檔把名字佔住，所以不會覆蓋既有的照片。"""
    stamp = sent_at.strftime("%Y-%m-%d %H-%M-%S")
    raw_name = raw_name.strip()[:SHORT_NAME_MAX]  # 「簡稱」：只取前面幾個字
    safe_name = raw_name  # 沒有防護 3：名稱原封不動拿來當檔名

    # ==== 防護 3：食物名稱當檔名前先清洗 開始 ====
    # 清掉 / \ : * ? " < > | 與控制字元，把 .. 換掉，去掉結尾的點和空白，
    # 避開 Windows 保留名（CON、NUL…），最後確認完整路徑真的落在照片資料夾裡。
    safe_name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", raw_name)
    safe_name = safe_name.replace("..", "_").strip(" .")
    if safe_name.split(".")[0].strip().upper() in _WINDOWS_RESERVED:
        safe_name = "_" + safe_name
    if not safe_name:
        safe_name = "未命名"
    probe = (photos_dir / f"{stamp} {safe_name}.jpg").resolve()
    if probe.parent != photos_dir.resolve():
        safe_name = "未命名"
    # ==== 防護 3 結束 ====

    photos_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        suffix = "" if n == 1 else f"-{n}"
        path = photos_dir / f"{stamp} {safe_name}{suffix}.jpg"
        try:
            with open(path, "xb"):
                return path
        except FileExistsError:
            n += 1


def save_photo(staging: Path, data_dir: Path, photos_dir: Path, sent_at: datetime, raw_name: str) -> str:
    """把暫存的照片搬到正式位置，回傳相對於 DATA_DIR 的路徑（寫進 Excel 用）。"""
    path = make_photo_path(photos_dir, sent_at, raw_name)
    os.replace(staging, path)
    try:
        return path.relative_to(data_dir).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- Excel


def _prepare_cells(values: list) -> list:
    # 基本清理（一直開著，不屬於防護 4）：移除 Excel 檔不允許的看不見字元，免得一個壞字元讓整次寫入失敗
    cells = [ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in values]

    # ==== 防護 4：寫進 Excel 的文字不能被當成公式 開始 ====
    # 文字若以 = + - @（或 Tab、換行）開頭，前面加一個 ' 變成純文字。
    cells = [("'" + v) if isinstance(v, str) and v.startswith(_FORMULA_PREFIXES) else v for v in cells]
    # ==== 防護 4 結束 ====
    return cells


def _append_once(
    excel_path: Path, sent_at: datetime, uploader: str, description: str,
    ingredients: str, weight_g: int | None, calories: int, photo_rel: str,
) -> int:
    """在 Excel 最後面加一列，回傳當天累計熱量。整個流程重做不會重複計算（每次都從檔案重新讀）。"""
    excel_path.parent.mkdir(parents=True, exist_ok=True)
    if excel_path.exists():
        wb = load_workbook(excel_path)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "紀錄"
        ws.append(HEADERS)

    date_prefix = sent_at.strftime("%Y-%m-%d")
    total = calories
    for row in ws.iter_rows(min_row=2, values_only=True):
        when, kcal = row[0], row[5] if len(row) > 5 else None
        if isinstance(when, str) and when.startswith(date_prefix) and isinstance(kcal, (int, float)):
            total += kcal
    total = int(total)

    ws.append(_prepare_cells([
        sent_at.strftime("%Y-%m-%d %H:%M:%S"), uploader, description, ingredients,
        weight_g, calories, total, photo_rel,
    ]))

    tmp = excel_path.with_name(excel_path.name + ".tmp")
    try:
        wb.save(tmp)
        os.replace(tmp, excel_path)  # 先寫暫存檔再換掉正式檔，寫到一半當機也不會弄壞舊檔
    finally:
        tmp.unlink(missing_ok=True)
    return total


async def append_record(
    excel_path: Path, sent_at: datetime, uploader: str, description: str,
    ingredients: str, weight_g: int | None, calories: int, photo_rel: str,
) -> int:
    """寫入一筆紀錄；Excel 被打開時印警告並重試，最後還是不行才丟 ExcelWriteError。"""
    for attempt in range(1, EXCEL_RETRIES + 1):
        try:
            return await asyncio.to_thread(
                _append_once, excel_path, sent_at, uploader, description,
                ingredients, weight_g, calories, photo_rel,
            )
        except PermissionError:
            log.warning(
                "⚠️ %s 目前寫不進去（可能正被 Excel 打開，請先關掉它）。第 %d/%d 次嘗試失敗%s",
                excel_path.name, attempt, EXCEL_RETRIES,
                f"，{EXCEL_RETRY_SECONDS} 秒後重試…" if attempt < EXCEL_RETRIES else "，放棄這一筆。",
            )
            if attempt < EXCEL_RETRIES:
                await asyncio.sleep(EXCEL_RETRY_SECONDS)
        except Exception as exc:
            raise ExcelWriteError(f"寫入 Excel 時發生錯誤：{exc}") from exc
    raise ExcelWriteError("Excel 一直無法寫入")
