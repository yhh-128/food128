# gift-agent：禮物辨識與心意指數評分助理

在你自己的電腦上執行的 Discord bot。指定頻道裡的人傳自己送出去的禮物照片，它用 `claude -p` 辨識禮物、估價，並打 1～10 分的**心意指數**，記進該使用者自己的**禮物櫃**（Excel）。也能 @agent 用日常說話查詢、統計、修改、刪除禮物，或請它推薦適合送的禮物。**每個人的禮物櫃彼此分開。**

## 目前狀態

| 部分 | 狀態 |
|---|---|
| 文件（需求、架構、Discord 設定步驟、`CLAUDE.md`） | **已改成禮物版** |
| `src/` 與 `tests/` 的程式 | **還是舊的食物版**（套件 `foodbot`），尚未改寫 |

所以現在 `uv run foodbot` 跑起來的仍是食物版。改寫完成後，啟動指令會變成 `uv run gift-agent`，本檔案的這一節與下面「防護位置」表也會更新。

## 文件導覽

| 文件 | 內容 |
|---|---|
| `docs/requirements (1).md` | 需求：要做什麼、已決定的事 |
| `docs/architecture.md` | 架構：資料流、Excel 欄位、心意指數算法、安全與五段防護、驗證清單、實作階段 |
| `docs/discord-server-setup.md` | 怎麼建立 Discord Server、頻道、取得 `CHANNEL_ID`、邀請同學 |
| `docs/discord-bot-setup.md` | 怎麼建立 bot、打開 Message Content Intent、取得 token、邀進 Server |
| `CLAUDE.md` | 給 Claude Code 的專案規則 |

## 啟動前（只要做一次）

1. 照 `docs/discord-server-setup.md` 建立 Server 與頻道，拿到 `CHANNEL_ID`（是**頻道**的 ID，不是 Server 的 ID）。
2. 照 `docs/discord-bot-setup.md` 建立 bot、打開 Message Content Intent、拿到 token、把 bot 邀進 Server（權限記得包含 **Add Reactions**）。
3. 複製 `.env.example` 成 `.env`，填入四個值（`.env` 已被 `.gitignore` 擋住，**絕對不能 commit**）：

   | 項目 | 說明 |
   |---|---|
   | `DISCORD_TOKEN` | bot 的登入密碼，不能給任何人看 |
   | `CHANNEL_ID` | 只聽這一個頻道 |
   | `CLAUDE_MODEL` | 傳給 `claude --model` 的模型。禮物的品牌與價格辨識建議用 `sonnet` |
   | `DATA_DIR` | `gifts.xlsx`、照片與紀錄檔放的資料夾；`./data` 代表專案資料夾裡的 `data/`（已被 `.gitignore` 擋住） |

4. 電腦上要已經登入 `claude`（在終端機輸入 `claude` 能正常使用）。

## 五段防護

禮物版有五段防護，會各自用 `# ==== 防護 N：… 開始 ====` 與 `# ==== 防護 N 結束 ====` 的註解包起來，**一開始全部開啟**，之後教學逐段註解掉做對照。

| 防護 | 預計所在檔案 | 保護什麼 |
|---|---|---|
| 防護 1：`claude -p` 的檔案範圍隔離 | `analyzer.py` | 每次只給 claude 一個一次性的暫存資料夾並加 `--restricted`，讓它讀不到 DATA_DIR 裡別人的資料 |
| 防護 2：使用者的字只當資料 | `analyzer.py` | 給 claude 的指令是固定的；使用者打的字只放在資料區塊，不接進指令 |
| 防護 3：禮物名稱當檔名前先清洗 | `storage.py` | 清掉 `/ \ .. :` 等會讓路徑亂跑的字、Windows 保留名 |
| 防護 4：寫進 Excel 的文字不能被當成公式 | `storage.py` | 以 `= + - @` 開頭的文字前面加 `'`，變成純文字 |
| 防護 5：個人隔離 | `storage.py`（`visible_rows`） | 身分只來自 Discord；所有讀取都只拿到自己的資料列 |

程式改寫完成後，這裡會補上實際的行號。

## 範圍與已知限制

- 只記錄**送出**的禮物，不記收到的。
- 回覆都貼在**公開頻道**：頻道裡的人看得到你傳的禮物照片和 agent 的回覆。隔離的是「不能透過 agent 查別人的禮物櫃」。
- 管理者（你）打開電腦上的 Excel 看得到所有人的資料；照片與說明文字會送到 Anthropic 處理。
- 每處理一件事就會用掉你自己的 Claude 額度（實測一次約 0.07～0.13 美元），能在頻道發言的人就能花你的額度。
- 修改與刪除都會寫進「修改歷史」；刪除是標記為「已刪除」，不是真的刪掉。

## 測試

```
uv run pytest
```

目前測試對應的是舊的食物版；改寫時會一併重寫。
