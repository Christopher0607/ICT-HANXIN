# bridge: 本機訊號引擎 → TopstepX

## 政策（Topstep 2026-09-10 書面答覆，全文在 `docs/topstep_automation_enquiry.md`）

| 階段 | 自動化 | 透過 TopstepX API |
|---|---|---|
| Trading Combine | 可以 | **可以**（須先在 Practice Account 測過） |
| Express Funded | 可以 | **可以** |
| Live Funded | 可以 | **不可以** —— API 限制只針對這一段 |

兩條規則決定了這個資料夾長成現在這樣：

- **「All trading activity must originate from your personal device. VPS, VPNs,
  and remote servers are prohibited. A private server can store data, run
  research, or log activity, but it cannot place, modify, cancel, or route
  orders.」** —— 引擎跑在你自己的電腦上，沒有雲端、沒有 VPS、沒有隧道。
- **「malfunctions or errant trades are not reviewed」** —— 程式出錯的虧損沒有申訴管道。
  preflight、journal、對帳是這件事唯一的保險。

## 兩條路，走上面那條

| | `bridge/live.py`（**預設**） | `bridge/server.py`（舊路徑） |
|---|---|---|
| 訊號來源 | 本機跑 `strategies/ltf_sweep.py` | TradingView 警報 |
| K 棒來源 | TopstepX `/api/History/retrieveBars` | TradingView |
| 對外連接埠 | **不需要** | 需要（webhook 要進得來） |
| 每月成本 | $0 | Essential $12.95 + CME 即時行情 $7 |
| 和回測的關係 | **就是同一份程式** | Pine 是第二份實作，要持續對帳 |

`server.py` 留著沒刪，但預設路徑是 `live.py` ——
TradingView 的警報是從它的伺服器推出來的，要送進本機得再架一層隧道，
而訊號引擎本來就在本機、K 棒也拿得到，那層隧道沒有存在的理由。

**完整上線步驟在 `docs/GO_LIVE.md`。**

```bash
# 1. 送真單之前，先驗每一件會失敗的事（不下單）
uv run python -m bridge.live --preflight --offline   # 不連券商，只驗設定
uv run python -m bridge.live --preflight             # 連上去，驗 K 棒與帳戶

# 2. dry-run，每個決定都寫進 journal
uv run python -m bridge.live --journal data/bridge_journal.jsonl

# 3. 對帳：引擎實際做的 vs 研究引擎說該做的
uv run python scripts/reconcile.py --start 2026-09-01 --end 2026-10-01 --risk 1000

# 4. 對得上才加 --live
uv run python -m bridge.live --live --risk 1000
```

`--preflight` 會把**真正會送出去的訂單 JSON 印出來**，包含括號單的 tick 距離，
並且實際打一次 `retrieveBars` —— 合約 ID 錯或 `BRIDGE_LIVE_DATA` 錯的症狀是
**回空陣列，不是錯誤訊息**，那是上線後最貴又最難看出來的一種錯。

### 一天的作息

引擎在 **09:25–11:00 ET** 之間每分鐘輪詢一次（`BRIDGE_CUTOFF_MINUTE`），
到點就**取消未成交的掛單**並收工。單子掛出去之後成交與停損停利都由券商管，
所以電腦只需要在訊號確認的那一刻開著。

**11:00 取消這件事不能省。** 留在券商那裡的限價單會一路掛到時段結束，
可能在幾小時後、沒人看著的時候成交，而它的停損停利是為一個早就不存在的行情算的。
實測（2024–2026）11:00 準時取消幾乎不花錢：74.7% 的獲利留下來，
而放著不管是 76.1%。

`--preflight` 會把**真正會送出去的訂單 JSON 印出來**，包含括號單的 tick 距離 ——
那是唯一能在下第一張真單之前用肉眼確認的地方。20 點在 0.25 的 tick 上是 80 ticks，
不是 20，也不是 20.00。填錯會得到一張 API 接受、停損在幾千 tick 外的單。

`--journal`（預設 `data/bridge_journal.jsonl`）每個決定寫一行 ——
**包含沒有下單的那些**。「上個月守衛擋掉了 14 個訊號」這種事在對帳單上看不出來，
在這裡一眼就看到。

`scripts/reconcile.py` 把差異分成三類，因為它們的意思完全不同：

| 類別 | 意思 | 該做什麼 |
|---|---|---|
| **data** | 進場價差一兩檔 | TradingView 的資料源不是 Databento，正常，不用管 |
| **settings** | **口數不一樣** | 風險設定兩邊不一致，**每一筆倉位都算錯了。停。** |
| **logic** | 一邊有單另一邊沒有，且 journal 沒有記錄原因 | 兩邊跑的不是同一份東西。**停。** |

守衛擋掉的訊號不算差異 —— 那是守衛在做它該做的事，journal 有記原因，
會另外列出來。

## 跑起來

```bash
export TOPSTEPX_USERNAME=...          # TopstepX 帳號
export TOPSTEPX_API_KEY=...           # 只從環境變數讀，永遠不要寫進檔案
export TOPSTEPX_ACCOUNT_ID=...        # 數字帳戶 ID
export BRIDGE_WEBHOOK_SECRET=...      # 自己產一個長字串
export BRIDGE_CONTRACT_ID=CON.F.US.MNQ.Z26
export BRIDGE_PRESET="Topstep 50K"

uv run python -m bridge.server                 # dry run（預設）
uv run python -m bridge.server --live          # 真的送單
```

**`--live` 必須顯式加。** 沒加的話什麼都不會離開這個行程，
只會把「本來要送什麼」記到 log 裡。

TradingView 那邊：Alerts → Webhook URL 填 `http://<你的位址>:8787`，
Message 留空（腳本用 `alert_message` 自己帶 JSON），
Trigger 選 **Once Per Bar Close**。

密鑰可以放 header `X-Webhook-Secret`，
或者在 Pine 的 `alertTemplate` 裡加一個 `"secret":"..."` 欄位 ——
TradingView 不是每個方案都能設自訂 header。兩種都是常數時間比對。

## 守衛層在防什麼

`bridge/guards.py` 在訊號和訂單之間強制執行 prop firm 的帳戶規則。
走 `live.py` 時它防的不再是「被竄改的圖表」，而是**引擎自己**：
風險參數填錯、餘額比想像中低、同一個設置被算了兩次。

規則在 `tradingview/README.md` 的「四條規則」，數字和 Pine 的預設表同一組 ——
`tests/test_bridge.py` 會**從 Pine 原始碼把數字讀出來比對**，
改了一邊沒改另一邊測試就會紅，而不是靜悄悄開一個洞。

**重放防護**：`live.py` 用訊號自己的確認時間當識別碼，
所以同一個設置在後續每一分鐘重跑時只會下單一次。
`tests/test_live.py` 逐分鐘重播一整天來證明這件事 ——
已確認的單價格從不改變，否則就會變成 Pine v4 那種沒人取消得掉的孤兒訂單。

**密鑰**（只有 `server.py` 需要）：常數時間比對，不合就 401。

## 兩個很容易寫錯、而且看起來會像成功的地方

**括號單的單位是「跳動點數」不是價格。**
ProjectX 的 `stopLossBracket.ticks` 是距離進場價的 tick 數。
直接把停損價格塞進去，會把停損放到幾千個 tick 之外，而且 API 會回成功。
`bracket_ticks()` 負責換算，並且拒絕小於一個 tick 的距離。

**`side` 和 `type` 都是裸整數。** `side` 0=買 1=賣，`type` 1=限價。
填錯是一張合法的、方向相反的單。

## 已查證 vs 沒查證

| 端點 | 狀態 |
|---|---|
| `POST /api/Auth/loginKey` | 對過官方文件 |
| `POST /api/Order/place` | 對過官方文件（含 enum 和 bracket 結構） |
| `POST /api/Account/search` | **沒查證** —— 建議直接設 `TOPSTEPX_ACCOUNT_ID` |

## 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `TOPSTEPX_USERNAME` | 必填 | |
| `TOPSTEPX_API_KEY` | 必填 | 只從環境變數讀 |
| `TOPSTEPX_ACCOUNT_ID` | 必填（送單時） | |
| `TOPSTEPX_BASE_URL` | `https://api.topstepx.com` | |
| `BRIDGE_WEBHOOK_SECRET` | 只有 `server.py` 需要 | `live.py` 沒有對外接口，不會讀它 |
| `BRIDGE_CONTRACT_ID` | `CON.F.US.MNQ.Z26` | 換月要改 |
| `BRIDGE_TICK_SIZE` | `0.25` | |
| `BRIDGE_POINT_VALUE` | `2.0` | MNQ=2、NQ=20 |
| `BRIDGE_PRESET` | `Topstep 50K` | 和 Pine 的預設表同一組數字 |
| `BRIDGE_ACCOUNT_START` | `50000` | |
| `BRIDGE_SAFETY_MULT` | `1.5` | 距離損失上限少於 N 倍單筆風險就不開新倉 |
| `BRIDGE_USE_GUARD` | `1` | 設 `0` 關掉損失上限與當日上限的封鎖。**考試階段設 0**（見 `tradingview/README.md` 的兩階段設定），funded 保持 1。獲利目標的停手不受這個開關影響。 |
| `BRIDGE_SCALING_PLAN` | `0` | 設 `1` 啟用 Express Funded 的 Scaling Plan 口數上限（20/30/50 跟餘額走）。**funded 階段設 1**，考試階段維持 0（考試是固定 50 micros）。 |
| `BRIDGE_LIVE_DATA` | `0` | `retrieveBars` 要讀哪個行情訂閱。**Practice Account 用 `0`，正式帳戶用 `1`。**填錯回的是空陣列，不是錯誤。 |
| `BRIDGE_CUTOFF_MINUTE` | `660`（11:00 ET） | 到點停止下新單並取消未成交的掛單 |
| `BRIDGE_MAX_BAR_AGE_S` | `150` | 最新 K 棒超過這個秒數就停手不下單（擋延遲行情） |
