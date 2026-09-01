# 土哥 (Mete Kaplan) 免費課程 — 學習清單

課程來源: <https://metekaplan.com/education/>
原始表格: [Google Sheet](https://docs.google.com/spreadsheets/d/1gFV7SOXT0Xh2-j-RnWJ71L2CaPoSZzSOifIYw4Zxwp4/edit?gid=0#gid=0)

> 作者原話: *"All price action concepts are interconnected and used with each other.
> You should be watching all videos in order without skipping. Otherwise, you will
> definitely be confused."* — 依序看完，不要跳。

`實作` 欄標示本 repo 中對應的程式碼。空白代表該堂課是紀律/風控觀念，沒有對應的偵測器。

## 40 堂課

| # | 主題 | 影片 | 實作 |
|---|---|---|---|
| 001 | MSB - Market Structure Break | [1](https://youtu.be/JAGgL40_ntI) [2](https://youtu.be/ogZTy62r5CY) | `ict/structure.py — find_msb` |
| 002 | OB - Order Block | [1](https://youtu.be/ZSY2cKNz-dg) [2](https://youtu.be/qasQHNDgJ8Q) [3](https://youtu.be/jOmu59ztcBs) [4](https://youtu.be/Hko5_x0B8TU) | `ict/orderblocks.py — find_order_blocks` |
| 003 | IMB - Imbalance | [1](https://youtu.be/prPjDV8cKGE) [2](https://youtu.be/Hko5_x0B8TU) | `ict/fvg.py — find_fvgs` |
| 004 | QML - Quasimodo | [1](https://youtu.be/yVwn5Jp-BMM) [2](https://youtu.be/_kt6Kr4IVCw) | `ict/patterns.py — find_qml` |
| 005 | BB - Breaker Block | [1](https://youtu.be/V_fqUAbN488) [2](https://youtu.be/oWb2kdreIKs) | `ict/orderblocks.py — find_breakers` |
| 006 | OTE - Fibonacci Optimal Trade Entry | [1](https://youtu.be/Mr5ol4EpWVU) [2](https://youtu.be/GT_vsiZHbok) [3](https://youtu.be/T5hfO4JFJv4) | `ict/levels.py — DealingRange.ote_zone` |
| 007 | PREMIUM & DISCOUNT | [1](https://youtu.be/DQfWwIAXECM) [2](https://youtu.be/kDVseXHNZlY) [3](https://youtu.be/T5hfO4JFJv4) | `ict/levels.py — is_premium / is_discount` |
| 008 | ROTE - Reverse Fibonacci Optimal Trade Entry | [1](https://youtu.be/Rc4bBnvfRe0) [2](https://youtu.be/PWx1bNjFEMU) [3](https://youtu.be/VSK5_a7rLew) | `ict/levels.py — DealingRange.retracement` |
| 009 | TRAPS - Stop Loss Hunting | [1](https://youtu.be/pAmEk8GWTXo) [2](https://youtu.be/Jp7lI4CD5kA) [3](https://youtu.be/0eya2UedNdg) [4](https://youtu.be/EEHPvx-Q8Wc) | `ict/liquidity.py — find_sweeps` |
| 010 | SFP - Swing Failure Pattern | [1](https://youtu.be/xqYHYTIFTjk) [2](https://youtu.be/G3Va9GGyQ6U) | `ict/liquidity.py — find_sfp` |
| 011 | BREAKERS | [1](https://youtu.be/h0sNjKO_8P4) [2](https://youtu.be/aYLn_op60o4) | `ict/orderblocks.py — find_breakers` |
| 012 | PO3 - Power of Three | [1](https://youtu.be/eHcH4Oriczg) [2](https://youtu.be/pwvcYW2mKNs) | `ict/po3.py — find_po3` |
| 013 | S/R FLIPS - Support Resistance Flips | [1](https://youtu.be/_Qbg2gxLVo8) [2](https://youtu.be/RPNvxycfgVs) | `ict/patterns.py — find_sr_flips` |
| 014 | RBR - DBD Rally Base Rally / Drop Base Drop | [1](https://youtu.be/y9F1DVMdV0o) [2](https://youtu.be/dQR_BD5RcfU) | `ict/orderblocks.py — find_order_blocks` |
| 015 | RBD - DBR Rally Base Drop / Drop Base Rally | [1](https://youtu.be/gESxrabDtd4) [2](https://youtu.be/gOsYhTFUwDg) | `ict/orderblocks.py — find_order_blocks` |
| 016 | MiB - Mitigation Block | [1](https://youtu.be/iyzlkJGfeDw) [2](https://youtu.be/HVJwhZUG0Ms) [3](https://youtu.be/Lj-NyoxGmk0) | `ict/orderblocks.py — find_mitigation_blocks` |
| 017 | GPX - Gameplay Box Scalping with VWAP | [1](https://youtu.be/fLmpVhXZo9Q) [2](https://youtu.be/qHjsBLWPa1E) [3](https://youtu.be/MJHNKqLp4Gc) | — |
| 018 | RANGE + DEVIATION | [1](https://youtu.be/rQ2bokoMdww) [2](https://youtu.be/K4bniSRieaI) [3](https://youtu.be/R7yOFlRnypM) [4](https://youtu.be/3KUJsg8ELgI) [5](https://youtu.be/VazJqpXYy1U) | `ict/patterns.py — find_range_deviations` |
| 019 | TT3 - Three Tap Concept | [1](https://youtu.be/U1kURfpziwc) [2](https://youtu.be/Y03RUTAbyWM) | `ict/patterns.py — find_three_taps` |
| 020 | FTR / IC / FTB - Failed to Return | [1](https://youtu.be/vMBmaPimedw) [2](https://youtu.be/ur1HiEf1Eio) | `ict/patterns.py — find_ftr` |
| 021 | HIGH & LOW Probability Trades | [1](https://youtu.be/c7I1qt2Q3HU) [2](https://youtu.be/3t5UmXgnzSM) [3](https://youtu.be/lfPtDU2pwFk) | — |
| 022 | 10 Magic Rules of Trading | [1](https://youtu.be/xb8tTJN-Yyw) | — |
| 023 | Superior Risk Management | [1](https://youtu.be/ENxSWo5_Ok8) | — |
| 024 | Regular SMC Setups | [1](https://youtu.be/scktbEIhjpw) | — |
| 025 | Sniper Scalping Mastery Series (1 Minute) New York Opening Mastery (9:30 AM Scalping) LIQUIDITY & INDUCEMENT SCALPING | [1](https://youtu.be/NIHWxhXs0Bg) [2](https://youtu.be/3oGhJmgyAB8) [3](https://youtu.be/bxmBTRitZ4E) [4](https://youtu.be/cNUPMveg9Ts) [5](https://youtu.be/Y3u2_WeXYC0) [6](https://youtu.be/XgYiiCUk7wc) [7](https://youtu.be/BV1oy17WbFw) [8](https://youtu.be/Bzd5nPJ3Orc) [9](https://youtu.be/zqfT-b1VhHo) [10](https://youtu.be/SmLMAPKLhFU) [11](https://youtu.be/oGSNhEagWMo) [12](https://youtu.be/vpEa503OwIE) | — |
| 026 | How to PREDICT RALLIES? | [1](https://youtu.be/CO7dbqsB8VQ) [2](https://youtu.be/VzXwWPgdkYY) [3](https://youtu.be/e0Rh8zCbrqs) | — |
| 027 | How to PREDICT TREND REVERSALS? | [1](https://youtu.be/gj1Lixw29RI) [2](https://youtu.be/e0Rh8zCbrqs) | — |
| 028 | SBB - Super Breaker Block | [1](https://youtu.be/UwIIAUyc2Ic) [2](https://youtu.be/dqbCMNh-I2s) | `ict/orderblocks.py — find_order_blocks (has_fvg)` |
| 029 | Biggest Trader Mistakes | [1](https://youtu.be/0JMEkVzaIxY) | — |
| 030 | Order Blocks (Reverse Fractal Strategy) | [1](https://youtu.be/dSyMFzUJO_c) [2](https://youtu.be/tizc4RFpZjc) [3](https://youtu.be/g5FPFYZZOic) [4](https://youtu.be/AgnP0aHWLd0) [5](https://youtu.be/gYLrgpicvk4) [6](https://youtu.be/Yby0ieHw2bw) [7](https://youtu.be/6uWqWyNU8wY) [8](https://youtu.be/eyjzgqx4-1M) | `ict/orderblocks.py — find_order_blocks` |
| 031 | BEST Time Frames to Trade | [1](https://youtu.be/gWP0PFNZraU) | — |
| 032 | BPR - Balanced Price Range (RALLY STARTER) | [1](https://youtu.be/Qn7gyWXSFwY) [2](https://youtu.be/gYLrgpicvk4) [3](https://youtu.be/Yby0ieHw2bw) [4](https://youtu.be/VzXwWPgdkYY) [5](https://youtu.be/e0Rh8zCbrqs) [6](https://youtu.be/ZLszXXoopYk) | `ict/fvg.py — find_bpr` |
| 033 | SOB - Super Order Blocks | [1](https://youtu.be/huG_kzgh3QM) [2](https://youtu.be/KCr_BTe6nIA) [3](https://youtu.be/eyjzgqx4-1M) [4](https://youtu.be/VMwUOJ_2SmI) | `ict/orderblocks.py — find_order_blocks (super_order_block)` |
| 034 | Market Structuve VS Liq+Imb Combo | [1](https://youtu.be/wgHDwi7Z5_Y) [2](https://youtu.be/eyjzgqx4-1M) [3](https://youtu.be/VmvbtqmIN7M) [4](https://youtu.be/DTDvC2YTQ3A) [5](https://youtu.be/20rE_GZSq40) | — |
| 035 | SESSION TRADING | [1](https://youtu.be/bdTi-s_0jvQ) [2](https://youtu.be/PWIMO1QWJpk) [3](https://youtu.be/oGSNhEagWMo) [4](https://youtu.be/vpEa503OwIE) [5](https://youtu.be/fiIFFJD6Mz0) | `ict/sessions.py — KILLZONES / session_ranges` |
| 036 | Best Trading Strategy (KING of SMART MONEY) | [1](https://youtu.be/DXDM8kFJ_h0) [2](https://youtu.be/PWIMO1QWJpk) [3](https://youtu.be/fiIFFJD6Mz0) [4](https://youtu.be/JieDC7uGnHo) | — |
| 037 | Finding BIAS 1 | [1](https://youtu.be/_1HMAweR6Hs) [2](https://youtu.be/uOHCPKl0Sm8) | — |
| 038 | ONE STRATEGY that I Trade WEEKLY | [1](https://youtu.be/VAHP4jJN41g) | — |
| 039 | BPR with INDUCEMENT Strategy | [1](https://youtu.be/KmDbwnDpuGc) | — |
| 040 | ADVANCED TRADING STRATEGY | [1](https://youtu.be/BzmD9Y0NWBw) [2](https://www.metekaplanmasterclass.com) [3](https://youtu.be/v3JdCAOn0jo) [4](https://youtu.be/QH47sRzCu7U) [5](https://youtu.be/m7GwKRaPD28) [6](https://youtu.be/NZH8yEgsBY8) [7](https://youtu.be/36CO8Eo0WXw) [8](https://youtu.be/8h7xeH_BKGw) [9](https://youtu.be/Nk8rP5pDteY) [10](https://youtu.be/fLmpVhXZo9Q) [11](https://youtu.be/qHjsBLWPa1E) [12](https://youtu.be/MJHNKqLp4Gc) | — |

## 30 支策略影片

| # | 策略 | 影片 |
|---|---|---|
| 001 | HTF + LTF + SFP + LIQ + PREMIUM DISCOUNT + RANGE + TT3 + S/R FLIP | [1](https://youtu.be/Y03RUTAbyWM) |
| 002 | HTF + LTF + EQL HIGHS + MiB + IMBALANCE + FTR + SFP + RANGE + S/R FLIP | [1](https://youtu.be/MA-d9OXtIug) |
| 003 | ROTE + BPR + DBD + BB + MSB + SFP + RANGE + PREMIUM DISCOUNT | [1](https://youtu.be/0sd4ziRidgI) |
| 004 | TT3 + RANGE + MSB + IMB + BB + CSFP + TRAPS + FAKE ENTRIES | [1](https://youtu.be/6G63U5ZmvfA) |
| 005 | OTE + IMB + LIQ + RBR + S/R FLIP + SFP + RANGE + STOP HUNTS | [1](https://youtu.be/l_WncUv9r6M) |
| 006 | SNIPER + DOUBLE RANGE + MIB + BB + OTE + EQL HUNT + SFP | [1](https://youtu.be/B7UctN6VdDE) |
| 007 | HTF + LTF + BPR + MSB + PARTIAL TP + EQL HIGHS + IMB | [1](https://youtu.be/ur1HiEf1Eio) |
| 008 | RANGE + LIQ POOLS + MSB + LIQ SWEEP + OTE + PREMIUM/DISCOUNT + IMB | [1](https://youtu.be/vaiOYSBKzJ4) |
| 009 | EQL POOL + LIQ POOL + PPI + SFP + RANGE + HTF + LTF | [1](https://youtu.be/3t5UmXgnzSM) |
| 010 | LOW/HIGH PROBS + DBD + LIQ + SFP + IMB + EQL + GOOD/BAD SETUPS | [1](https://youtu.be/lfPtDU2pwFk) |
| 011 | FTR + SUPPLY + LIQ + EQL + HTF/LTF COMBO + PREMIUM/DISCOUNT + IMB + ROTE | [1](https://youtu.be/ed-bNndl328) |
| 012 | RANGE + LIQ POOLS + MSB + LIQ SWEEP + IMB + PROBABILITY MANAGEMENT | [1](https://youtu.be/22t3JzLrs6g) |
| 013 | ROTE + PO3 + RANGE + MSB + LIQ + IMB + RISK MANAGEMENT | [1](https://youtu.be/Iy4a8QgZmCA) |
| 014 | SFP + MSB + IMB + PREMIUM/DISCOUNT + BREAKEVEN MANAGEMENT | [1](https://youtu.be/3Twr_RH4pgs) |
| 015 | RANGE + ROTE + LIQUIDITY + SFP + BIAS + EQH/EQL | [1](https://youtu.be/VazJqpXYy1U) |
| 016 | ROTE MASTERY + RANGE + IMB + LIQ + SFP + BIAS + EQLs | [1](https://youtu.be/VSK5_a7rLew) |
| 017 | FVG + HTF/LTF COMBOS + PO3 + EQUAL HUNTS + MSB + IDM | [1](https://youtu.be/1BlBhKTLvnc) |
| 018 | HIGH PROFITABILITY MASTERY (Turning 2R Setups to 10R Setups) | [1](https://youtu.be/b2GJOFdjT5c) |
| 019 | Low Time Frame Sniper Scalping Mastery | [1](https://youtu.be/NIHWxhXs0Bg) |
| 020 | New York Opening 9:30 AM Scalping | [1](https://youtu.be/cNUPMveg9Ts) |
| 021 | IDM + LIQ + MSB + OTE + BREAKS + OB + SFP | [1](https://youtu.be/NjA3DcFnYGQ) |
| 022 | Micro Scalping vs High Time Frame Day Trading | [1](https://youtu.be/LrbT3JDdJfQ) |
| 023 | DAILY BIAS + HTF/LTF + OTE + OB + LIQ + IMB + PO3 | [1](https://youtu.be/Rssj_vtpzgY) |
| 024 | SUPER ORDER BLOCK + HTF/LTF + OTE + LIQ + PO3 + SFP | [1](https://youtu.be/SmLMAPKLhFU) |
| 025 | SUPER BREAKER BLOCK + HTF/LTF + IMB/FVG + LIQ + PREMIUM DISCOUNT + IDM | [1](https://youtu.be/dqbCMNh-I2s) |
| 026 | SUPER OB + Reverse Fractal Strategy + SFP + OTE + EQL LOWS/HIGHS | [1](https://youtu.be/tizc4RFpZjc) |
| 027 | Market Structure Identification + Super OB + When to LONG or SHORT | [1](https://youtu.be/AgnP0aHWLd0) |
| 028 | Micro Order Block Scalping | [1](https://youtu.be/6uWqWyNU8wY) |
| 029 | Fractal Markets + OB Mastery + Imb + Sfp + Liq + Range | [1](https://youtu.be/eyjzgqx4-1M) |
| 030 | Rubber Ball Mastery - Price between LIQ + IMB | [1](https://youtu.be/ZLszXXoopYk) |

---

## 其他資源 (來自 25.09 版清單)

| 來源 | 連結 |
|---|---|
| ICT 本人頻道 (源頭) | <https://www.youtube.com/@InnerCircleTrader> |
| 版主 HANXIN | <https://www.youtube.com/watch?v=KjR7SebdtbA> |
| PF 出金世界第一交易員 (2025) | <https://youtube.com/post/UgkxXurGgLXffw5CMyae0GL9Gxv5s4dTTIT5> |
| Candy — 亞洲區 APEX 出金最多 (2025) | <https://www.youtube.com/@candy.thetrader> |
| YT 銀彈交易 | <https://www.youtube.com/watch?v=DtED-N88JYM> |
| YT 交易生活 (只看 ICT 相關) | <https://youtu.be/TxjBpxPmWIA> |

## 清單裡的紀律條款

1. 先看土哥 40 堂看懂定義，看幾遍都行
2. YT 選想看的看，**看完後至少回測一套交易系統最近兩年** ← 本 repo 做的就是這件事
3. 維持每個交易日都有出金帳號能交易，幾個看個人財力
4. 禁止複製交易，能單月出金破萬美前都不複製交易
5. 有學習步驟的問題去版主 HANXIN 社群發問
