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

| | 樣本外 2024–2026 | 開發期 2016–2023 |
|---|---|---|
| 回測原始數字 | 550 筆 / **+$21,957** | 1,611 筆 / −$25,001 |
| 11:00 下班、取消未成交 | 350 筆 / +$16,409 | 908 筆 / −$10,150 |
| **再扣掉本機拿不到的第一根 K 棒** | **300 筆 / +$12,200** | 793 筆 / −$2,232 |

最後一行是**規劃數字**。回測允許限價單在確認那一根 K 棒之內成交
（確認發生在該根的開盤瞬間，所以不是偷看未來，但那是零延遲的假設）。
本機引擎要等那根收盤才看得到，所以吃不到 —— 樣本外那是 14.3% 的交易、$4,209。
`bridge/live.py` 的輪詢設在每分鐘 :02 秒，把延遲壓到幾秒而不是一整分鐘，
搶回來多少算意外之財，**不要算進預期**。

### 上線前要知道自己在賭什麼

完整 11 年是 **−$3,044**，11 年裡 **5 年虧損**，**累計從未轉正**。
當前三個月視窗落在第 99 百分位。

上面那些截斷後為正的數字，**截止時間是看過逐時拆解之後挑的** —— 是選擇偏差，
不是發現。乾淨的檢驗只有 `scripts/forward_log.py`（`FORWARD_START = 2026-08-29`）
之後的新 K 棒。

**上線是在賭 2024 年後的優勢是真的。** 這不是勸阻，是讓三個月後的你
知道當初賭的是什麼。

---

## 步驟

| # | 做什麼 | 花費 | 過關條件 |
|---|---|---|---|
| 1 | `--preflight --offline` | 0 | 全綠 |
| 2 | 開 TopstepX **Practice Account**，`--preflight` | 0 | 訂單 JSON 與 K 棒檢查都綠 |
| 3 | Practice 上真的送單 | 0 | journal 有 `placed` |
| 4 | `scripts/reconcile.py` | 0 | **連續乾淨** |
| 5 | 買 Combine | $85/月 | —— |
| 6 | `--live` | —— | —— |

### 1. 設定，不連線

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

### 2. 連上 Practice Account

```bash
uv run python -m bridge.live --preflight
```

除了上面那些，這次會多驗三件事，**每一件錯了都不會報錯、只會安靜地錯**：

| 檢查 | 錯了會怎樣 |
|---|---|
| `bar feed` | 合約 ID 錯或 `BRIDGE_LIVE_DATA` 錯 → **回空陣列，不是錯誤** |
| `bar timestamps` | 若最新 K 棒不到 60 秒 → 這個行情源用**收盤**時間標，每個訊號會錯開一根 |
| `signals so far today` | 引擎在今天這段 K 棒上看到幾個設置 |

### 3. Practice 上真的送單（官方要求的那一步）

```bash
uv run python -m bridge.live --live --journal data/bridge_journal.jsonl --risk 1000
```

Practice Account 上 `--live` 送的是模擬單，但走的是**完整的真實路徑**。
這一步官方明文要求，也是唯一能在真錢之前發現路徑問題的機會。

至少跑到累積出幾筆成交。

### 4. 對帳

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

**對帳連續乾淨之前不要買 Combine。**
Combine 在 2% 風險下期望耗時只有 0.3 個月，花月費 debug 是把成本結構弄反。

### 5–6. Combine 與上線

兩個階段設定不同。完整說明與數字在 `tradingview/README.md`
的「兩個階段，兩組設定」，這裡只列 bridge 這邊要設的：

| | **Combine（考試）** | **Express Funded** |
|---|---|---|
| `--risk` | **1000**（$50K 的 2%） | **250**（0.5%） |
| `BRIDGE_USE_GUARD` | **0** | **1** |
| `BRIDGE_SCALING_PLAN` | 0 | **1** |
| `BRIDGE_LIVE_DATA` | 1 | 1 |

考試階段把守衛關掉是反直覺的，理由在 `tradingview/README.md`：
守衛把爆倉換成「停手」，而停手既不通過也不爆倉、月費照走 ——
實測通過率從 35% 掉到 10%。考試 $85 買得回，funded 買不回，所以兩邊反過來。

---

## 電腦要怎麼安排

**09:25–11:05 ET = 21:25–23:05 UTC+8**（夏令時；冬令時各加一小時）。

單子掛出去之後，成交與停損停利都是券商在管，所以電腦只需要在**訊號確認的那一刻**
開著。11:00 之後引擎會取消未成交的掛單並收工。

- **Windows**：工作排程器建立兩個工作，勾「喚醒電腦以執行此工作」，
  21:25 啟動、23:10 `shutdown /h`
- **macOS**：`sudo pmset repeat wakeorpoweron MTWRF 21:25:00 sleep MTWRF 23:10:00`
- **BIOS**：也可以用 RTC Wake

**電源設定要關掉睡眠**，還有**關掉自動更新重開機** ——
半夜重開機會讓引擎在有部位的時候消失。

---

## 每天要看的三件事

1. **journal 有沒有 `error` / `cancel_failed`**
   ```bash
   grep -E '"event": "(broker_error|cancel_failed|tick_error|stale_feed)"' \
     data/bridge_journal.jsonl | tail
   ```
   `cancel_failed` 最嚴重：**有一張單留在券商那裡沒人管**。
2. **對帳有沒有 settings / logic 差異**
3. **距離損失上限還剩多少**（journal 的 `blocked` 行會寫 `equity` 與 `floor`）

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
