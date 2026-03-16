# W&B 指标与面板最终方案

本文给出本仓库回测结果同步到 Weights & Biases (`wandb`) 的最终方案。目标不是“把所有东西都搬上去”，而是做一个足够解释交易表现、同时结构清晰的 dashboard。

结论先写在前面：

- `core-curves/*`：放最重要的全局曲线，优先看整体表现。
- `per-asset-curves/*`：放 20 只股票各自的策略 vs baseline 对比图。
- `summary`：放最终核心标量指标，用于 run 间比较。

## 1. 设计目标

这个面板需要回答 5 个核心问题：

1. 策略整体赚没赚钱？
2. 相对市场基准（SPY）表现如何？
3. 过程中风险和回撤大不大？
4. alpha 是来自少数股票，还是普遍存在？
5. 主动交易是否优于单股 buy-and-hold baseline？

## 2. W&B 面板结构

最终建议固定为三层：

### 2.1 `core-curves/*`

放全局最重要的 5 条曲线。

建议命名：

- `core-curves/strategy_nav`
- `core-curves/strategy_vs_spy`
- `core-curves/excess_return_cum`
- `core-curves/drawdown`
- `core-curves/cash_ratio`

### 2.2 `per-asset-curves/*`

每只股票一个 plot，共 20 个。

建议命名：

- `per-asset-curves/GS`
- `per-asset-curves/MSFT`
- `per-asset-curves/HD`
- ...
- `per-asset-curves/JNJ`

每个 plot 中建议至少包含两条线：

- `strategy`
- `buy_and_hold_baseline`

可选再加第 3 条：

- `excess_return`

### 2.3 `summary`

放所有最终关键标量指标，用于：

- run 间比较
- 按模型比较
- 按时间段比较

## 3. Core Curves 最终定义

### 3.1 `core-curves/strategy_nav`

含义：

- 策略总净值曲线

来源：

- `storage/reports/backtest/{run_id}/daily_nav.parquet`

Y 轴：

- `nav`

作用：

- 第一眼看策略最终赚没赚钱，以及收益路径是否平滑。

### 3.2 `core-curves/strategy_vs_spy`

含义：

- 策略净值 vs SPY 基准净值

来源：

- 策略：`daily_nav.parquet`
- 基准：`benchmark_nav.parquet`

建议同一张图中两条线：

- `strategy_nav`
- `spy_nav`

作用：

- 直观看策略有没有跑赢大盘。

注意：

- 仓库里 `benchmark_nav.parquet` 的文件名和画图名里写的是 SPY，但严格来说它代表“当前 benchmark”，只是默认配置下通常是 SPY。

### 3.3 `core-curves/excess_return_cum`

含义：

- 累计超额收益曲线

建议计算：

- 先对齐策略与基准日期
- 再计算日收益差 `r_strategy - r_benchmark`
- 再做累计和或累计复合

建议优先使用：

- 与仓库现有图一致的“累计日超额收益”口径

作用：

- 比双 NAV 图更直接地展示“领先/落后了多少”。

### 3.4 `core-curves/drawdown`

含义：

- 策略回撤曲线

建议计算：

- `drawdown = nav / nav.cummax() - 1`

作用：

- 这是最值得补上的风险曲线之一。
- 可以直观看出“最痛的时候有多痛、痛了多久”。

### 3.5 `core-curves/cash_ratio`

含义：

- 现金占总资产比例随时间变化

建议计算：

- 从 `detailed_portfolio_snapshots.jsonl` 中取：
  - `cash`
  - `total_equity`
- 计算 `cash / total_equity`

作用：

- 用来解释策略保守还是激进。
- 防止误判：有时收益低不是策略差，而是长期持有大量现金。

如果实现成本太高，这条可以退而求其次换成：

- `core-curves/turnover`

但优先级上我更推荐 `cash_ratio`。

## 4. Per-Asset Curves 最终定义

每只股票一个 plot，共 20 个。

### 4.1 每个 plot 的最小配置

建议每个 `per-asset-curves/{SYMBOL}` 至少包含：

- `strategy`
- `buy_and_hold_baseline`

其中：

- `strategy`：策略在该股票上的收益路径
- `buy_and_hold_baseline`：该股票的单股 buy-and-hold 基线

### 4.2 如果能多加一条线

推荐加：

- `excess_return`

即：

- `strategy - buy_and_hold_baseline`

这样单图解释力更强，能直接看出该股票上主动交易到底创造了多少额外价值。

### 4.3 为什么要保留 20 张

因为这 20 张图回答的是最关键的解释性问题：

- 哪些股票上策略明显优于 baseline
- 哪些股票上主动交易其实不如拿着不动
- alpha 是不是集中在少数资产上
- 策略是否对某类股票特别擅长或特别差

所以这 20 张不是“噪音”，而是解释策略来源的核心面板。

## 5. Summary 最终指标

## 5.1 最重要的 5 个指标

这 5 个是最适合被强调的主指标：

- `cum_return`
- `max_drawdown`
- `sortino`
- `sharpe`
- `information_ratio`

原因：

- `cum_return`：看最终收益
- `max_drawdown`：看最坏损失
- `sortino`：看下行风险调整后的收益质量
- `sharpe`：看总波动口径下的收益质量
- `information_ratio`：看相对基准的主动管理质量

## 5.2 建议一并写入 summary 的完整核心指标

建议最终写入 `wandb.summary` 的标量如下：

- `cum_return`
- `max_drawdown`
- `sortino`
- `sharpe`
- `volatility`
- `volatility_daily`
- `trades_count`
- `trades_notional`
- `excess_return_total`
- `information_ratio`
- `information_ratio_daily`
- `tracking_error`
- `tracking_error_daily`
- `hit_ratio_active`
- `beta`
- `corr`
- `up_capture`
- `down_capture`
- `sortino_excess`
- `cagr`

其中：

- 前 5 个是主指标
- 其余的是辅助解释指标

## 5.3 不建议放进顶层 summary 的东西

不建议把 20 只股票的所有单股指标都塞到顶层 summary。

原因：

- 会让 summary 过于拥挤
- run 间比较时可读性变差

更推荐：

- 单股曲线放 `per-asset-curves/*`
- 单股标量若需要，放 `wandb.Table`

## 6. 数据来源映射

### 6.1 全局曲线

| W&B 名称 | 数据来源 |
| --- | --- |
| `core-curves/strategy_nav` | `daily_nav.parquet` |
| `core-curves/strategy_vs_spy` | `daily_nav.parquet` + `benchmark_nav.parquet` |
| `core-curves/excess_return_cum` | `daily_nav.parquet` + `benchmark_nav.parquet` |
| `core-curves/drawdown` | 由 `daily_nav.parquet` 派生 |
| `core-curves/cash_ratio` | `detailed_portfolio_snapshots.jsonl` |

### 6.2 单股曲线

| W&B 名称 | 数据来源 |
| --- | --- |
| `per-asset-curves/{SYMBOL}` 中的 baseline | `per_symbol_benchmark/per_symbol_benchmark_nav.parquet` |
| `per-asset-curves/{SYMBOL}` 中的 baseline 指标 | `per_symbol_benchmark/per_symbol_benchmark_metrics.parquet` 或 `.jsonl` |

注意：

- 仓库现有产物里，单股 baseline 已经有现成数据。
- 但“策略在单只股票上的收益路径”未必有现成独立 parquet；如果没有，需要在记录组合明细时额外构造，或者先只上单股 baseline 曲线。

如果后续实现“单股策略路径”，最推荐的数据源是：

- 从每日组合快照中按 symbol 取 `position_value`
- 结合价格变化构造该 symbol 的策略持仓价值时间序列

## 7. 推荐的 W&B 命名规范

建议统一用斜杠分层，不要混用空格和驼峰。

### 7.1 曲线命名

- `core-curves/strategy_nav`
- `core-curves/strategy_vs_spy`
- `core-curves/excess_return_cum`
- `core-curves/drawdown`
- `core-curves/cash_ratio`

- `per-asset-curves/AAPL`
- `per-asset-curves/MSFT`
- ...

### 7.2 summary 命名

直接使用指标原名即可：

- `cum_return`
- `max_drawdown`
- `sortino`
- `sharpe`
- `information_ratio`
- `trades_count`
- `trades_notional`

这样最利于和仓库现有 `metrics.json` 对齐。

## 8. 最终推荐面板

如果只看最终方案，建议固定为：

### 8.1 Dashboard 顶部

放 5 张 core 曲线：

1. `core-curves/strategy_nav`
2. `core-curves/strategy_vs_spy`
3. `core-curves/excess_return_cum`
4. `core-curves/drawdown`
5. `core-curves/cash_ratio`

### 8.2 Dashboard 中部

放 20 张单股图：

- `per-asset-curves/{SYMBOL}`，共 20 个

每张至少两条线：

- `strategy`
- `buy_and_hold_baseline`

### 8.3 Dashboard 右侧或底部

放 summary 标量：

- 收益：`cum_return`, `cagr`
- 风险：`max_drawdown`, `volatility`, `volatility_daily`
- 风险收益比：`sortino`, `sharpe`
- 相对基准：`excess_return_total`, `information_ratio`, `tracking_error`, `beta`, `corr`, `hit_ratio_active`
- 交易行为：`trades_count`, `trades_notional`

## 9. 最终判断

这套 W&B 面板对于“分析交易情况”是够用的，而且是清晰的。

它的优点是：

- 先看全局，再看单股解释
- 核心曲线数量克制，不会一上来信息过载
- 单股 20 图保留了解释力
- summary 适合 run 间比较

这不是“所有可能指标都上”的方案，但它是一个非常实用、足够稳定、适合团队长期使用的方案。

## 10. 一句话版本

最终 W&B 方案就是：

- `core-curves/*` 放 5 条全局关键曲线
- `per-asset-curves/*` 放 20 只股票各自的策略 vs baseline 图
- `summary` 放最终核心标量指标

这是当前最推荐的最终版。

