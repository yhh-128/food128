# Discord Bot 設定步驟

這份文件帶你做四件事：

1. 在 Discord Developer Portal（Discord 的開發者網站）建立一個 application 和 bot（機器人帳號）。
2. 打開 **Message Content Intent**。
3. 拿到 **bot token**（bot 的登入密碼）。
4. 把 bot 邀進你自己的 Server。

**請先完成 `docs/discord-server-setup.md`**，因為最後一步需要你已經有自己的 Server。

---

> ## ⚠️ 先看這個：bot token 的規則
>
> **bot token 就是這個 bot 的密碼。拿到它的人，可以完全控制你的 bot**：用你的 bot 發言、讀它能看的所有頻道。
>
> - **絕對不能貼給任何人**：同學、朋友、老師、網路上的任何人都不行。
> - **絕對不能貼進這個對話**（包含跟 Claude 的對話）。需要時，你自己把它貼進 `.env` 檔就好，不用給我看。
> - **絕對不能 commit 進 git**：`.env` 檔已經被 `.gitignore` 擋住，但每次 commit 前仍要看一眼 `git status`，確認 `.env` 沒出現。
> - **也不要**截圖時讓它入鏡、不要貼在 Discord 或 LINE 裡。
>
> **如果不小心洩漏了：** 立刻回到 Developer Portal 的 Bot 頁面按 **Reset Token**（見下面第三節的 3.4），舊的 token 會馬上失效。

---

## 開始前

- 你的電腦：Windows，用瀏覽器（Chrome、Edge 都可以）操作。
- 需要登入你的 Discord 帳號。
- Developer Portal 的畫面偶爾會更新，按鈕位置可能略有不同；找名稱相同或意思相近的就好。

---

## 一、建立 application 和 bot

**Application**（應用程式）是 Discord 開發者網站上的一個專案；每個 application 裡面可以有一個 **bot**（機器人帳號）。

1. 打開瀏覽器，網址列輸入 `https://discord.com/developers/applications`，按 Enter。
2. 如果要求登入，用你的 Discord 帳號登入。
3. 點右上角的藍色按鈕 **New Application**（新增應用程式）。
4. 在 **Name** 欄位輸入名字，例如 `food128-bot`。這個名字就是 bot 在 Server 裡顯示的名字。
5. 勾選下面同意條款的方塊。
6. 點 **Create**（建立）。
7. 你會進入這個 application 的設定頁（左邊有一排選單：General Information、Installation、OAuth2、Bot…）。

> 這個 application 建立後，Discord 會**自動幫它建立一個 bot**，你不用另外按「Add Bot」。

---

## 二、打開 Message Content Intent（一定要做）

### 為什麼一定要

Discord 預設**不讓 bot 看到訊息的內容**，包括：訊息裡的**文字**，以及訊息裡的**附件**（也就是食物照片）。沒打開的話，bot 收到訊息時文字是空的、看不到照片，整個 agent 就等於瞎了。

這個 agent 需要：
- **照片附件**：才能拿到食物照片來分析。
- **文字**：才能讀使用者附的備註（例如「只吃一半」）和更正的話（例如「那是豆漿」）。

**Intent**（意圖）就是 Discord 要 bot 事先「申請」的權限開關。

### 怎麼打開

1. 在左邊選單點 **Bot**。
2. 往下捲到 **Privileged Gateway Intents**（特權閘道意圖）這一區。
3. 找到 **Message Content Intent** 的開關，把它**打開**（變成藍色）。
4. 另外兩個 **Presence Intent**、**Server Members Intent** 保持**關閉**，這個專案用不到。
5. 頁面上方或下方會跳出一條「Careful — you have unsaved changes」的提示列，點 **Save Changes**（儲存變更）。
6. 確認：重新整理頁面，Message Content Intent 仍是打開的。

> 你的 Server 很小（不到 100 個人），打開這個開關**不需要**另外向 Discord 申請審核。

---

## 三、拿到 bot token

### 3.1 注意

token **只會完整顯示一次**。顯示的當下就要複製，離開頁面之後就看不到了，只能重新產生。

### 3.2 產生並複製 token

1. 還是在左邊選單的 **Bot** 頁面。
2. 往上找到 **Token** 這一區，點 **Reset Token**（重設 token）。
3. 跳出確認視窗，點 **Yes, do it!**。
4. 如果要求輸入密碼或兩步驟驗證碼，照著做。
5. 頁面會出現一長串文字，旁邊有 **Copy**（複製）按鈕，**立刻點它**。
6. token 現在在你的剪貼簿裡了。**不要貼到任何聊天或文件裡**，直接做下一步。

### 3.3 把 token 放進 `.env`

`.env` 是放在專案資料夾裡的設定檔，程式啟動時會讀它。

1. 打開「記事本」。
2. 輸入下面四行，再把方括號的部分換成你的內容（`DISCORD_TOKEN=` 後面用 `Ctrl + V` 貼上剛剛複製的 token）：

   ```
   DISCORD_TOKEN=（貼上你的 token）
   CHANNEL_ID=（貼上你在 server-setup 拿到的頻道 ID）
   CLAUDE_MODEL=（等一下我們再決定要填什麼）
   DATA_DIR=（照片和 Excel 要放的資料夾，例如 C:\food128-data）
   ```

3. 按 `Ctrl + S` 儲存。
4. 在「另存新檔」視窗，**位置**選 `C:\Users\k0929\food128`（就是專案資料夾）。
5. **存檔類型**改成 **所有檔案 (*.*)**。（沒改的話，記事本會偷偷存成 `.env.txt`，程式就讀不到。）
6. **檔案名稱**輸入 `.env`（開頭有一個點，前面沒有其他字，不加 `.txt`）。
7. 點 **存檔**。
8. 檢查：在檔案總管打開 `food128` 資料夾，確認看到的檔名是 `.env`，不是 `.env.txt`。（如果看不到副檔名，點上方「檢視」→ 勾選「副檔名」。）

> `CLAUDE_MODEL` 和 `DATA_DIR` 先不用急著填對，等開始寫程式時我們再一起確認。`DATA_DIR` 建議放在專案資料夾**之外**。

### 3.4 如果 token 洩漏、或不小心弄丟了

1. 回到 `https://discord.com/developers/applications`，點你的 application。
2. 左邊點 **Bot**，點 **Reset Token** → **Yes, do it!**。
3. 複製新的 token，換掉 `.env` 裡的舊 token。
4. 舊的 token 立刻失效，別人拿著也沒用。

---

## 四、設定 bot 的邀請權限，並邀進你的 Server

### 4.1 先把 bot 設成「只有你能邀請」

1. 還是在 **Bot** 頁面，往下找 **Public Bot** 的開關。
2. 把它**關閉**。（這樣只有你自己能把這個 bot 加到 Server，別人拿到邀請網址也加不了。）
3. 如果頁面跳出 **Save Changes**，點它儲存。
4. **Requires OAuth2 Code Grant** 保持關閉。

### 4.2 產生邀請網址

1. 在左邊選單點 **OAuth2**，再找到 **OAuth2 URL Generator**（若需要先往下捲或點開子項目）。
2. 在 **Scopes**（範圍）區，勾選兩個：
   - **bot**
   - **applications.commands**（讓 bot 能提供 `/今日` 這類斜線指令）
3. 勾選 bot 之後，下面會出現 **Bot Permissions**（bot 權限）區。**只勾這三個**：
   - **View Channels**（檢視頻道）：看得到頻道
   - **Send Messages**（傳送訊息）：能回覆
   - **Read Message History**（讀取訊息歷史）：能「回覆某一則訊息」
4. **不要**勾 Administrator（管理員）或其他權限。原則是只給 bot 做事所需要的最小權限。
5. 頁面最下面會出現 **Generated URL**（產生的網址），點旁邊的 **Copy**。

> 這個邀請網址**不是機密**（它不含 token），但 4.1 把 Public Bot 關掉之後，只有你能用它。

### 4.3 把 bot 邀進你的 Server

1. 打開一個新的瀏覽器分頁，把剛剛複製的網址貼到網址列，按 Enter。
2. 頁面會顯示 **Add to server**（加入伺服器）。點下面的下拉選單，選你的 Server（例如 `food128`）。
3. 點 **Continue**（繼續）。
4. 這一頁會列出剛剛勾選的權限，確認只有你勾的那幾個，然後點 **Authorize**（授權）。
5. 如果出現「我不是機器人」的驗證，照著點選完成。
6. 看到「Authorized」（已授權）的畫面就成功了。

### 4.4 確認 bot 已經進來

1. 回到 Discord，打開你的 Server。
2. 看右邊的成員清單，往下找 **OFFLINE**（離線）區，應該看到 `food128-bot`，旁邊有個 **BOT** 標籤。
3. 它是灰色／離線，這是**正常的**，因為你的 agent 程式還沒開。程式啟動後，它會變成綠色的上線。
4. 點進 `# food-log`（你在 server-setup 建的頻道），Discord 可能出現一則「food128-bot 加入了這個伺服器」的系統訊息。

---

## 完成檢查

- [ ] 我已在 Developer Portal 建立 application（例如 `food128-bot`）
- [ ] **Message Content Intent** 已打開並儲存，重新整理後仍是打開的
- [ ] Public Bot 已關閉
- [ ] 我已複製 bot token，並**只**貼進 `.env`，沒貼到任何其他地方
- [ ] `.env` 放在專案資料夾，檔名真的是 `.env`（不是 `.env.txt`）
- [ ] 邀請網址的 Scopes 勾了 `bot` 和 `applications.commands`，權限只勾了 View Channels、Send Messages、Read Message History
- [ ] bot 已出現在我的 Server 成員清單（灰色離線）
- [ ] 我執行 `git status`，確認 `.env` 沒有出現在待 commit 清單裡

## 常見問題

| 狀況 | 怎麼辦 |
|---|---|
| 找不到 Message Content Intent | 確認在左邊選單的 **Bot** 頁面，往下捲到 **Privileged Gateway Intents** |
| 找不到 Copy 按鈕，token 一直看不到 | token 只會在「Reset Token」之後顯示一次；沒複製到就再按一次 Reset Token |
| 邀請網址打開後，下拉選單裡沒有我的 Server | 你登入的 Discord 帳號不是 Server 的擁有者，或是登入錯帳號；換成建立 Server 的那個帳號 |
| 儲存 `.env` 後變成 `.env.txt` | 另存新檔時「存檔類型」要選「所有檔案」，檔名輸入 `.env` |
| 之後 bot 上線，但完全沒反應 | 先檢查 Message Content Intent 有沒有儲存成功，再檢查 `CHANNEL_ID` 是不是 `# food-log` 頻道的 ID |
| 懷疑 token 外洩 | 立刻 Reset Token（見 3.4），再用新 token 更新 `.env` |
