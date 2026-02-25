# 投委会式策略评审与筛选打分规则（v1）

目标：防过拟合 + 可复现 + 可规模化筛选；Robustness 权重最高。

## 硬门槛（Gating）
- 成本后：包含滑点 5 bps/side（单边）
- MaxDD（intrabar）≤ 10%
- Trades ≥ 200（可按策略类型调整）
- 参数盆地：最优点周围扰动仍不过度崩塌（v1 先实现 1D/2D 热力图切片）
- 时间窗口一致性：Walk-forward OOS 多窗口聚合，最差窗口不得灾难

## 数据切分
- Test：最近 12 个月（freeze 后才跑；不反复调参）
- Train+Val：更早历史；Train 选参，Val 排名
- Walk-forward 在 Train+Val 内滚动。

## 评分权重（总分 100）
- Robustness 55
- Risk/Tail 25
- Return Quality 15
- Implementability 5

> v1 先实现：MaxDD gate + 时间窗口一致性 summary + 参数盆地面积占比；后续再加 bootstrap / deflated sharpe。
