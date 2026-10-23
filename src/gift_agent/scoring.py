"""心意指數：公開、固定的評分標準。AI 只給四個分項，總分由這裡用固定比重計算。"""

# (分項的英文代號, 比重, 中文名稱)
WEIGHTS = (
    ("value", 0.25, "價值感"),
    ("effort", 0.30, "用心程度"),
    ("fit", 0.25, "貼近對方"),
    ("uniqueness", 0.20, "獨特性"),
)
SUB_KEYS = tuple(k for k, _, _ in WEIGHTS)

CATEGORIES = (
    "飾品配件", "服飾", "美妝保養", "3C 電子", "食品飲料", "花卉植物",
    "書籍文具", "家居生活", "玩具遊戲", "手作品", "票券卡片", "其他",
)

# 這些欄位被修改後，原本的分數就不準了，需要重新評分
SCORING_FIELDS = frozenset({
    "item_name", "category", "brand", "price_twd", "occasion", "recipient", "handmade", "budget_twd",
})


def final_score(sub_scores: dict[str, int]) -> float:
    """心意指數 = 價值感×0.25 + 用心程度×0.30 + 貼近對方×0.25 + 獨特性×0.20，算到小數第一位，限制在 1.0～10.0。"""
    total = sum(sub_scores[key] * weight for key, weight, _ in WEIGHTS)
    return round(min(10.0, max(1.0, total)), 1)


def explain() -> str:
    parts = "、".join(f"{label} {round(weight * 100)}%" for _, weight, label in WEIGHTS)
    return (
        f"心意指數（1～10 分）= {parts} 的加權平均。\n"
        "AI 只負責給四個分項（各 1～10 分）和理由，總分由程式依上面的比重計算；資料不足時會明講「資料不足」。"
    )
