# Webhook bridge: TradingView → TopstepX

## ⚠ 先確認政策，再上線

**Topstep 在 Live Funded Account 禁止透過 ProjectX API 自動交易，
而且禁用 VPS / VPN / 遠端伺服器。**
Apex 的來源互相矛盾，最清楚的說法同樣是「評估階段可以、funded 禁止」。

意思是：**你能自動化的是評估階段，賺錢那一段可能要手動。**
而且**把這支程式跑在雲端主機上，本身就可能違反 Topstep 的規則**。

我查到的都是二手評測站（不少帶聯盟行銷），2026 年內政策改過好幾次。
**先跟客服要書面確認，Combine 和 Funded 分開問。**

這座橋預設 `--dry-run`，就是為了讓你在等答覆的期間也能把管線跑通、對帳。

---

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
