# gift-agent：禮物辨識與心意指數評分助理

在你自己的電腦上執行的 Discord bot。指定頻道裡的人傳自己送出去的禮物照片，它用 `claude -p` 辨識禮物、估價，並打 1～10 分的**心意指數**，記進該使用者自己的**禮物櫃**（Excel）。也能 @agent 用日常說話查詢、統計、修改、刪除禮物，或請它推薦適合送的禮物。**每個人的禮物櫃彼此分開。**

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
   | `CLAUDE_MODEL` | 傳給 `claude --model` 的模型，例如 `haiku` 或 `sonnet`。品牌與價格的辨識用 `sonnet` 通常比較準，`haiku` 比較省 |
   | `DATA_DIR` | `gifts.xlsx`、照片與紀錄檔放的資料夾；`./data` 代表專案資料夾裡的 `data/`（已被 `.gitignore` 擋住） |

4. 電腦上要已經登入 `claude`（在終端機輸入 `claude` 能正常使用）。

## 啟動與關閉

```
uv run gift-agent
```

在這個視窗按 `Ctrl + C` 就關閉；關閉期間傳的照片與訊息不會被處理。第一次執行 `uv` 會自動準備 Python 3.12 與套件。

執行後 `DATA_DIR` 底下會有：

```
gifts.xlsx          # 三張工作表：禮物櫃／修改歷史／偏好（請不要在 bot 執行時開著它）
photos/<使用者ID>/  # 照片，檔名：2026-10-23 14-05-33 手織圍巾.jpg
inbox/              # 處理中的暫存，處理完會清掉
logs/agent.log      # 紀錄檔
```

## 怎麼用

- **登錄禮物：** 在指定頻道傳禮物照片，可以順便打字說明（場合、送給誰、是不是手作、預算）。訊息上會先出現 ⏳，處理完換成 ✅（失敗是 ❌）。
- **`/禮物櫃`：** 在指定頻道輸入 `/禮物櫃`，只有你自己看得到你的禮物櫃（最新的在最上面，附件數與平均心意指數）。不呼叫 AI、不花額度。需要邀請 bot 時勾選 `applications.commands`，見 `docs/discord-bot-setup.md`。
- **日常說話：** @agent 或回覆 agent 的訊息，例如「我送過哪些禮物？」「這個月的心意指數統計」「#3 其實是手作的」「刪掉 #5」「幫我重新評分 #3」「同事下週生日，預算 800，幫我推薦」「記住：我喜歡實用型的禮物」。只打 @agent 會顯示說明。
- 沒有 @agent、也沒附照片的聊天，agent 完全不理。

## 五段防護

五段防護各自用 `# ==== 防護 N：… 開始 ====` 與 `# ==== 防護 N 結束 ====` 的註解包起來，**目前全部開啟**。把整段（含中間的程式）註解掉，程式仍能執行，只是少了那層保護。每段上方有幾行「沒有防護時的預設行為」，那就是關掉之後實際會執行的程式。

| 防護 | 檔案與行號（約） | 保護什麼 |
|---|---|---|
| 防護 1：`claude -p` 的檔案範圍隔離 | `src/gift_agent/analyzer.py` 第 507–518 行 | 每次只給 claude 一個一次性的暫存資料夾並加 `--restricted`，讓它讀不到 DATA_DIR 裡別人的資料 |
| 防護 2：使用者的字只當資料 | `src/gift_agent/analyzer.py` 第 520–528 行 | 給 claude 的指令是固定的；使用者打的字只放在資料區塊，不接進指令 |
| 防護 3：禮物名稱當檔名前先清洗 | `src/gift_agent/storage.py` 第 151–163 行 | 清掉 `/ \ .. :` 等會讓路徑亂跑的字、Windows 保留名 |
| 防護 4：寫進 Excel 的文字不能被當成公式 | `src/gift_agent/storage.py` 第 213–216 行 | 以 `= + - @` 開頭的文字前面加 `'`，變成純文字 |
| 防護 5：個人隔離 | `src/gift_agent/storage.py` 第 197–199 行（`visible_rows`） | 身分只來自 Discord；所有讀取都只拿到自己的資料列 |

行號可能隨修改而位移，找不到時搜尋 `防護 N` 即可。

## 範圍與已知限制

- 只記錄**送出**的禮物，不記收到的。
- 回覆都貼在**公開頻道**：頻道裡的人看得到你傳的禮物照片和 agent 的回覆。隔離的是「不能透過 agent 查別人的禮物櫃」。唯一的例外是 `/禮物櫃`，它的回覆只有本人看得到。
- 管理者（你）打開電腦上的 Excel 看得到所有人的資料；照片與說明文字會送到 Anthropic 處理。
- 每處理一件事就會用掉你自己的 Claude 額度（實測一次約 0.07～0.13 美元、8～12 秒），能在頻道發言的人就能花你的額度。
- 修改與刪除都會寫進「修改歷史」；刪除是標記為「已刪除」，不是真的刪掉。
- 看不出禮物（自拍、風景、截圖）或太糊的照片不會建檔。
- 每一句話獨立處理，agent 不記得上一句。

## 測試

```
uv run pytest
```

不需要 Discord 與 token，也不會呼叫 `claude`（用假的分析器與假的 Discord 訊息）。
