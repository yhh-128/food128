"""執行「動作」：列出、詳情、統計、修改、重新評分、刪除（只負責確認前的準備）、偏好、推薦、說明。

AI 只決定動作；真正讀寫資料的是這裡的程式，而且資料一律來自 store（已經過防護 5 篩過），
使用者 ID 只來自 Discord。
"""

import copy
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from gift_agent import analyzer, report, scoring
from gift_agent.analyzer import Action, AnalysisError
from gift_agent.config import Config
from gift_agent.storage import (
    CHANGE_FIELDS, ExcelWriteError, GiftRow, GiftStore, NotFound, ScoreUpdate,
)

log = logging.getLogger(__name__)


@dataclass
class ConfirmDelete:
    number: int
    name: str


@dataclass
class Outcome:
    ok: bool
    reply: str | None  # None：不回覆（例如這句話根本不是對 agent 說的）
    bind_gift: int | None = None  # 這則回覆代表哪一件禮物（登錄成功時，之後用來找到被回覆的那件）
    confirm: ConfirmDelete | None = None


@dataclass
class Actor:
    user_id: str  # 只來自 Discord
    uploader: str
    said: str  # 使用者原話（寫進修改歷史用）


# ---------------------------------------------------------------- 篩選與統計（純程式，不經過 AI）


def apply_filters(rows: list[GiftRow], action: Action) -> list[GiftRow]:
    def has(text: str | None, wanted: str | None) -> bool:
        return wanted is None or (text is not None and wanted.casefold() in text.casefold())

    result = [
        r for r in rows
        if has(r.recipient, action.recipient) and has(r.occasion, action.occasion)
        and (action.category is None or r.category == action.category)
        and (action.date_from is None or r.gift_date >= action.date_from)
        and (action.date_to is None or r.gift_date <= action.date_to)
    ]
    return sorted(result, key=lambda r: (r.gift_date, r.number), reverse=True)


GROUP_LABELS = {"occasion": "場合", "recipient": "送給誰", "month": "月份", "category": "種類"}


def group_stats(rows: list[GiftRow], group_by: str | None) -> list[tuple[str, int, float]]:
    if group_by in (None, "none"):
        return []
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        key = {
            "occasion": r.occasion, "recipient": r.recipient, "category": r.category, "month": r.gift_date[:7],
        }[group_by] or "（未提供）"
        buckets[key].append(r.score)
    groups = [(name, len(scores), sum(scores) / len(scores)) for name, scores in buckets.items()]
    return sorted(groups, key=lambda g: (-g[2], g[0]))


def _filtered(action: Action) -> bool:
    return any([action.recipient, action.occasion, action.category, action.date_from, action.date_to])


def gift_context_text(row: GiftRow) -> str:
    """交給 AI 重新評分用的「已登錄資料」（內容是使用者可能輸入過的字，所以會放進資料區塊）。"""
    return (
        f"name: {row.name}; category: {row.category}; brand: {row.brand or 'unknown'}; "
        f"price_twd: {row.price if row.price is not None else 'unknown'}; occasion: {row.occasion or 'unknown'}; "
        f"recipient: {row.recipient or 'unknown'}; handmade: {row.handmade or 'unknown'}; "
        f"budget_twd: {row.budget if row.budget is not None else 'unknown'}; note: {row.note or 'none'}"
    )


async def _rescore(store_row: GiftRow, cfg: Config, rescore=analyzer.rescore_gift) -> ScoreUpdate:
    photo = cfg.data_dir / store_row.photo if store_row.photo else None
    photo = photo if photo is not None and photo.is_file() else None
    result = await rescore(photo, gift_context_text(store_row), cfg)
    return ScoreUpdate(result.sub_scores, scoring.final_score(result.sub_scores), result.reasons)


# ---------------------------------------------------------------- 執行


async def execute(
    action: Action, actor: Actor, store: GiftStore, cfg: Config,
    rescore=analyzer.rescore_gift, recommend=analyzer.recommend_gifts,
) -> Outcome:
    kind = action.kind
    try:
        if kind == "none":
            return Outcome(True, None)
        if kind == "help":
            return Outcome(True, report.help_reply())
        if kind == "clarify":
            return Outcome(True, report.UNCLEAR_TEXT if not action.question else action.question)

        gifts = await store.list_gifts(actor.user_id)

        if kind == "list":
            return Outcome(True, report.list_reply(apply_filters(gifts, action), len(gifts)))

        if kind == "stats":
            rows = apply_filters(gifts, action)
            group = action.group_by if action.group_by not in (None, "none") else None
            return Outcome(True, report.stats_reply(rows, group_stats(rows, group), GROUP_LABELS.get(group), _filtered(action)))

        if kind in ("show", "modify", "rescore", "delete"):
            row = next((g for g in gifts if g.number == action.number), None)
            if action.number is None or row is None:
                # 別人的編號對這位使用者來說，等於不存在
                return Outcome(False, f"找不到 #{action.number}，只能操作你自己的禮物。" if action.number else "請告訴我是哪一件禮物（例如 #3）。")

            if kind == "show":
                return Outcome(True, report.show_reply(row))
            if kind == "delete":
                return Outcome(True, report.delete_confirm_text(row.number, row.name), confirm=ConfirmDelete(row.number, row.name))
            if kind == "rescore":
                scores = await _rescore(row, cfg, rescore)
                old, new = await store.update_gift(actor.user_id, actor.uploader, row.number, {}, scores, actor.said)
                return Outcome(True, report.rescore_reply(old, new))
            return await _modify(action, actor, store, cfg, row, rescore)

        if kind == "add_preference":
            if not action.preference_text:
                return Outcome(False, "你想記下什麼偏好呢？例如「記住：我喜歡實用型的禮物」。")
            pref = await store.add_preference(actor.user_id, actor.uploader, action.preference_text, actor.said)
            return Outcome(True, f"📝 已記下偏好 {pref.number}：{report._safe(pref.text)}")

        if kind == "list_preferences":
            return Outcome(True, report.preferences_reply(await store.list_preferences(actor.user_id)))

        if kind == "delete_preference":
            if action.preference_index is None:
                return Outcome(False, "請告訴我要刪掉第幾條偏好。")
            pref = await store.delete_preference(actor.user_id, actor.uploader, action.preference_index, actor.said)
            return Outcome(True, f"🗑️ 已刪除偏好 {pref.number}：{report._safe(pref.text)}（修改歷史仍有紀錄）")

        if kind == "recommend":
            return await _recommend(action, actor, store, cfg, gifts, recommend)

        return Outcome(False, report.UNCLEAR_TEXT)
    except NotFound as exc:
        return Outcome(False, f"{exc}，只能操作你自己的資料。")
    except ExcelWriteError as exc:
        log.error("Excel 寫入失敗：%s", exc)
        return Outcome(False, report.EXCEL_FAIL_TEXT)
    except AnalysisError as exc:
        log.warning("動作執行時 AI 失敗（%s）：%s", kind, exc)
        return Outcome(False, report.FAIL_TEXT)


async def _modify(action: Action, actor: Actor, store: GiftStore, cfg: Config, row: GiftRow, rescore) -> Outcome:
    if not action.changes:
        return Outcome(False, "你想把它改成什麼呢？例如「#3 其實是手作的」。")
    changes = {k: v for k, v in action.changes.items() if k in CHANGE_FIELDS}
    needs_rescore = any(k in scoring.SCORING_FIELDS for k in changes)

    scores = None
    rescored: bool | None = None
    if needs_rescore:
        # 用「修改後」的內容請 AI 重新評分；失敗的話仍然套用修改，只是分數不變
        preview = _preview(row, changes)
        try:
            scores = await _rescore(preview, cfg, rescore)
            rescored = True
        except AnalysisError as exc:
            log.warning("修改後重新評分失敗：%s", exc)
            rescored = False
    old, new = await store.update_gift(actor.user_id, actor.uploader, row.number, changes, scores, actor.said)
    return Outcome(True, report.modify_reply(old, new, changes, rescored))


def _preview(row: GiftRow, changes: dict[str, object]) -> GiftRow:
    preview = copy.copy(row)
    for key, value in changes.items():
        attr = CHANGE_FIELDS[key][0]
        setattr(preview, attr, ("是" if value else "否") if key == "handmade" and value is not None else value)
    return preview


async def _recommend(action: Action, actor: Actor, store: GiftStore, cfg: Config, gifts: list[GiftRow], recommend) -> Outcome:
    prefs = await store.list_preferences(actor.user_id)
    given = [g for g in gifts if action.recipient and g.recipient and action.recipient.casefold() in g.recipient.casefold()]
    texts = {
        "request": (
            f"occasion: {action.occasion or 'unknown'}; recipient: {action.recipient or 'unknown'}; "
            f"budget_twd: {action.budget_twd if action.budget_twd is not None else 'unknown'}; notes: {action.notes or 'none'}"
        ),
        "my_preferences": "\n".join(f"- {p.text}" for p in prefs) or "(none)",
        "gifts_already_given_to_this_recipient": "\n".join(
            f"- {g.name} ({g.gift_date}, {g.score:.1f}/10)" for g in given[:15]
        ) or "(none)",
    }
    return Outcome(True, report.recommendation_reply(await recommend(texts, cfg)))


def recent_gifts_text(gifts: list[GiftRow], limit: int = 30) -> str:
    recent = sorted(gifts, key=lambda g: g.number, reverse=True)[:limit]
    if not recent:
        return "(the user has no gifts yet)"
    return "\n".join(
        f"#{g.number} {g.name} | recipient: {g.recipient or '-'} | occasion: {g.occasion or '-'} | "
        f"date: {g.gift_date} | score: {g.score:.1f}"
        for g in recent
    )


def today_text() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")
