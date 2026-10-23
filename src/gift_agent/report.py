"""組出回覆給使用者的文字。AI 或使用者寫的字顯示到頻道前，一律先經過 _safe。"""

import discord

from gift_agent import scoring
from gift_agent.analyzer import GiftAnalysis, Recommendation
from gift_agent.storage import CHANGE_FIELDS, GiftRow, PrefRow

FAIL_TEXT = "分析失敗，請再傳一次"
UNCLEAR_TEXT = "我不太確定你的意思，可以換個說法嗎？"
EXCEL_FAIL_TEXT = "紀錄暫時寫不進去，請稍後再試"
MAX_REPLY_CHARS = 1900  # Discord 單則訊息上限是 2000
MAX_LIST_ITEMS = 15


def _safe(text) -> str:
    """跳脫 Markdown，並讓 @everyone 之類只是普通文字。"""
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def _clip(text: str) -> str:
    return text[:MAX_REPLY_CHARS]


def money(value: float | None) -> str:
    return "不明" if value is None else f"NT${round(value):,}"


def _score_line(row: GiftRow) -> str:
    return f"{row.score:.1f}"


CONFIDENCE_LABEL = {"high": "高", "medium": "中", "low": "低", "": ""}


def upload_reply(gift: GiftRow, analysis: GiftAnalysis, count: int, avg: float, index: int, total: int) -> str:
    where = f"（第 {index} 張／共 {total} 張）" if total > 1 else ""
    context = []
    for label, value in (("場合", gift.occasion), ("送給", gift.recipient), ("手作", gift.handmade),
                         ("預算", money(gift.budget) if gift.budget is not None else None)):
        if value:
            context.append(f"{label}：{_safe(value)}")
    lines = [
        f"🎁 已登錄到你的禮物櫃：#{gift.number} {_safe(gift.name)}{where}",
        f"種類：{_safe(gift.category)}　品牌：{_safe(gift.brand) if gift.brand else '（未辨識）'}",
        f"估計價格：約 {money(gift.price)}（信心：{CONFIDENCE_LABEL.get(gift.confidence, gift.confidence)}）",
    ]
    if context:
        lines.append("　".join(context))
    lines += [
        "",
        f"心意指數：{_score_line(gift)} / 10",
        f"　價值感 {gift.s_value}　用心程度 {gift.s_effort}　貼近對方 {gift.s_fit}　獨特性 {gift.s_uniq}",
        f"理由：{_safe(gift.reasons)}",
        "",
        f"你的禮物櫃：共 {count} 件，平均心意指數 {avg:.1f}",
        "回覆這則訊息就可以修改，例如「這是送給外婆的」。",
    ]
    return _clip("\n".join(lines))


def not_a_gift_reply(analysis: GiftAnalysis) -> str:
    seen = f"（看起來是：{_safe(analysis.what_is_it)}）" if analysis.what_is_it else ""
    if analysis.status == "unreadable":
        return f"🌫️ 照片太暗或太糊，看不清楚{seen}，請重拍後再傳一次。"
    return f"🤔 這張照片看不出是禮物{seen}，請傳禮物本身的照片。"


def gift_line(row: GiftRow) -> str:
    parts = [f"#{row.number} {_safe(row.name)}"]
    if row.recipient:
        parts.append(f"給 {_safe(row.recipient)}")
    if row.occasion:
        parts.append(_safe(row.occasion))
    parts += [row.gift_date, f"{_score_line(row)} 分"]
    return "｜".join(parts)


def list_reply(rows: list[GiftRow], total_in_cabinet: int) -> str:
    if not rows:
        return "你的禮物櫃裡沒有符合的禮物。" if total_in_cabinet else "你的禮物櫃還是空的，傳一張禮物照片來登錄第一件吧！"
    shown = rows[:MAX_LIST_ITEMS]
    lines = [f"🎁 你的禮物櫃（符合 {len(rows)} 件）"] + [gift_line(r) for r in shown]
    if len(rows) > len(shown):
        lines.append(f"……還有 {len(rows) - len(shown)} 件沒列出，可以加上條件縮小範圍。")
    return _clip("\n".join(lines))


def show_reply(row: GiftRow) -> str:
    lines = [
        f"🎁 #{row.number} {_safe(row.name)}",
        f"種類：{_safe(row.category)}　品牌：{_safe(row.brand) if row.brand else '（未辨識）'}",
        f"估計價格：{money(row.price)}（信心：{CONFIDENCE_LABEL.get(row.confidence, row.confidence)}）",
        f"送出日期：{row.gift_date}　場合：{_safe(row.occasion or '未提供')}　送給：{_safe(row.recipient or '未提供')}",
        f"手作：{row.handmade or '未提供'}　預算：{money(row.budget) if row.budget is not None else '未提供'}",
        f"心意指數：{_score_line(row)} / 10（價值感 {row.s_value}、用心程度 {row.s_effort}、貼近對方 {row.s_fit}、獨特性 {row.s_uniq}）",
        f"理由：{_safe(row.reasons)}",
    ]
    if row.note:
        lines.append(f"備註：{_safe(row.note)}")
    return _clip("\n".join(lines))


def stats_reply(rows: list[GiftRow], groups: list[tuple[str, int, float]], group_label: str | None, filtered: bool) -> str:
    if not rows:
        return "沒有符合條件的禮物，所以沒有統計可以看。"
    best = max(rows, key=lambda r: r.score)
    worst = min(rows, key=lambda r: r.score)
    total_price = sum(r.price or 0 for r in rows)
    avg = sum(r.score for r in rows) / len(rows)
    lines = [
        "📊 心意指數統計" + ("（依你設定的條件）" if filtered else "（全部有效的禮物）"),
        f"禮物件數：{len(rows)}　平均心意指數：{avg:.1f}",
        f"最高：#{best.number} {_safe(best.name)}（{_score_line(best)}）",
        f"最低：#{worst.number} {_safe(worst.name)}（{_score_line(worst)}）",
        f"估計總金額：{money(total_price)}",
    ]
    if group_label and groups:
        lines += ["", f"依{group_label}："]
        lines += [f"　{_safe(name)}：{count} 件，平均 {mean:.1f}" for name, count, mean in groups[:15]]
    return _clip("\n".join(lines))


def modify_reply(old: GiftRow, new: GiftRow, changes: dict[str, object], rescored: bool | None) -> str:
    lines = [f"✏️ 已修改 #{new.number} {_safe(new.name)}："]
    for key in changes:
        attr, label = CHANGE_FIELDS[key]
        lines.append(f"・{label}：{_display(getattr(old, attr))} → {_display(getattr(new, attr))}")
    if rescored is True:
        lines.append(f"・心意指數：{old.score:.1f} → {new.score:.1f}（因為內容改了，已自動重新評分）")
    elif rescored is False:
        lines.append("・分數這次沒有更新（重新評分失敗），可以再說「重新評分 #" + str(new.number) + "」")
    lines.append("修改前的內容已記在修改歷史裡。")
    return _clip("\n".join(lines))


def rescore_reply(old: GiftRow, new: GiftRow) -> str:
    return _clip(
        f"🔁 已重新評分 #{new.number} {_safe(new.name)}：{old.score:.1f} → {new.score:.1f}\n"
        f"價值感 {new.s_value}　用心程度 {new.s_effort}　貼近對方 {new.s_fit}　獨特性 {new.s_uniq}\n"
        f"理由：{_safe(new.reasons)}"
    )


def _display(value) -> str:
    if value is None or value == "":
        return "（空）"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _safe(value)


def delete_confirm_text(number: int, name: str) -> str:
    return f"確定要刪除 #{number} {_safe(name)} 嗎？\n它會從你的禮物櫃移除，但修改歷史仍保留紀錄。60 秒內沒有按就會失效。"


def deleted_reply(row: GiftRow) -> str:
    return f"🗑️ 已把 #{row.number} {_safe(row.name)} 從你的禮物櫃移除，修改歷史仍保留紀錄。"


def preferences_reply(rows: list[PrefRow]) -> str:
    if not rows:
        return "你還沒有記下任何偏好。可以說「記住：我喜歡實用型的禮物」。"
    return _clip("📝 你記下的偏好：\n" + "\n".join(f"{r.number}. {_safe(r.text)}" for r in rows))


def recommendation_reply(rec: Recommendation) -> str:
    lines = ["💡 推薦的禮物（僅為建議，不保證有貨，價格是估計）"]
    for i, s in enumerate(rec.suggestions, 1):
        lines.append(
            f"{i}. {_safe(s.name)}｜{money(s.price_min)}～{money(s.price_max)}｜預期心意指數約 {s.expected_score:.1f}\n"
            f"　{_safe(s.reason)}"
        )
    if rec.avoid:
        lines.append(f"\n提醒：{_safe(rec.avoid)}")
    return _clip("\n".join(lines))


def help_reply() -> str:
    return _clip(
        "我是禮物櫃助理 🎁\n"
        "・傳一張你送出去的禮物照片（可以順便說明場合、送給誰、是不是手作、預算），我會辨識並打心意指數。\n"
        "・@我 或回覆我的訊息，用日常說話問我，例如：\n"
        "　「我送過哪些禮物？」「這個月的心意指數統計」「#3 其實是手作的」\n"
        "　「刪掉 #5」「幫我重新評分 #3」「同事下週生日，預算 800，幫我推薦」「記住：我喜歡實用型的禮物」\n"
        "・你只看得到、也只改得到自己的禮物櫃。\n\n"
        + scoring.explain()
    )
