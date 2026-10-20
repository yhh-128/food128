import asyncio
import io
from datetime import datetime, timezone

import pytest
from openpyxl import load_workbook
from PIL import Image

from foodbot import report, storage
from foodbot.analyzer import Analysis, AnalysisError
from foodbot.config import Config, ConfigError, load_config
from foodbot.pipeline import JobQueue, PhotoJob, process_photo

T = datetime(2026, 10, 20, 12, 30, 15, tzinfo=timezone.utc)


@pytest.fixture
def cfg(tmp_path):
    return Config(discord_token="x", channel_id=1, claude_model="sonnet", data_dir=tmp_path / "data")


def image_bytes(fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), "orange").save(buf, fmt)
    return buf.getvalue()


def make_job(data=None, note="", index=1, count=1, aid=1, who="小明"):
    return PhotoJob(data if data is not None else image_bytes(), who, note, T, aid, index, count)


def fake(result):
    async def analyze(staging, note, cfg):
        assert staging.exists()
        if isinstance(result, Exception):
            raise result
        return result
    return analyze


FOOD = Analysis(True, "排骨便當", ["白飯", "炸排骨"], 520, 850)
MUG = Analysis(False, "馬克杯", [], None, 0)


def rows(cfg):
    return list(load_workbook(cfg.excel_path).active.iter_rows(min_row=2, values_only=True))


def test_food_end_to_end(cfg):
    r = asyncio.run(process_photo(make_job(), cfg, fake(FOOD)))
    assert r.ok
    for part in ("這是食物", "排骨便當", "白飯、炸排骨", "520", "850", "當日已攝取熱量", "850"):
        assert part in r.reply
    row = rows(cfg)[0]
    assert row[1:7] == ("小明", "排骨便當", "白飯、炸排骨", 520, 850, 850)
    assert (cfg.data_dir / row[7]).exists() and row[7] == "photos/2026-10-20 12-30-15 排骨便當.jpg"
    assert not list(cfg.inbox_dir.iterdir())  # 暫存檔已清掉


def test_not_food_is_still_logged_saved_and_replied(cfg):
    asyncio.run(process_photo(make_job(aid=1), cfg, fake(FOOD)))
    r = asyncio.run(process_photo(make_job(aid=2), cfg, fake(MUG)))
    assert r.ok and "你傳的不是食物，是馬克杯" in r.reply and "850" in r.reply
    row = rows(cfg)[1]
    assert row[2] == "不是食物" and row[3] == "馬克杯" and row[5] == 0 and row[6] == 850
    assert (cfg.data_dir / row[7]).exists() and "不是食物" in row[7]


def test_analysis_failure_replies_and_writes_nothing(cfg):
    r = asyncio.run(process_photo(make_job(), cfg, fake(AnalysisError("bad json"))))
    assert not r.ok and r.reply == "分析失敗，請再傳一次"
    assert not cfg.excel_path.exists() and not cfg.photos_dir.exists()


def test_unreadable_image_fails_gracefully(cfg):
    r = asyncio.run(process_photo(make_job(data=b"not an image"), cfg, fake(FOOD)))
    assert not r.ok and r.reply == report.FAIL_TEXT


def test_excel_failure_has_its_own_message(cfg, monkeypatch):
    async def boom(*a, **k):
        raise storage.ExcelWriteError("locked")
    monkeypatch.setattr(storage, "append_record", boom)
    r = asyncio.run(process_photo(make_job(), cfg, fake(FOOD)))
    assert not r.ok and r.reply == report.EXCEL_FAIL_TEXT


def test_same_second_photos_get_different_names_and_running_total(cfg):
    asyncio.run(process_photo(make_job(aid=1, index=1, count=2), cfg, fake(FOOD)))
    r = asyncio.run(process_photo(make_job(aid=2, index=2, count=2), cfg, fake(FOOD)))
    assert "第 2 張／共 2 張" in r.reply and "1,700" in r.reply
    paths = [row[7] for row in rows(cfg)]
    assert len(set(paths)) == 2 and paths[1].endswith("-2.jpg")


def test_hostile_food_name_cannot_escape_photos_dir_or_make_formula(cfg):
    evil = Analysis(True, "=HYPERLINK(\"x\")/../../..\\evil", ["@x"], 100, 100)
    r = asyncio.run(process_photo(make_job(), cfg, fake(evil)))
    assert r.ok
    saved = [p for p in cfg.data_dir.rglob("*.jpg")]
    assert len(saved) == 1 and saved[0].parent == cfg.photos_dir
    assert rows(cfg)[0][2].startswith("'=")


def test_reply_does_not_leave_mentions_active():
    a = Analysis(True, "@everyone **粗體**", ["<@123456789012345678>"], 100, 100)
    text = report.format_reply(a, 100, 1, 1)
    assert "@everyone" not in text and "<@123456789012345678>" not in text


# ---------------------------------------------------------------- 佇列


def test_queue_runs_one_at_a_time_in_order_and_survives_errors():
    active, peak, done = 0, 0, []

    async def handler(job):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        if job == 2:
            raise RuntimeError("boom")
        done.append(job)

    async def main():
        q = JobQueue(handler)
        q.start()
        for i in range(1, 6):
            q.put(i)
        await q.join()

    asyncio.run(main())
    assert peak == 1 and done == [1, 3, 4, 5]


# ---------------------------------------------------------------- 設定


def test_config_errors_are_friendly(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.env")
    env = tmp_path / ".env"
    env.write_text("DISCORD_TOKEN=abc\nCHANNEL_ID=notanumber\nCLAUDE_MODEL=sonnet\nDATA_DIR=x\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="CHANNEL_ID"):
        load_config(env)
    env.write_text("DISCORD_TOKEN=abc\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="CHANNEL_ID"):
        load_config(env)


def test_config_loads(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DISCORD_TOKEN=abc\nCHANNEL_ID=123\nCLAUDE_MODEL=sonnet\nDATA_DIR=C:\\food-data\n", encoding="utf-8")
    c = load_config(env)
    assert c.channel_id == 123 and c.claude_model == "sonnet" and c.photos_dir.name == "photos"

