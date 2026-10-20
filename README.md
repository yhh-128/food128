# food128：食物照片記錄 Agent

在你自己的電腦上執行的 Discord bot。指定頻道裡有人傳食物照片，它用 `claude -p` 判斷、估熱量，記進 Excel、存照片，再回覆上傳者。設計說明見 `docs/architecture.md`。

## 啟動前（只要做一次）

1. 照 `docs/discord-server-setup.md` 建立 Server 與頻道，拿到 `CHANNEL_ID`。
2. 照 `docs/discord-bot-setup.md` 建立 bot、打開 Message Content Intent、拿到 token、把 bot 邀進 Server（權限記得包含 **Add Reactions**）。
3. 複製 `.env.example` 成 `.env`，填入四個值（`.env` 已被 `.gitignore` 擋住，**絕對不能 commit**）：

   | 項目 | 說明 |
   |---|---|
   | `DISCORD_TOKEN` | bot 的登入密碼，不能給任何人看 |
   | `CHANNEL_ID` | 只聽這一個頻道 |
   | `CLAUDE_MODEL` | 傳給 `claude --model` 的模型，例如 `sonnet` |
   | `DATA_DIR` | 紀錄與照片放的資料夾；`./data` 代表專案資料夾裡的 `data/`（已被 `.gitignore` 擋住） |

4. 電腦上要已經登入 `claude`（在終端機輸入 `claude` 能正常使用）。

## 啟動與關閉

```
uv run foodbot
```

在這個視窗按 `Ctrl + C` 就關閉；關閉期間傳的照片不會被處理。第一次執行 `uv` 會自動準備 Python 3.12 與套件。

執行後 `DATA_DIR` 底下會有：

```
records.xlsx        # 紀錄，一直往下加（請不要在 bot 執行時開著它）
photos/             # 照片，檔名：2026-10-20 12-30-15 排骨便當.jpg
inbox/              # 處理中的暫存，處理完會清掉
logs/agent.log      # 紀錄檔
```

## 四段防護在哪裡

每一段都用 `# ==== 防護 N：… 開始 ====` 與 `# ==== 防護 N 結束 ====` 包起來，**目前四段全部開啟**。把整段（含中間的程式）註解掉，程式仍能執行，只是少了那層保護。

| 防護 | 檔案與行號（約） | 保護什麼 |
|---|---|---|
| 防護 1：claude -p 的檔案範圍隔離 | `src/foodbot/analyzer.py` 第 157–166 行 | 每次只給 claude 一個只放這張照片的暫存資料夾，並加 `--restricted`，讓它讀不到 DATA_DIR 裡別人的照片和紀錄 |
| 防護 2：上傳者的文字只當資料 | `src/foodbot/analyzer.py` 第 168–174 行 | 給 claude 的指令是固定的；上傳者打的字只放在 `<user_note>` 資料區塊，不接進指令 |
| 防護 3：食物名稱當檔名前先清洗 | `src/foodbot/storage.py` 第 68–80 行 | 清掉 `/ \ .. :` 等會讓路徑亂跑的字、Windows 保留名，並確認檔案真的落在 `photos/` 裡 |
| 防護 4：寫進 Excel 的文字不能被當成公式 | `src/foodbot/storage.py` 第 111–114 行 | 以 `= + - @` 開頭的文字前面加 `'`，變成純文字 |

行號可能隨修改而位移，找不到時搜尋 `防護 N` 即可。每段上方有幾行「沒有防護時的預設行為」，那是關掉該段防護之後實際會執行的程式。

## 測試

```
uv run pytest
```

不需要 Discord 與 token，也不會呼叫 `claude`。

## 這一版有做與沒做

- 有做：⏳／✅／❌ 反應、一次處理一張的排隊、食物與非食物都建檔存照片並回覆、當日已攝取熱量（整個頻道合計，不分人）、Excel 被打開時印警告並重試。
- 沒做：更正、`/` 查詢指令、開機公告、信心標示、備註欄、每人分開計算。
