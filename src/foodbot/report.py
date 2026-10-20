"""組出回覆給上傳者的文字。"""

import discord

from foodbot.analyzer import Analysis

FAIL_TEXT = "分析失敗，請再傳一次"
EXCEL_FAIL_TEXT = "紀錄暫時寫不進去，請稍後再傳一次"
MAX_REPLY_CHARS = 1900  # Discord 單則訊息上限是 2000


def _safe(text: str) -> str:
    """AI 回的字會顯示在頻道裡：跳脫 Markdown，並讓 @everyone 之類只是普通文字。"""
    return discord.utils.escape_mentions(discord.utils.escape_markdown(text))


def format_reply(analysis: Analysis, daily_total: int, index: int, count: int) -> str:
    where = f"（第 {index} 張／共 {count} 張）" if count > 1 else ""
    total_line = f"當日已攝取熱量（整個頻道合計）：{daily_total:,} 大卡"

    if analysis.is_food:
        ingredients = "、".join(analysis.ingredients) or "（未提供）"
        text = (
            f"✅ 這是食物{where}\n"
            f"餐點：{_safe(analysis.name)}\n"
            f"成份：{_safe(ingredients)}\n"
            f"重量：約 {round(analysis.weight_g)} 公克\n"
            f"熱量：約 {round(analysis.calories_kcal)} 大卡\n"
            f"{total_line}"
        )
    else:
        text = f"🚫 你傳的不是食物，是{_safe(analysis.name)}。{where}\n{total_line}"
    return text[:MAX_REPLY_CHARS]
