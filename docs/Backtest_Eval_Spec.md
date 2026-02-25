# Quant Harbor — Backtest / Evaluation / Freeze Spec (Implementation-Complete)

更新时间（PT）：2026-02-22

本文件是 Quant Harbor 项目的“可直接实现”的研究与评估规范：
- 如何拉取并固化数据快照（可复现）
- 如何回测策略+参数
- 如何做 WFA（walk-forward）评估
- 如何做硬门槛 Gate、参数盆地（Parameter Basin）
- 如何打分（Scorecard）
- 如何用 **FreezeA（方案A）** 选出最终可交易的参数并在 Test（最近12个月）盲测

目标：把本文件给任何一个 AI agent，它可以不问你任何问题，直接实现同样的流程与落盘产物。

---

## 0) 术语与总原则

### 0.1 研究对象（Production 主口径）
- **策略 S + 固定参数 θ（frozen）** 是最终要比较/落地交易的对象。

### 0.2 Diagnostics（可选）
- **策略 S + 调参规则 R（滚动选参）** 是研究诊断对象（判断策略是否依赖频繁调参）。
- Diagnostics 不进入 Production 主榜排名。

### 0.3 可复现性（必须）
- 回测必须 **永远读取本地数据快照**（snapshot），而不是直接打 API。
- 每个 run 必须落盘：
  - 使用的 snapshot_id / snapshot_dir
  - 使用的参数 θ
  - 使用的成本模型、时间段过滤规则

---

## 1) 数据层（Data → Snapshot）

### 1.1 数据源
- 目标：Alpaca Market Data。

数据完整性要求（必须记录在 snapshot meta 中）：
- Corporate actions 口径：raw vs adjusted（split/dividend/all）。本项目默认 `adjustment="all"`。
- 若扩展到个股：必须考虑 survivorship bias（退市/更名/并购导致的样本缺失）。

### 1.2 快照（snapshot）落盘
每次拉取数据必须落盘到：

```
~/Desktop/trader/quant_harbor/data/snapshots/<snapshot_id>/
  bars.parquet
  meta.json
```

#### bars.parquet 规范
- Parquet 文件
- index：UTC tz-aware 时间戳（若读出为 naive，必须转为 UTC tz-aware）
- columns：`open, high, low, close, volume`
- 数据应为 15m bars（本项目当前默认）

#### meta.json 规范（必须包含）
建议字段（必须至少覆盖下面这些）：
- snapshot_id
- symbol
- timeframe
- start_et / end_et
- created_utc
- source（例如 alpaca-py StockHistoricalDataClient）
- feed（iex/sip 等）
- adjustment（raw/split/dividend/all；本项目默认 all）
- rth_filter（例如 09:30-16:00 ET）
- bar_timestamp_semantics（说明 15m bar label 语义；需要明确 15:45–16:00 bar 的 close 是否包含收盘竞价）
- note（例如 “index is UTC; RTH filtered by clock time (MVP)”）

### 1.3 交易时段过滤
- 只保留 RTH：09:30–16:00 ET
- 保存为 UTC，展示层可转换 ET/PT

---

## 2) 回测层（Backtrader Runner）

### 2.1 基础设定（Quant Harbor 当前默认口径）
- Symbol：QQQ
- Timeframe：15m
- Session：RTH（09:30–16:00 ET）
- Overnight：允许持仓跨日（是否强制日内平仓由策略参数/配置决定，不作为全局硬要求）
- 初始资金：2000（可配置）
- 成本：滑点 5 bps/side（建议后续做敏感性：10bps/side、20bps/side）
- 手续费：先设 0（保留接口）

### 2.2 单次回测输入/输出接口（必须统一）

#### 输入
- df_utc：UTC tz-aware index 的 OHLCV DataFrame
- strat_params（策略参数 dict）
- cfg（包含 cash、slippage、commission 等）

#### 输出（落盘产物）
每次 run 输出目录：

```
results/<run_id>/
  summary.json
  (optional) trades.parquet
  (optional) equity.parquet
  (optional) snapshot_meta.json
```

- `summary.json`：必须包含至少以下字段：
  - symbol
  - strategy
  - strategy_params
  - start_value / end_value
  - net_pnl / net_return_pct
  - max_drawdown_close_pct / max_drawdown_intrabar_pct
  - total_trades / win_rate_pct
  - profit_factor / expectancy / sharpe
  - data_dt_min_utc / data_dt_max_utc
  - slippage_bps_side / commission_pct
  - slippage_sensitivity（可选但推荐；例如 bps=10/20 下净收益/回撤/Sharpe 的退化）
  - generated_utc

- trades/equity：
  - 评估环节（大规模遍历）可只写 summary（节省磁盘），通过 `persist_details=False`。
  - 最终 Test 验收（freeze 后一次性跑）建议写全 trades/equity（dashboard 复盘用）。

---

## 3) 时间切分（Train/Val/Test）

### 3.1 Test（盲测）
- 固定保留 **最近 12 个月**作为 Test。

### 3.2 Pre（Train+Val）
- 其余更早的历史为 Pre。

### 3.3 Train/Val
- 在 Pre 内按时间切分（默认 80/20）：
  - Train：前 80%
  - Val：后 20%

纪律：
- Test 不参与调参。
- 调参仅在 Pre（或 WFA OOS）进行。

---

## 4) WFA（Walk-forward）

本项目存在两种 WFA 模式：

### 4.1 WFA（fixed params across windows）— Production 使用
目的：对“策略 S + 固定参数 θ”做时间一致性验证。

#### 窗口生成
在 Pre（Train+Val）内，用 UTC 时间索引生成滚动窗口：
- train_months = 12
- oos_months = 3
- 每次向前滚动 oos_months

每个窗口包含：
- train_start/train_end
- oos_start/oos_end

#### 输出
对于某个固定参数 θ，在所有 OOS 窗口上回测，必须落盘：

- `wfa_windows.parquet`：每行一个窗口，字段至少：
  - window
  - oos_start_utc / oos_end_utc
  - oos_net_pnl / oos_net_return_pct
  - oos_maxdd_intrabar_pct
  - oos_trades

- `wfa_summary.json`：聚合统计：
  - windows
  - pos_window_rate（= mean(oos_net_pnl>0)）
  - oos_net_return_mean / median / worst
  - wfa_mode = "fixed_params"

### 4.2 WFA（re-tune per window）— Diagnostics 使用
目的：评估“策略 + 调参规则”是否稳定。

流程：每个窗口 train 上选参 → OOS 上验证。

输出（diagnostics）：
- `wfa_windows.parquet`：每行窗口 OOS 指标（参数可能不同）
- `wfa_summary.json`：聚合
- wfa_mode = "retune_per_window"

---

## 5) Gate（硬门槛）

Gate 是“先筛掉不合格候选”的硬规则。默认 gate（可配置）：
- MaxDD(intrabar) ≤ 10%
- trades（年化）≥ 200
- 成本后 net_pnl > 0（Production 阶段常用；系统搭建阶段可放宽）

### 5.1 trades 年化计算
为避免 3 个月 OOS 窗口天然 trades 不够：
- 使用 summary 的 `data_dt_min_utc/data_dt_max_utc` 估算覆盖天数 days
- annualized_trades = trades * 365.25 / days

Gate 输出应包含：
- gate_ok（bool）
- gate_reasons（list[str]）
- gate_cfg（echo config）

---

## 6) Parameter Basin（参数盆地）

目的：衡量参数的“邻域稳定性”，避免单点最优。

### 6.1 邻域扰动网格（默认）
围绕 base 参数 θ0 扰动：
- 连续参数（entry_rsi/stop_pct/take_pct）：±{5%,10%,20%}
- 离散参数（max_bars_hold）：±{1,2,4}
- 非线性短周期参数（如 rsi_period）：使用离散覆盖（例如 2,3,4,5），而不是按百分比扰动
- 包含 base 点

生成笛卡尔积网格，得到 grid_points。

### 6.2 合格判定（plan v1）
一个扰动点被认为“合格”，需要同时满足：
- Gate 通过（MaxDD、年化 trades、net>0 如果要求）
- 且 Profit Factor ≥ 1.0

### 6.3 Basin 指标
- passed_points：合格点数量
- basin_pass_rate = passed_points / grid_points

### 6.4 Basin 输出
- `basin_grid.parquet`：每个点的参数+指标+qual_ok
- `basin_report.json`：汇总（grid_points/passed_points/basin_pass_rate/base_params/gate_cfg/basin_cfg）

可选：对多个 WFA OOS 窗口重复 basin，输出：
- `basin_wfa_windows.parquet`
- `basin_wfa_report.json`（mean/median/worst）

---

## 7) Scorecard（打分，主榜口径）

### 7.1 输入来源（推荐）
- Robustness：来自 WFA fixed-params 的 `pos_window_rate` + basin 指标
- Risk/Return：来自 Test 12m（盲测）summary

### 7.2 权重
- Robustness 55
- Risk/Tail 25
- Return Quality 15
- Implementability 5

### 7.3 v1 计算（可复现、可扩展）
Scorecard 输出必须包含：
- total_score（0..100）
- subscores01（0..1）：robustness/risk/return_quality/implementability
- inputs：用于计算的原始指标
- missing：缺失字段列表

（v1 的具体映射函数可按工程实现；关键是：权重与输入字段结构固定，后续可升级映射细节。）

### 7.4 scorecard.json 规范
每个 Production run（例如 FreezeA）必须写：

- `results/<run_id>/scorecard.json`
  - meta：
    - symbol
    - strategy
    - run_kind（例如 freezeA）
    - wfa_mode（fixed_params）
    - chosen_params（最终参数 θ*）
    - snapshot_dir
  - scorecard：上述分数字段
  - sources：所有输入文件相对路径（用于 dashboard 追溯）

---

## 8) FreezeA（方案A：最终参数选择 + Test 验收）

### 8.1 输入
- Candidate grid Θ（参数空间），并记录 **n_trials = |Θ|**（用于多重比较惩罚/审计）
- WFA windows（Pre 内滚动）
- Selection rule（默认）：
  1) 过滤 pos_window_rate ≥ 0.7
  2) 排序：median(OOS net_return_pct) desc
  3) tie-break：worst desc，再 mean desc
  4) 若无候选满足阈值：fallback（必须在报告中标注）

统计严谨性（MCP / multiple comparisons）：
- 当 Θ 很大时，OOS 上总会“撞大运”选到表现很好的参数。
- 因此必须把 n_trials 写入产物，并在 scorecard 中暴露 Deflated Sharpe Ratio（DSR，近似实现）作为参考。

### 8.2 FreezeA 必需产物
输出目录 `results/rsi2_freezeA_<SYMBOL>_<run_id>/` 应包含：
- `candidates_oos_agg.parquet`：每个候选 θ 的 OOS 聚合统计
- `wfa_windows.parquet`：最终选中 θ* 的每窗口 OOS 指标（供 dashboard 画图）
- `wfa_summary.json`：最终选中 θ* 的 OOS 聚合（pos_window_rate/median/worst/mean，wfa_mode=fixed_params）
- `test/`：最后12个月 Test 的完整回测（建议包含 equity/trades）
- `final_report.json`：freeze 选择过程 + θ* + Test summary（必须包含 fallback 标记）
- `scorecard.json`：Production 主榜用

---

## 9) Dashboard v2（展示规范）

Dashboard v2 必须：
- 主榜（Leaderboard v2）按 `scorecard.json` 发现并排序
- 可按 run_kind 过滤（默认只看 freezeA）
- Details v2 从 scorecard.sources 加载：
  - Segments：val_summary/test_summary
  - WFA：wfa_windows.parquet
  - Basin：basin_report.json（+可选 basin_wfa_report）

---

## 10) 参考实现映射（Quant Harbor 当前代码组织）

核心原则：评估系统（WFA/Gate/Basin/Scorecard/FreezeA/Dashboard）应当**策略无关**。新增策略只需：
- 实现策略本身（Backtrader Strategy 类）
- 在策略 registry 中注册（策略 id + 类 + 默认参数空间）

参考实现：
- 数据快照：`quant_harbor/alpaca_data.py::make_snapshot_multi`（支持多标的；单标的仍兼容 `bars.parquet`）
- 回测 runner：`quant_harbor/backtest_runner.py::run_backtest_df`（通用；支持多 data feeds）
- 策略 registry：`quant_harbor/strategies/registry.py`
- 切分：`quant_harbor/split.py::split_train_val_test_last12m`
- WFA 窗口：`quant_harbor/walk_forward.py::make_quarterly_wfa_windows`
- Gate：`quant_harbor/gates.py`
- Basin：`quant_harbor/basin.py` + `cli_rsi2_basin*.py`
- Scorecard：`quant_harbor/scorecard.py` + `cli_scorecard.py`
- FreezeA：`quant_harbor/cli_rsi2_freeze_wfa.py`
- Dashboard：`quant_harbor/dashboard/app.py`
