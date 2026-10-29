# Windows 部署清單

照著做。**順序是刻意的：免費、可逆的排在花錢、不可逆的前面。**

這份是 Windows 專用。`docs/GO_LIVE.md` 是決策與數字的出處，這裡只講怎麼在
Windows 上把它跑起來 —— 同一件事寫兩遍遲早會分叉，所以凡是「為什麼」都指過去。

> **Remote Control 只支援 Mac / Linux**，所以 Windows 全程是自己在本機跑指令。
> 這不是降級：Topstep 的規則本來就要求
> 「All trading activity must originate from your personal device」，
> VPS、VPN、遠端主機一律禁止。

---

## 階段 A：不用 Topstep 帳號，$0

這一整段跑完，你會知道你的機器算出來的東西跟開發環境**逐位相同**。
買帳號之前先做完。

### A1. 裝 uv

PowerShell（**不需要**系統管理員）：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

裝完**關掉 PowerShell 再開一個**（讓 PATH 生效），`uv --version` 要有輸出。
沒有 git 的話：<https://git-scm.com/download/win>

### A2. 取回程式碼

```powershell
cd $HOME
git clone https://github.com/Christopher0607/ICT-HANXIN.git
cd ICT-HANXIN
git checkout claude/ict-trading-resources-ugh07b
```

`data/raw` 裡有 **101 MB** 的 parquet，這一步會慢，是正常的。

### A3. 裝相依套件

```powershell
uv sync --extra dev --extra data
```

`--extra data` 裝的是 `databento`，只有 `scripts/fetch_databento.py` 要用。
少了它拉資料會 `ModuleNotFoundError`。

### A4. 跑測試 —— 驗機器的第一道

```powershell
uv run pytest -q
```

**必須是 `455 passed`。** 有任何一個失敗就停下來，不要往下走。

### A5. 重建行情資料

```powershell
uv run python scripts/ingest.py
```

最後幾行**必須**長這樣，`0.0000000000` 一個字都不能差：

```
  ema12: max abs diff 0.0000000000 over 989,313 bars
  atr14: max abs diff 0.0000000000 over 989,311 bars
  1m ends 2026-10-28 23:59:00+00:00, 5m ends 2026-10-28 23:55:00+00:00 (lag 0 days 00:04:00)
```

它在寫檔前會自己驗出廠範圍的指標有沒有被改動。**它報錯就不要繞過。**

### A6. 複現前瞻記錄 —— 驗機器最關鍵的一道

```powershell
uv run python scripts/forward_log.py --dry-run
```

**必須和開發環境一模一樣：**

```
  2026-08-31 -> 2026-10-28   28 trading days, 28 trades
  net P&L              $+507.97
  mean per trade       $+18.14
  win rate             53.6%
```

對得上 = 你的機器和開發環境算出來的東西完全相同。
之後對帳出現的任何差異，就只可能來自券商那一側，不會是環境問題。
**對不上就停。**

> 已於 2026-10-29 在 `C:\Users\...\ICT-HANXIN` 實跑通過。

---

## ⚠ PowerShell 的 `set` 不會設環境變數

這一條單獨開一節，因為它**不報錯**。

PowerShell 裡 `set` 是 `Set-Variable` 的別名。打

```powershell
set BRIDGE_USE_GUARD=0          # ← 沒有用
```

建出來的是一個名叫 `BRIDGE_USE_GUARD=0` 的 **PowerShell 變數**，
`os.environ` 看不到它。沒有錯誤訊息。

| | 設環境變數的寫法 |
|---|---|
| **PowerShell** | `$env:BRIDGE_USE_GUARD = "0"` |
| **cmd / `.bat`** | `set BRIDGE_USE_GUARD=0` |

**為什麼這件事會花錢**：`TOPSTEPX_USERNAME` 沒設會 `RuntimeError`，看得見。
但 `BRIDGE_USE_GUARD` 在 `bridge/config.py` 裡**預設是 `1`** ——
沒設成 `0` 就是守衛開著跑，而那正是 `docs/GO_LIVE.md` 決定不要的配置
（實測把通過率從 35% 壓到 10%），**而且全程不會報錯**。

預設值刻意是 `1`：忘記設變數的代價是少賺，不是爆帳戶。關掉它是一個決定，
所以必須明寫。

### 自檢（不需要金鑰，現在就能跑）

```powershell
uv run python -c "import os; print({k: v for k, v in os.environ.items() if k.startswith('BRIDGE_')})"
```

要印出 **8 個** `BRIDGE_*`，而且 `BRIDGE_USE_GUARD` 是 `'0'`。
印出 `{}` 就是沒生效。

---

## A7. 建 `run_bridge.bat`

複製 `run_bridge.bat.example` 成 `run_bridge.bat`（已在 `.gitignore` 裡）。
用記事本存的話，存檔類型要選「所有檔案」，**別存成 `run_bridge.bat.txt`**。

裡面**只有非機密項**。三個憑證走 Windows 使用者環境變數，不進任何檔案。

兩階段的差別只有一項，出處是 `docs/GO_LIVE.md` 的兩階段表：

| | Combine（考試） | Express Funded |
|---|---|---|
| `BRIDGE_USE_GUARD` | **0** | **0**（兩階段都關） |
| `BRIDGE_SCALING_PLAN` | 0 | **1** |
| `BRIDGE_LIVE_DATA` | Practice 用 `0`，正式帳戶 `1` | 1 |

`BRIDGE_LIVE_DATA` 填錯的症狀是 `retrieveBars` 回**空陣列**而不是錯誤，
所以它不會自己跳出來告訴你。

## A8. 工作排程器 —— 建好但**先停用**

- 觸發器：每天 **21:25**（UTC+8），勾 **「喚醒電腦以執行這個工作」**
- 動作：`run_bridge.bat`，起始位置填儲存庫根目錄
- 建完 **右鍵 → 停用**

現在就順手做掉：**關掉 Windows 自動更新重新開機**。
半夜重開會讓引擎在有部位的時候直接消失。

關機要放在 wrapper 的 `&&` 右邊，不要寫進交易程式 —— 理由見
`docs/GO_LIVE.md` 的〈關機放在 wrapper，不在程式裡〉。

---

## 階段 B：買了帳號之後

順序被 Topstep 的規則綁死：**沒有 active Combine 訂閱就沒有 Practice Account**，
所以不可能「驗完再花錢」，只能花了錢、但先不要碰真的考試帳戶。

| # | 做什麼 | 花費 |
|---|---|---|
| B1 | 買 Combine（**Standard**）+ **API Access**（代碼 `topstep`），拿金鑰 | ~$64/月 |
| B2 | Dashboard → Accounts → Add-ons → 啟用 **Practice Account** | 0 |
| B3 | 三個憑證設進**使用者環境變數** | 0 |
| B4 | `--preflight --offline` | 0 |
| B5 | `--preflight`（連 Practice） | 0 |
| B6 | 不帶 `--live` 跑一天 | 0 |
| B7 | `--live` 在 Practice 真送單 | 0 |
| B8 | `scripts/reconcile.py` 跑到**連續乾淨** | 0 |
| B9 | 帳戶 ID 換成 Combine | —— |

### B3：憑證進使用者環境變數，不進檔案

系統內容 → 環境變數 → **使用者變數**，新增三個：

```
TOPSTEPX_USERNAME
TOPSTEPX_API_KEY
TOPSTEPX_ACCOUNT_ID
```

設完**要重開 PowerShell** 才讀得到。

**不要把金鑰寫進 `run_bridge.bat`。** 一個會設金鑰的 `.bat` 就是一個裝著金鑰的檔案 ——
這個專案到處都遵守「金鑰只從環境變數讀」，不要在最後一步破例。

### B4-B9：`--live` 是最後才加的旗標

`--live` 是**真送單**。不帶它走的是 `DryRunBroker`，什麼都不會送出去。
它是 B7 和成品 `.bat` 裡的旗標，**不是除錯用的**。

```powershell
uv run python -m bridge.live --preflight --offline --risk 900
uv run python -m bridge.live --preflight --risk 900            # B5：餘額要是 ~$150,000
uv run python -m bridge.live --risk 900                        # B6：乾跑，不送單
uv run python -m bridge.live --live --risk 900                 # B7：Practice 真送單
uv run python scripts/reconcile.py --start <起> --end <迄> --risk 900
```

`--preflight --offline` **需要金鑰存在才跑得起來**：`bridge/config.py` 對
`TOPSTEPX_API_KEY` 沒有預設值，缺了直接 `RuntimeError`。
**別為了讓它跑去填一個假的** —— 這個專案刻意不讓任何地方有預設密鑰，
因為假密鑰在真正需要它的那天還是假的。

對帳最容易誤判的一關是口數，理由和實測數字在
`docs/GO_LIVE.md` 的〈口數不是一個固定的數字〉。

---

## 換月

`BRIDGE_CONTRACT_ID` 要跟著換，見 `docs/GO_LIVE.md` 的〈換月〉。
留在上一季的合約代碼會送出一個 API 會接受、但沒有人想要的部位。
