"""測試共用的小工具（不需要 Discord、不會呼叫 claude）。"""

import asyncio
import io
from datetime import datetime, timezone

from PIL import Image

from gift_agent import scoring
from gift_agent.analyzer import Action, GiftAnalysis
from gift_agent.storage import ScoreUpdate

T = datetime(2026, 10, 23, 14, 5, 33, tzinfo=timezone.utc)
SUBS = {"value": 6, "effort": 9, "fit": 8, "uniqueness": 8}


def score(subs=None, reasons="手織耗時"):
    subs = subs or SUBS
    return ScoreUpdate(dict(subs), scoring.final_score(subs), reasons)


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), "orange").save(buf, "PNG")
    return buf.getvalue()


def gift_analysis(**changes):
    base = dict(
        status="ok", item_name="手織圍巾", category="手作品", brand=None, price_twd=600.0, confidence="medium",
        occasion="生日", recipient="媽媽", handmade=True, budget_twd=500.0, sub_scores=dict(SUBS), reasons="手織耗時",
    )
    base.update(changes)
    return GiftAnalysis(**base)


def action(kind, **fields):
    return Action(kind=kind, **fields)


def run(coro):
    return asyncio.run(coro)


async def add(store, user_id="111", name="手織圍巾", uploader="小明", recipient="媽媽", occasion="生日",
              subs=None, when=T, photo="photos/111/a.jpg"):
    return await store.add_gift(
        user_id=user_id, uploader=uploader, logged_at=when, name=name, category="手作品", brand=None, price=600.0,
        confidence="medium", occasion=occasion, recipient=recipient, handmade=True, budget=500.0,
        score=score(subs), photo=photo,
    )
