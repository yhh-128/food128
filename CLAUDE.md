# CLAUDE.md

這是 `gift-agent`：一支在本機執行的 Discord bot（禮物辨識與心意指數評分助理）。指定頻道裡有人傳自己送出去的禮物照片，它用 `claude -p` 辨識並打 1～10 分的心意指數，記進該使用者自己的禮物櫃（Excel）；也能用日常說話查詢、統計、修改、刪除與推薦禮物。每個人的資料彼此隔離。

專案名稱 `gift-agent`，Python 套件名稱 `gift_agent`（程式在 `src/gift_agent/`）。啟動：`uv run gift-agent`；測試：`uv run pytest`。

## 開始任何工作前

先讀 `docs/architecture.md`。需求原文在 `docs/requirements (1).md`。設計與文件不一致時，先問使用者，不要自行決定。

## 五段防護

架構文件第 12 節定義了五段防護，之後教學會逐段關掉做對照：

1. 防護 1：`claude -p` 的檔案範圍限制（讀不到 DATA_DIR）
2. 防護 2：使用者文字只當資料，不當指令
3. 防護 3：禮物名稱當檔名前先清洗
4. 防護 4：寫進 Excel 的文字不能被當成公式
5. 防護 5：個人隔離（只能存取自己的禮物櫃；身分只來自 Discord，所有讀取都經過 `visible_rows(rows, user_id)`）

寫程式時：

- 每一段都要用註解標出開始與結束，格式固定：

  ```python
  # ==== 防護 1：claude -p 的檔案範圍隔離 開始 ====
  ...
  # ==== 防護 1 結束 ====
  ```

- 改動程式導致防護的位置移動時，同步更新 `README.md` 裡的行號對照表。

- 每一段各自獨立成一個區塊。註解掉其中一段時，其他四段與程式本身都要能照常運作。
- **一開始五段全部開啟。** 不要加開關設定，也不要自行關掉任何一段。

## 不可以 commit 的東西

- `.env`（內含 `DISCORD_TOKEN`）絕對不可以 commit。只有 `.env.example`（假的範例值）可以 commit。
- `DATA_DIR` 裡的內容（照片、`gifts.xlsx`、紀錄檔）不要 commit，裡面有所有人的禮物資料。
- commit 前先看 `git status`，確認以上檔案沒有出現在待 commit 清單。

## 其他

- Python 3.12，用 `uv run` 執行；這台電腦全域的 `python` 是 3.7，不要直接用。
