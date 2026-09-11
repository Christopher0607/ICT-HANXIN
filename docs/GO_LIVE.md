# 上線清單

照著這份做。順序是刻意的：**免費、可逆的步驟排在花錢、不可逆的前面。**

---

## 先讀：官方規則（2026-09-10 書面答覆，全文在 `topstep_automation_enquiry.md`）

| 階段 | 自動化 | 透過 TopstepX API |
|---|---|---|
| Trading Combine | 可以 | **可以**，但**必須先在 Practice Account 測過** |
| Express Funded | 可以 | **可以** |
| Live Funded | 可以 | **不可以** —— API 限制只針對這一段 |

還有兩條，都不是建議而是規則：

- **「All trading activity must originate from your personal device.
  VPS, VPNs, and remote servers are prohibited.」**
  `bridge/live.py` 跑在你自己的電腦上。不能放雲端、不能放 VPS。
- **「malfunctions or errant trades are not reviewed」**
  程式出錯造成的虧損**沒有申訴管道**。preflight、journal、對帳不是好習慣，
  是這件事唯一的保險。

---

## 這套東西實際能賺多少（規劃用這個數字）

跑的是策略**原本寫定的完整時段**（掃針 09:30–15:00、進場截止 15:30、16:00 平倉）。

| | 樣本外 2024–2026 | 開發期 2016–2023 | 11 年合計 |
|---|---|---|---|
| 完整時段 | **484 筆 / +$13,274** | 1,451 筆 / −$17,668 | **−$4,394** |

### 為什麼不挑一個比較賺的收工時間

因為挑得出來，而且挑出來的數字不算數。我量過每一個候選：

| 收工(ET) | 樣本外 | 開發期 | 11 年合計 |
|---|---|---|---|
| 11:00 | +$12,200 | −$2,232 | **+$9,968** |
| 13:00 | +$15,217 | −$18,843 | −$3,626 |
| 15:30（原始規格） | +$13,274 | −$17,668 | −$4,394 |

11:00 那行是唯一 11 年為正的 —— **正因為它是我從逐時表裡挑出來的**。
兩段獨立期間對「開多久」給出相反的答案，代表那裡沒有訊號，只有雜訊。
所以引擎跑完整時段，**不挑時間，做完就走**。

### 上線前要知道自己在賭什麼

完整 11 年是 **−$3,044**（2,161 筆），11 年裡 **5 年虧損**，**累計從未轉正**。
把 11 年切成 126 個三個月視窗，**中位數獲利因子 0.996** —— 中位數的三個月是打平 ——
只有 49.2% 的視窗獲利，而當前視窗的 2.06 在第 **98** 百分位。

**上線是在賭 2024 年後的優勢是真的。** 不是勸阻，是讓三個月後的你
知道當初賭的是什麼。乾淨的檢驗只有 `scripts/forward_log.py`
（`FORWARD_START = 2026-08-29`）之後的新 K 棒。

## 步驟

**順序是被規則綁死的，不是我選的。** Practice Account 是免費的，但官方寫着
「**In order to have a Practice Account, you must have an active Trading Combine
subscription**」—— 沒有 Combine 就沒有 Practice。所以不可能「驗完再花錢」，
只能**花了錢、但先不要拿真的考試帳戶練習**。

| # | 做什麼 | 花費 | 過關條件 |
|---|---|---|---|
| 1 | 買 Combine + **API Access**（代碼 `topstep`），拿到 API 金鑰 | **~$100/月** | —— |
| 2 | Dashboard → Accounts → Add-ons → 啟用 **Practice Account** | 0 | —— |
| 3 | `--preflight --offline`（不連線，只驗設定與算術） | 0 | 全綠 |
| 4 | `TOPSTEPX_ACCOUNT_ID` 指向 **Practice**，`--preflight` | 0 | **餘額是 ~$150,000** |
| 5 | Practice 上 `--live` 真的送單 | 0 | journal 有 `placed` |
| 6 | `scripts/reconcile.py` | 0 | **連續乾淨** |
| 7 | `TOPSTEPX_ACCOUNT_ID` 換成 **Combine** | —— | —— |

金鑰要買了 API Access 才拿得到，所以離線檢查排在買之後 ——
它驗的是設定和括號單算術，**不需要連線，但需要金鑰存在**。
（別為了讓它跑而填一個假的金鑰：這個專案刻意不讓任何地方有預設密鑰，
因為假密鑰會在真正需要它的那天還是假的。）

### 月費是兩筆，不是一筆

| | |
|---|---|
| Trading Combine 50K | $85/月 |
| **TopstepX API Access** | **$29/月，用代碼 `topstep` 是 $14.50** |

API Access 由 ProjectX 分開計費（帳單顯示 Sim2Funded Solutions），
**沒有它就沒有 API 金鑰**，這座橋一行都跑不動。合計約 **$100/月**。

---

## Practice Account 到底在驗什麼

不是形式。**現在這套東西唯一還沒被驗證的部分全部在這一步**：

| 還沒驗證的 | 為什麼會有差異 |
|---|---|
| **七個端點從來沒碰過真的伺服器** | 全部照文件寫、全部只跑過 `DryRunBroker`。已知文件自相矛盾一處：`retrieveBars` 把 `contractId` 標成 integer，下單端點吃的是字串 |
| **資料源不是 Databento** | 回測用 GLBX.MDP3，live 是 TopstepX 自己的行情。掃針的門檻是**穿透一檔**（0.25），正是兩個資料源最容易不一致的地方 |
| **成交模型偏樂觀** | 回測是「碰到限價就成交」，真實限價單要排隊。這個偏差朝回測有利的方向，而且不會消失 |
| **括號單是券商的** | 回測把停損停利當逐根檢查的價位，ProjectX 的 bracket 是真委託單，有自己的 OCO、取整、跳空行為 |

**變成一份實作的只有訊號邏輯** —— live 跑的就是回測跑的那份
`strategies/ltf_sweep.py`，所以「兩份程式算出不同東西」那一類分歧沒有了。
訊號以下每一層都要在這裡驗。

這一步**不是在驗策略有沒有優勢**（那要三年，`scripts/forward_log.py` 在做），
是在驗**橋接有沒有把單送對**：口數、方向、括號單距離、進出場時間。
Combine 的月費從第 2 步就開始走，所以時間盒抓一週、五筆左右的訊號就夠。

### 3. 設定，不連線

```bash
export TOPSTEPX_USERNAME=...
export TOPSTEPX_API_KEY=...            # 只從環境變數讀，永遠不要寫進檔案
export TOPSTEPX_ACCOUNT_ID=...
export BRIDGE_CONTRACT_ID=CON.F.US.MNQ.Z26
export BRIDGE_LIVE_DATA=0              # Practice Account 用 sim 行情

uv run python -m bridge.live --preflight --offline
```

要看到 `bracket ticks  20.00 points at tick 0.25 -> 80 ticks (expected 80)`。
**80，不是 20。** 括號單的單位是跳動點數不是價格，填錯會得到一張 API 接受、
停損在幾千 tick 外的單。

### 4. 連上 Practice Account

```bash
uv run python -m bridge.live --preflight
```

**先看帳戶那一行的餘額。Practice 是 $150,000**（不管 Combine 買哪一檔）。
如果印出來是 $50,000，你指到的是考試帳戶 —— 停下來改 `TOPSTEPX_ACCOUNT_ID`。

另外三件事錯了都**不會報錯、只會安靜地錯**：

| 檢查 | 錯了會怎樣 |
|---|---|
| `bar feed` | 合約 ID 錯或 `BRIDGE_LIVE_DATA` 錯 → **回空陣列，不是錯誤** |
| `bar timestamps` | 若最新 K 棒不到 60 秒 → 這個行情源用**收盤**時間標，每個訊號會錯開一根 |
| `signals so far today` | 引擎在今天這段 K 棒上看到幾個設置 |

**驗證時用將來真的要跑的那組設定**（preset `Topstep 50K`、`--risk 1000`、
`BRIDGE_USE_GUARD=0`）—— 要驗的就是那組。Practice 餘額是 $150K 而
`BRIDGE_ACCOUNT_START` 是 50000，考試階段守衛關着所以無害；
**funded 階段守衛開着的時候這個不符會讓守衛算錯**，換帳戶時記得一起改。

Practice 的部位上限是 15 lots，遠高於我們的口數（$1,000 風險下實測 16 micros）。

### 5. Practice 上真的送單（官方要求的那一步）

```bash
uv run python -m bridge.live --live --journal data/bridge_journal.jsonl --risk 1000
```

Practice 上 `--live` 送的是模擬單，但走的是**完整的真實路徑**。
journal 第一行 `session_start` 會記下這一輪用的是哪個 `account_id` ——
Practice 和 Combine 共用同一支程式和同一個檔案，那一行是事後唯一分得出來的依據。

### 6. 對帳

```bash
uv run python scripts/reconcile.py --journal data/bridge_journal.jsonl \
  --start 2026-09-01 --end 2026-10-01 --risk 1000
```

差異分三類，意思完全不同：

| 類別 | 意思 | 該做什麼 |
|---|---|---|
| **data** | 進場價差一兩檔 | 行情源不同，正常 |
| **settings** | **口數不一樣** | 風險參數兩邊不一致，**每一筆倉位都算錯了。停。** |
| **logic** | 該進沒進 / 不該進卻進了 | 兩邊跑的不是同一份東西。**停。** |

**對帳連續乾淨之前不要把 `TOPSTEPX_ACCOUNT_ID` 換成 Combine。**
Practice 每天有 10 次免費重置，錯了重來不花錢；考試帳戶爆了要再買一次。

### 7. 換成 Combine，然後上線

改 `TOPSTEPX_ACCOUNT_ID`，**重跑 `--preflight` 確認餘額變成 $50,000**，
再開始跑。兩個階段設定不同，完整說明與數字在 `tradingview/README.md`
的「兩個階段，兩組設定」，這裡只列 bridge 這邊要設的：

| | **Combine（考試）** | **Express Funded** |
|---|---|---|
| `--risk` | **1000**（$50K 的 2%） | **250**（0.5%） |
| `BRIDGE_USE_GUARD` | **0** | **1** |
| `BRIDGE_SCALING_PLAN` | 0 | **1** |
| `BRIDGE_LIVE_DATA` | 1 | 1 |
| `BRIDGE_ACCOUNT_START` | 50000 | 帳戶實際餘額 |

考試階段把守衛關掉是反直覺的，理由在 `tradingview/README.md`：
守衛把爆倉換成「停手」，而停手既不通過也不爆倉、月費照走 ——
實測通過率從 35% 掉到 10%。考試 $85 買得回，funded 買不回，所以兩邊反過來。

---

## 電腦要怎麼安排

**21:25 UTC+8 喚醒，做完就自動關機。** 不設固定的關機時間。

單子掛出去之後，成交與停損停利都是券商在管，所以電腦只需要在
**訊號確認的那一刻**開著。引擎在當天的設置解決、而且**沒有任何掛單也沒有部位**
之後就乾淨結束 —— 實測 697 個交易日：

| | 固定開到 16:00 | **做完就關** |
|---|---|---|
| 平均每晚開機 | 6.6 小時 | **2.8 小時** |
| 中位關機時間 | 04:05 UTC+8 | **23:16 UTC+8** |

42.8% 的晚上 23:05 前就關了、69.3% 在 01:05 前、99% 在 03:35 前。

### 關機放在 wrapper，不在程式裡

引擎只負責退出碼：**`0` = 沒有任何東西還開著，關機是安全的**；
非 `0` = 有東西沒收乾淨，**不要關機**。

Windows（工作排程器，勾「喚醒電腦以執行此工作」，21:25 啟動）：

```bat
py -m bridge.live --live --risk 1000 && shutdown /h
```

macOS（`sudo pmset repeat wakeorpoweron MTWRF 21:25:00`）：

```bash
python -m bridge.live --live --risk 1000 && sudo pmset sleepnow
```

**不要把 `shutdown` 寫進交易程式。** 萬一哪天誤觸發，機器關了、螢幕黑了，
你連為什麼都看不到。放在 wrapper 裡它是看得見、改得掉、關得掉的，
而且 `&&` 只有在退出碼是 0 的時候才會執行右邊。

**電源設定要關掉自動更新重開機** —— 半夜重開會讓引擎在有部位的時候消失。

## 每天要看的三件事

1. **journal 有沒有 `error` / `cancel_failed`**
   ```bash
   grep -E '"event": "(broker_error|cancel_failed|tick_error|stale_feed)"' \
     data/bridge_journal.jsonl | tail
   ```
   `cancel_failed` 最嚴重：**有一張單留在券商那裡沒人管**。
2. **對帳有沒有 settings / logic 差異**
3. **距離損失上限還剩多少**（journal 的 `blocked` 行會寫 `equity` 與 `floor`）

昨晚有沒有乾淨收工，看 journal 最後一行是不是 `session_done`。
如果是 `entry_deadline` 或 `cancel_failed` 收尾，電腦不會自動關機 —— 那是刻意的。

## 什麼情況立刻停

- **口數對不上** —— 每一筆倉位都算錯了
- **該進沒進、不該進卻進了**，而 journal 沒有記錄原因
- **剩餘空間掉到單筆風險的 2 倍以內**
- **`bar timestamps` 檢查變紅** —— 訊號整體錯開一根 K 棒
- **`cancel_failed`** —— 先去平台上手動確認那張單

## 換月

`BRIDGE_CONTRACT_ID` 要改（`CON.F.US.MNQ.Z26` → 下一個季月）。
**改完一定要重跑 `--preflight`** ——
合約 ID 錯的症狀是 `bar feed` 回空陣列，不是錯誤訊息。
這是最容易犯又最貴的錯。
