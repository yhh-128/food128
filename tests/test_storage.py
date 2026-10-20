import asyncio
import io
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook
from PIL import Image

from foodbot import storage
from foodbot.storage import ExcelWriteError, ImageError

T = datetime(2026, 10, 20, 12, 30, 15, tzinfo=timezone.utc)


def png_bytes(mode="RGBA"):
    buf = io.BytesIO()
    Image.new(mode, (20, 20), (255, 0, 0, 128) if mode == "RGBA" else "red").save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- 照片


def test_convert_png_to_real_jpeg():
    out = storage.convert_to_jpeg(png_bytes())
    assert Image.open(io.BytesIO(out)).format == "JPEG"


@pytest.mark.parametrize("data", [b"", b"hello", b"%PDF-1.4 not an image"])
def test_convert_rejects_non_images(data):
    with pytest.raises(ImageError):
        storage.convert_to_jpeg(data)


def test_photo_name_format(tmp_path):
    p = storage.make_photo_path(tmp_path / "photos", T, "排骨便當")
    assert p.name == "2026-10-20 12-30-15 排骨便當.jpg"
    assert p.exists()  # 名字先用空檔佔住


def test_same_second_gets_suffix(tmp_path):
    photos = tmp_path / "photos"
    names = [storage.make_photo_path(photos, T, "拉麵").name for _ in range(3)]
    assert names == [
        "2026-10-20 12-30-15 拉麵.jpg",
        "2026-10-20 12-30-15 拉麵-2.jpg",
        "2026-10-20 12-30-15 拉麵-3.jpg",
    ]


@pytest.mark.parametrize("name", [
    "../../evil", "..\\..\\evil", "a/b\\c", "a:b*c?d\"e<f>g|h", "CON", "nul", "com1.txt", "", "   ", "..", "...",
    "\x00\x01\n", "name.", "x" * 200, "C:\\Windows\\System32", "/etc/passwd",
])
def test_hostile_names_stay_inside_photos_dir(tmp_path, name):
    photos = tmp_path / "photos"
    p = storage.make_photo_path(photos, T, name)
    assert p.resolve().parent == photos.resolve()
    assert all(c not in p.name for c in '<>:"/\\|?*')
    assert ".." not in p.name
    assert not p.name.endswith((" .jpg", "..jpg"))
    assert len(p.name) < 80


def test_save_photo_moves_file_and_returns_relative_path(tmp_path):
    staging = tmp_path / "inbox" / "1.jpg"
    staging.parent.mkdir()
    staging.write_bytes(b"jpegdata")
    rel = storage.save_photo(staging, tmp_path, tmp_path / "photos", T, "沙拉")
    assert rel == "photos/2026-10-20 12-30-15 沙拉.jpg"
    assert (tmp_path / rel).read_bytes() == b"jpegdata" and not staging.exists()


# ---------------------------------------------------------------- Excel


@pytest.mark.parametrize("text", ["=1+1", "+1", "-1", "@SUM(A1)", "\t=x", "\r=x", "=HYPERLINK(\"http://x\",\"y\")"])
def test_formula_like_text_becomes_plain_text(text):
    assert storage._prepare_cells([text]) == ["'" + text]


def test_normal_values_untouched():
    assert storage._prepare_cells(["便當", 5, None, 0.5, "a=b", "排骨 -1"]) == ["便當", 5, None, 0.5, "a=b", "排骨 -1"]


def test_illegal_characters_removed():
    assert storage._prepare_cells(["a\x00b\x07c"]) == ["abc"]


def add(path, when=T, who="小明", desc="便當", ing="白飯", w=500, kcal=800, photo="photos/a.jpg"):
    return asyncio.run(storage.append_record(path, when, who, desc, ing, w, kcal, photo))


def test_creates_file_with_header_and_appends_without_overwriting(tmp_path):
    x = tmp_path / "records.xlsx"
    assert add(x, desc="第一餐") == 800
    assert add(x, desc="第二餐", kcal=300) == 1100
    rows = list(load_workbook(x).active.iter_rows(values_only=True))
    assert list(rows[0]) == storage.HEADERS
    assert [r[2] for r in rows[1:]] == ["第一餐", "第二餐"]
    assert [r[6] for r in rows[1:]] == [800, 1100]
    assert rows[1][0] == "2026-10-20 12:30:15" and rows[1][7] == "photos/a.jpg"


def test_daily_total_is_per_local_date_and_not_per_person(tmp_path):
    x = tmp_path / "records.xlsx"
    add(x, who="小明", kcal=400)
    assert add(x, who="小華", kcal=700) == 1100  # 這一版不分人
    assert add(x, when=datetime(2026, 10, 21, 8, 0, 0, tzinfo=timezone.utc), kcal=250) == 250  # 隔天重算


def test_non_food_row_adds_zero(tmp_path):
    x = tmp_path / "records.xlsx"
    add(x, kcal=600)
    assert add(x, desc="不是食物", ing="馬克杯", w=None, kcal=0) == 600
    last = list(load_workbook(x).active.iter_rows(values_only=True))[-1]
    assert last[2] == "不是食物" and last[3] == "馬克杯" and last[4] is None and last[5] == 0


def test_formula_text_is_stored_as_text_not_formula(tmp_path):
    x = tmp_path / "records.xlsx"
    add(x, who="=cmd|' /C calc'!A0", desc="=1+1", ing="@x")
    ws = load_workbook(x).active
    for cell in ws[2]:
        assert cell.data_type != "f"
    assert ws["C2"].value == "'=1+1"


def test_retries_when_excel_is_open_then_succeeds(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(storage, "EXCEL_RETRY_SECONDS", 0)
    x = tmp_path / "records.xlsx"
    real_replace, calls = os.replace, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("locked by Excel")
        return real_replace(src, dst)

    monkeypatch.setattr(storage.os, "replace", flaky)
    assert add(x, kcal=500) == 500
    assert calls["n"] == 3
    assert "目前寫不進去" in caplog.text
    assert len(list(load_workbook(x).active.iter_rows(min_row=2))) == 1  # 沒有重複寫入
    assert not list(tmp_path.glob("*.tmp"))


def test_gives_up_with_clear_error_when_always_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "EXCEL_RETRY_SECONDS", 0)
    monkeypatch.setattr(storage, "EXCEL_RETRIES", 3)
    monkeypatch.setattr(storage.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(ExcelWriteError):
        add(tmp_path / "records.xlsx")
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_excel_file_raises_write_error_not_crash(tmp_path):
    x = tmp_path / "records.xlsx"
    x.write_bytes(b"this is not a zip file")
    with pytest.raises(ExcelWriteError):
        add(x)
