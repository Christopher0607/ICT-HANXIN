# Webhook bridge: TradingView → TopstepX

## ⚠ 政策：三個階段，只有最後一段禁止

Topstep 的帳戶分三段：**Trading Combine → Express Funded → Live Funded**。

**自動化在 Combine 和 Express Funded 可以，在 Live Funded Account 禁止**
（同時禁用 VPS / VPN / 遠端伺服器）。

也就是說這座橋在前兩段都用得上，包括開始出金的 Express Funded ——
只有走到 Live Funded 那一步要改手動。

我查到的是二手來源，2026 年內政策改過幾次。
**上線前跟客服要一份書面確認，三個階段分開問**，
特別是「Express Funded 可以用 ProjectX API 自動下單嗎」這一句。

這座橋預設 `--dry-run`。

---

## 上線順序

```bash
# 1. 送真單之前，先驗每一件會失敗的事（不下單）
uv run python -m bridge.server --preflight
uv run python -m bridge.server --preflight --offline   # 不連券商，只驗設定

# 2. dry-run 跑一個月，每個決定都寫進 journal
uv run python -m bridge.server

# 3. 對帳：橋接實際做的 vs 研究引擎說該做的
uv run python scripts/reconcile.py --start 2026-09-01 --end 2026-10-01 --risk 1000

# 4. 對得上、政策也確認了，才加 --live
uv run python -m bridge.server --live
```

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

## 這座橋做了三件圖表不能被信任去做的事

**一、重新檢查一次所有 prop firm 規則。**
圖表可能沒重新載入、input 可能被手動改過、
知道 URL 的人可以重放 webhook。`bridge/guards.py` 是 Pine 裡
`propBlock` 的獨立第二份實作，`tests/test_bridge.py` 會**從 Pine 原始碼把數字讀出來比對**
—— 改了一邊沒改另一邊，測試就會紅，而不是靜悄悄開一個洞。

**二、拒絕重放。** TradingView 的警報會重送，
這裡重複一次就是重複一個部位。用訊號自己的時間戳（不是到達時間）去重。

**三、驗密鑰。** 常數時間比對，不合就 401。

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

## 上線流程

1. 跟 Topstep 要自動化政策的書面確認
2. `--dry-run` 跑滿一個月，每天拿 log 和 TradingView 的 Strategy Tester 對帳
3. 對得起來、政策也允許，才談 `--live`
4. `--live` 第一週用最小口數

## 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `TOPSTEPX_USERNAME` | 必填 | |
| `TOPSTEPX_API_KEY` | 必填 | 只從環境變數讀 |
| `TOPSTEPX_ACCOUNT_ID` | 必填（送單時） | |
| `TOPSTEPX_BASE_URL` | `https://api.topstepx.com` | |
| `BRIDGE_WEBHOOK_SECRET` | 必填 | |
| `BRIDGE_CONTRACT_ID` | `CON.F.US.MNQ.Z26` | 換月要改 |
| `BRIDGE_TICK_SIZE` | `0.25` | |
| `BRIDGE_POINT_VALUE` | `2.0` | MNQ=2、NQ=20 |
| `BRIDGE_PRESET` | `Topstep 50K` | 和 Pine 的預設表同一組數字 |
| `BRIDGE_ACCOUNT_START` | `50000` | |
| `BRIDGE_SAFETY_MULT` | `1.5` | 距離損失上限少於 N 倍單筆風險就不開新倉 |
| `BRIDGE_USE_GUARD` | `1` | 設 `0` 關掉損失上限與當日上限的封鎖。**考試階段設 0**（見 `tradingview/README.md` 的兩階段設定），funded 保持 1。獲利目標的停手不受這個開關影響。 |
| `BRIDGE_SCALING_PLAN` | `0` | 設 `1` 啟用 Express Funded 的 Scaling Plan 口數上限（20/30/50 跟餘額走）。**funded 階段設 1**，考試階段維持 0（考試是固定 50 micros）。 |
