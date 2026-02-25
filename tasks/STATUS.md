# 当前状态

更新时间（PT）：2026-02-22

## 已完成
- Walk-forward（Train+Val 内滚动，训练窗选参→OOS评估→聚合）
- Gate v1（MaxDD intrabar / trades / net_pnl）在 Val/OOS 与 Test 上评估并落盘报告
- Parameter basin（参数盆地）扰动网格评估并落盘
- 工程目录：`~/Desktop/trader/quant_harbor/`
- venv + 依赖已安装（backtrader/pandas/streamlit/plotly/alpaca-py/pyarrow）
- Alpaca 数据拉取（IEX feed）→ RTH 过滤 → parquet 快照
- RSI2 Daytrade 策略（含 safediv 防止 RSI 除零）
- Backtrader runner + 结果落盘（summary.json + snapshot_meta.json）

## 已跑通的回测（PoC）
- 标的：QQQ
- 周期：15m（RTH）
- 成本：5 bps/side（通过 backtrader `set_slippage_perc`）
- 最新结果目录：`~/Desktop/trader/quant_harbor/results/rsi2_QQQ_20260221_225937/`

关键指标（最近 1 年样本，含成本）：
- Net PnL: -89.13
- Net Return: -4.46%
- MaxDD close: 5.21%
- MaxDD intrabar(min): 5.23%
- Trades: 420
- Win rate: 46.43%
- Profit Factor: 0.85
- Expectancy（avg pnl/trade）: -0.215
- Sharpe: -1.47

## 下一步（按 TASKS.md）
- ✅ T4.1 已完成：最近12个月 Test + Train/Val 分段回测落盘
- ✅ T4.2 已完成：Walk-forward（Train+Val 内滚动）：训练窗选参 → OOS 窗口评估 → 聚合一致性指标
- 产出：`results/rsi2_wfa_QQQ_20260222_135917/`（wfa_windows.parquet + wfa_summary.json；每个 window 落盘 train 候选 + oos 结果）
  - WFA 聚合（OOS）：windows=11；pos_window_rate≈9.1%；OOS net return mean≈-1.43%；median≈-1.24%；worst≈-4.36%
  - 入口：`python -m quant_harbor.cli_rsi2_walk_forward ...`
- ✅ T4.3 已实现：Gate（Val/OOS + Test）
  - WFA OOS gate report：`results/rsi2_wfa_QQQ_20260222_135917/wfa_gate_report.json`（gate_version=t4.3_v2；wfa_pass_rate≈9.1%）
  - VAL+TEST gate report：`results/rsi2_gate_QQQ_20260222_141542/gate_report.json`（VAL gate_ok=False；TEST gate_ok=False）
- ✅ T4.4 已实现：参数盆地（Parameter basin）
  - 输出：`results/rsi2_basin_QQQ_20260222_145046/`（basin_grid.parquet + basin_report.json；segment=val）
  - 本次（val）盆地通过率 basin_pass_rate=0.2496（156/625）
- ✅ T4.5 已实现：Scorecard v1（Robustness 55 / Risk 25 / Return 15 / Implementability 5）
  - 输出示例：`results/rsi2_gate_QQQ_20260222_143224/scorecard.json`（total_score=30.02；用于验证系统闭环，不代表策略可用）
- ✅ T5.3 已实现：Dashboard v2（Leaderboard/Details v2：展示分段指标、WFA、参数盆地与 scorecard 排名；legacy 页面保留）
