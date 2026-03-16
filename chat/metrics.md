# StockBench 指标与产物梳理

本文以代码实现为准，不以 `README.md` / `storage/README.md` 为准。主要依据：

- `stockbench/stockbench/backtest/metrics.py`
- `stockbench/stockbench/backtest/engine.py`
- `stockbench/stockbench/backtest/reports.py`
- `stockbench/stockbench/backtest/pipeline.py`
- `stockbench/stockbench/llm/llm_client.py`
- `stockbench/stockbench/agents/dual_agent_llm.py`
- `stockbench/config.yaml`

## 0. 口径说明

- 下文默认你是按官方 README 的方式先 `cd stockbench` 再运行，所以相对路径默认相对 `stockbench/` 目录。
- 这套代码大量使用 `os.getcwd()`；如果你从别的工作目录启动，`storage/...` 和 `logs/...` 的根路径会跟着当前工作目录变化。
- 当前仓库没有 `wandb` / Weights & Biases 集成，也没有真正落盘的统一 telemetry sink；很多“过程指标”是分散保存在详细交易文件、LLM cache、普通日志里的。
- 策略 NAV 是按“当日开盘后、成交完成后的组合净值”计算的，不是收盘净值。

## 1. 这套系统到底会产出什么

### 1.1 主报告目录

主报告目录是：

`storage/reports/backtest/{run_id}/`

其中 `{run_id}` 来自 CLI 参数 `--run-id`；如果目录已存在，`write_outputs()` 会给最终目录追加时间后缀避免覆盖。

### 1.2 一定会写出的主结果文件

| 文件 | 默认是否写出 | 内容 |
| --- | --- | --- |
| `storage/reports/backtest/{run_id}/trades.parquet` | 是 | 轻量级逐笔成交表 |
| `storage/reports/backtest/{run_id}/daily_nav.parquet` | 是 | 每日 NAV 序列 |
| `storage/reports/backtest/{run_id}/metrics.json` | 是 | 主绩效指标字典 |
| `storage/reports/backtest/{run_id}/metrics_summary.csv` | 是 | 便于表格分析的一行摘要 |
| `storage/reports/backtest/{run_id}/summary.txt` | 是 | 人类可读摘要 |
| `storage/reports/backtest/{run_id}/conclusion.md` | 是 | 列出全部 metrics 的 Markdown 报告 |
| `storage/reports/backtest/{run_id}/meta.json` | 是 | Python / pandas / pyarrow / httpx 版本信息 |

### 1.3 有条件写出的结果文件

| 文件 | 条件 | 内容 |
| --- | --- | --- |
| `storage/reports/backtest/{run_id}/benchmark_nav.parquet` | 成功构造基准序列时 | 策略对照基准的 NAV |
| `storage/reports/backtest/{run_id}/equity_vs_spy.png` | 有 benchmark 且 matplotlib 可用 | 策略 vs 基准 NAV + 回撤图 |
| `storage/reports/backtest/{run_id}/excess_return_vs_spy.png` | 有 benchmark 且 matplotlib 可用 | 超额收益累积图 |
| `storage/reports/backtest/{run_id}/nl_summary.txt` | `backtest.summary_llm=true` 时 | LLM 生成的自然语言总结 |
| `storage/reports/backtest/baselines/{baseline_name}.json` | 配置了 `backtest.baseline_name` 时 | 基线 run 的指标快照 |

注意：

- 虽然文件名叫 `equity_vs_spy.png` / `excess_return_vs_spy.png`，但代码里实际拿的是“当前 benchmark_nav”，不一定真的是 SPY。
- `meta.json` 是环境信息，不是金融指标。

## 2. 主结果指标：`metrics.json`

`metrics.json` 来自 `stockbench.backtest.metrics.evaluate()`。

### 2.1 绝对绩效指标

这些字段在有 NAV 时都会出现：

| 字段 | 计算口径 | 金融含义 | 存储位置 |
| --- | --- | --- | --- |
| `cum_return` | `nav[-1] - 1.0` | 回测期总收益率，相对初始资金的最终涨跌幅 | `metrics.json` / `metrics_summary.csv` / `summary.txt` / `conclusion.md` |
| `max_drawdown` | `min(nav / nav.cummax() - 1)` | 最大回撤，衡量最深亏损坑 | 同上 |
| `volatility_daily` | 日收益率标准差 | 日频波动率，衡量日常净值波动 | `metrics.json` / `metrics_summary.csv` / `conclusion.md` |
| `sortino` | `mean(ret) / std(ret[ret<0])` | 仅惩罚下行波动的风险调整收益 | `metrics.json` / `metrics_summary.csv` / `summary.txt` / `conclusion.md` |
| `trades_count` | `len(trades)` | 执行成交次数，近似反映换手频度 | `metrics.json` / `metrics_summary.csv` / `summary.txt` / `conclusion.md` |
| `trades_notional` | `sum(exec_price * qty)` | 成交名义金额总和，近似反映总换手规模 | `metrics.json` / `metrics_summary.csv` / `summary.txt` / `conclusion.md` |
| `volatility` | `volatility_daily * sqrt(252)` | 年化波动率 | `metrics.json` / `metrics_summary.csv` / `summary.txt` / `conclusion.md` |
| `sharpe` | `(mean(ret) * 252) / volatility` | 年化 Sharpe，比的是单位总波动下的收益 | `metrics.json` / `metrics_summary.csv` / `summary.txt` / `conclusion.md` |
| `sortino_annual` | 年化平均收益 / 年化下行波动 | 年化 Sortino，比的是单位下行风险下的收益 | `metrics.json` / `conclusion.md` |

补充说明：

- 这里默认无风险利率为 0。
- `cum_return` 用的是“最终 NAV 相对 1.0”，而不是“最终 NAV 相对首日 NAV”。对策略主回测来说，这表示相对初始本金的收益。
- 日收益率来自 `daily_nav.parquet` 的 `nav.pct_change()`。

### 2.2 相对基准指标

只有成功构造 `benchmark_nav` 时才会出现：

| 字段 | 计算口径 | 金融含义 | 存储位置 |
| --- | --- | --- | --- |
| `excess_return_total` | `sum(r_strategy - r_bench)` | 总超额收益，反映相对基准的日收益差累计 | `metrics.json` / `metrics_summary.csv` / `conclusion.md` |
| `tracking_error_daily` | `std(r_strategy - r_bench)` | 日频跟踪误差，衡量相对基准的偏离波动 | `metrics.json` / `metrics_summary.csv` / `conclusion.md` |
| `information_ratio_daily` | `mean(excess_ret) / tracking_error_daily` | 日频信息比率 | `metrics.json` / `metrics_summary.csv` / `conclusion.md` |
| `beta` | `cov(r_strategy, r_bench) / var(r_bench)` | 对基准系统性风险暴露 | `metrics.json` / `conclusion.md` |
| `corr` | 策略与基准日收益相关系数 | 与基准同步程度 | `metrics.json` / `conclusion.md` |
| `up_capture` | 基准上涨日的平均策略收益 / 平均基准收益 | 牛市跟涨能力 | `metrics.json` / `conclusion.md` |
| `down_capture` | 基准下跌日的平均策略收益 / 平均基准收益 | 熊市跟跌或抗跌能力 | `metrics.json` / `conclusion.md` |
| `hit_ratio_active` | `(excess_ret > 0).mean()` | 跑赢基准的交易日占比 | `metrics.json` / `conclusion.md` |
| `sortino_excess` | 年化超额收益 / 超额收益下行波动 | 超额收益口径下的 Sortino | `metrics.json` / `conclusion.md` |
| `rolling_ir_63` | 63 日滚动 `mean(excess)/std(excess)` 的最后一个值 | 短窗主动管理效率 | `metrics.json` / `conclusion.md` |
| `rolling_te_63` | 63 日滚动超额收益标准差再年化 | 短窗跟踪误差 | `metrics.json` / `conclusion.md` |
| `rolling_ir_126` | 同上，126 日 | 中窗主动管理效率 | `metrics.json` / `conclusion.md` |
| `rolling_te_126` | 同上，126 日 | 中窗跟踪误差 | `metrics.json` / `conclusion.md` |
| `excess_return_annual` | `mean(excess_ret) * 252` | 年化超额收益 | `metrics.json` / `conclusion.md` |
| `tracking_error` | `std(excess_ret) * sqrt(252)` | 年化跟踪误差 | `metrics.json` / `metrics_summary.csv` / `summary.txt` / `conclusion.md` |
| `information_ratio` | `excess_return_annual / tracking_error` | 年化信息比率 | `metrics.json` / `metrics_summary.csv` / `summary.txt` / `conclusion.md` |
| `alpha_simple` | 直接等于 `excess_return_annual` | 这里不是 CAPM alpha，只是“简单年化超额收益” | `metrics.json` / `summary.txt` / `conclusion.md` |
| `rolling_ir_252` | 同上，252 日 | 长窗主动管理效率 | `metrics.json` / `conclusion.md` |
| `rolling_te_252` | 同上，252 日 | 长窗跟踪误差 | `metrics.json` / `conclusion.md` |
| `n` | 对齐后样本点个数 | 相对指标有效样本数 | `metrics.json` / `conclusion.md` |
| `freq` | 固定写 `"day"` | 数据频率标签 | `metrics.json` / `conclusion.md` |

补充说明：

- `excess_return_total` 是把日超额收益直接相加，不是复利意义上的“主动净值差”。
- `rolling_ir_*` 代码里没有再做年化；它就是滚动窗口内的日超额收益均值除以标准差。
- 若样本不足 63/126/252 天，对应 rolling 指标会先得到 `NaN`，写入 `metrics.json` 时会被清洗成 `null`。

## 3. 表格摘要：`metrics_summary.csv`

`metrics_summary.csv` 只有一行，方便后续汇总到 DataFrame / Excel。字段如下：

| 字段 | 含义 |
| --- | --- |
| `run_id` | 本次回测 ID |
| `cagr` | 几何年化收益率，按 `nav_start -> nav_end` 和样本天数估算 |
| `cum_return` | 同 `metrics.json` |
| `max_drawdown` | 同 `metrics.json` |
| `volatility_daily` | 同 `metrics.json` |
| `sortino` | 同 `metrics.json` |
| `trades_count` | 同 `metrics.json` |
| `trades_notional` | 同 `metrics.json` |
| `information_ratio_daily` | 同 `metrics.json`，若无 benchmark 则写 0 |
| `excess_return_total` | 同 `metrics.json`，若无 benchmark 则写 0 |
| `volatility` | 同 `metrics.json` |
| `sharpe` | 同 `metrics.json` |
| `information_ratio` | 同 `metrics.json`，若无 benchmark 则写 0 |

注意：

- `cagr` 只在 `metrics_summary.csv` 中出现，不在 `metrics.json` 中。
- 没有 benchmark 时，`metrics_summary.csv` 会把相对指标列写成 0，而不是留空。

## 4. 过程与执行指标

### 4.1 `trades.parquet`

这是轻量级成交台账，字段来自 `trade_rows`：

| 字段 | 含义 | 金融意义 |
| --- | --- | --- |
| `ts` | 交易日 | 成交时间点 |
| `symbol` | 标的代码 | 哪只股票被交易 |
| `side` | `buy` / `sell` | 买卖方向 |
| `qty` | 成交股数 | 成交数量 |
| `exec_price` | 含滑点的执行价 | 用于成本价与收益计算 |
| `open_price` | 当日开盘价 | 当日实际参考价格 |
| `mark_price` | 当前估值价 | 本实现里基本等于开盘价 |
| `exec_ref_price` | 成交现金流参考价 | 现金更新时使用的价格，当前实现等于开盘价 |
| `commission_bps` | 佣金费率 | 交易成本参数 |
| `fill_ratio` | 成交比例 | 模拟流动性约束 |
| `trade_value` | `abs(qty) * exec_ref_price` | 名义成交额 |

重要实现细节：

- 现金流更新用的是 `exec_ref_price`，不是 `exec_price`。
- `exec_price` 体现滑点，用于持仓均价；`trade_value` 则按参考开盘价算。

### 4.2 `detailed_trades.jsonl`

默认配置 `backtest.enable_detailed_logging: true`，所以还会额外写：

`storage/reports/backtest/{run_id}/detailed_trades.jsonl`

它比 `trades.parquet` 更像“审计日志”，除上面字段外，还包含：

| 字段 | 含义 | 金融意义 |
| --- | --- | --- |
| `commission` | 本笔手续费 | 直接交易成本 |
| `net_cost` | 买入净流出 / 卖出净流入 | 真实现金影响 |
| `cash_before` / `cash_after` | 交易前后现金 | 流动性变化 |
| `position_before` / `position_after` | 交易前后持仓股数 | 仓位变化 |
| `avg_price_before` / `avg_price_after` | 交易前后持仓均价 | 成本变化 |
| `total_equity_before` / `total_equity_after` | 交易前后总权益 | 组合规模变化 |
| `total_position_value_before` / `total_position_value_after` | 交易前后持仓市值 | 风险敞口变化 |
| `unrealized_pnl_before` / `unrealized_pnl_after` | 交易前后浮盈亏 | 未实现盈亏变化 |
| `realized_pnl` | 卖出时已实现盈亏 | 真正锁定的 PnL |

### 4.3 `detailed_portfolio_snapshots.jsonl`

路径：

`storage/reports/backtest/{run_id}/detailed_portfolio_snapshots.jsonl`

这是“每日组合快照”，字段来自 `PortfolioSnapshot`：

| 字段 | 含义 | 金融意义 |
| --- | --- | --- |
| `timestamp` | 记录时间 | 日志时间戳 |
| `date` | 回测日期 | 快照对应交易日 |
| `cash` | 现金 | 流动性 |
| `total_equity` | 总权益 | 组合总资产 |
| `total_position_value` | 持仓市值 | 风险资产暴露 |
| `unrealized_pnl` | 浮盈亏 | 未兑现收益 |
| `nav` | `total_equity / initial_cash` | 组合净值 |
| `positions` | 每只股票的详细持仓字典 | 微观持仓明细 |
| `benchmark_nav` | 基准净值 | 代码字段存在，但当前实现里几乎恒为 `0.0` |

`positions` 内每只股票还带：

- `shares`
- `avg_price`
- `mark_price`
- `position_value`
- `position_pct`
- `unrealized_pnl`
- `holding_days`
- `total_cost`

这些字段的经济含义分别对应持仓数量、成本、估值、仓位占比、浮盈亏、持有天数和累计成本。

### 4.4 `detailed_trading_summary.json`

路径：

`storage/reports/backtest/{run_id}/detailed_trading_summary.json`

默认会写，聚合字段包括：

| 字段 | 含义 |
| --- | --- |
| `total_trades` | 全部成交笔数 |
| `total_snapshots` | 组合快照数 |
| `initial_cash` | 初始资金 |
| `final_cash` | 期末现金 |
| `final_equity` | 期末总权益 |
| `final_nav` | 期末 NAV |
| `trading_summary.buy_trades` | 买入笔数 |
| `trading_summary.sell_trades` | 卖出笔数 |
| `trading_summary.total_commission` | 总手续费 |
| `trading_summary.total_realized_pnl` | 总已实现盈亏 |

这个文件最接近“过程级执行汇总”。

## 5. LLM 过程审计

### 5.1 LLM cache 会写到哪里

LLM cache 根目录是：

`storage/cache/llm/by_run/{run_id}/`

其中：

- `_index.jsonl`：每次缓存写入一行索引。
- 如果 cache key 带日期前缀，还会进入日期子目录，例如 `storage/cache/llm/by_run/{run_id}/2025-03-17/...`。
- 文件命名：
  - `analysis_{cache_key}.json`：fundamental filter agent
  - `decision_{cache_key}.json`：decision agent
  - `{cache_key}.json`：其他角色，例如 backtest report

### 5.2 单次 LLM 调用实际保存了什么

单个 cache 文件的结构是：

- `metadata`
  - `ts_utc`
  - `role`
  - `model`
  - `provider`
  - `base_url`
  - `temperature`
  - `max_tokens`
  - `seed`
  - `cache_key`
  - `run_id`
  - `retry_attempt`
  - `is_retry`
- `input`
  - `system_prompt`
  - `user_prompt`
  - `system_prompt_length`
  - `user_prompt_length`
- `output`
  - `parsed_response`
  - `raw_response`
  - `parsed_response_length`
  - `raw_response_length`

其中的金融/工程意义：

- `raw_response.usage` 里通常能看到 `prompt_tokens` / `completion_tokens` / `total_tokens`。
- `retry_attempt` / `is_retry` 用来区分是不是重试得到的响应。
- `system_prompt_length` / `user_prompt_length` 反映 prompt 规模，间接对应成本和延迟。

### 5.3 双 agent 聚合出来但没有正式落盘的过程指标

dual-agent 决策过程中会聚合一个 `__meta__`，字段包括：

- `calls`
- `cache_hits`
- `parse_errors`
- `latency_ms_sum`
- `tokens_prompt`
- `tokens_completion`
- `prompt_version`

这些字段的含义分别是：

- `calls`：LLM 调用次数
- `cache_hits`：命中 cache 的次数
- `parse_errors`：JSON 解析失败次数
- `latency_ms_sum`：调用总延迟
- `tokens_prompt`：累计 prompt token
- `tokens_completion`：累计 completion token
- `prompt_version`：使用的 prompt 文件版本

但要特别注意：

- 这组聚合指标没有被写入 `metrics.json`。
- 也没有被写到 `storage/reports/backtest/{run_id}/`。
- 它主要存在于运行时内存和普通日志中；真正持久化到磁盘的是“每次调用的 cache 文件”和日志。

### 5.4 普通日志在哪里

这套系统实际有两类日志：

| 路径 | 来源 | 内容 |
| --- | --- | --- |
| `storage/logs/{LLM_PROFILE}_{START_DATE}_{END_DATE}.log` | `scripts/run_benchmark.sh` 的 stdout/stderr 重定向 | 整次脚本运行日志 |
| `logs/stockbench/{YYYY-MM-DD}.log` | `setup_json_logging()` | 结构化 JSON 日志，包含 LLM 成功/失败、延迟、token usage、交易流水等 |

补充说明：

- `run_backtest.py` 里虽然实例化了 `Metrics()` 并调用了 `m.incr("run_backtest.start", 1)`，但仓库内没有任何地方调用 `m.flush()`。
- 所以当前实现并没有一个真正独立、稳定落盘的“统一 runtime metrics 文件”。

## 6. Benchmark 与默认启用的额外分析

当前 `config.yaml` 默认同时开了两套基准相关输出：

- 旧式基准：`backtest.benchmark.symbol: SPY`
- 新式逐股票买入持有基准：`backtest.benchmark.type: per_symbol_buy_and_hold`

### 6.1 单一 / 组合 benchmark 相关

成功构造 benchmark 时会得到：

| 文件 | 含义 |
| --- | --- |
| `storage/reports/backtest/{run_id}/benchmark_nav.parquet` | 基准 NAV 序列 |
| `metrics.json` 中所有相对指标 | 策略相对基准的超额收益、TE、IR、Beta、Capture 等 |
| `equity_vs_spy.png` | 策略 vs 基准 NAV 与回撤 |
| `excess_return_vs_spy.png` | 累计超额收益曲线 |

金融含义：

- 这组产物回答的是“策略是否跑赢基准、偏离基准多少、风险暴露像不像基准”。

### 6.2 逐股票 buy-and-hold benchmark

默认配置下会启用，而且结果放在：

`storage/reports/backtest/{run_id}/per_symbol_benchmark/`

主要文件如下：

| 文件 | 默认是否启用 | 含义 |
| --- | --- | --- |
| `per_symbol_benchmark_nav.parquet` | 是 | 每只股票的买入持有 NAV 矩阵 |
| `per_symbol_benchmark_metrics.parquet` | 是 | 每只股票的逐日指标明细 |
| `per_symbol_benchmark_metrics.jsonl` | 是 | 同上，JSONL 版 |
| `{SYMBOL}_metrics.txt` | 是 | 单只股票最后一日的关键指标摘要 |
| `{SYMBOL}_metrics.png` | 是 | 单只股票指标图 |

这套 per-symbol 指标来自 `compute_nav_to_metrics_series()`，可产生：

| 字段 | 含义 |
| --- | --- |
| `nav` | 单只股票买入持有净值 |
| `cum_return` | 相对该股票首个有效 NAV 的累计收益 |
| `max_drawdown_to_date` | 截至当日的累计最大回撤序列 |
| `sortino` | 可选 `rolling` 或 `to_date` 的 Sortino 序列 |

当前默认配置是：

- `metrics: [cum_return, max_drawdown, sortino]`
- `sortino.mode: to_date`
- `sortino.window: 63`
- `save_format: [text, image]`

所以默认情况下：

- `per_symbol_benchmark_metrics.parquet` 通常只保留 `cum_return`、`max_drawdown_to_date`、`sortino` 三列。
- 单股 PNG 默认主要画的是 `cum_return`，因为默认 metrics 里没有 `nav`。

这套 buy-and-hold benchmark 的金融意义是：

- 它不是“策略自己的 NAV”。
- 它是在同一股票池内，为每只股票构造一个“只买不动”的被动持有基线。
- 用来回答“策略主动交易是否优于简单持有这些股票”。

补充实现细节：

- 这套 per-symbol benchmark 会把手续费和滑点只计入首日建仓，后续纯持有。
- 所以它是“带一次性入场成本的被动持有基准”。

### 6.3 默认启用的图形化分析

默认还会生成下列图形结果：

| 文件 | 含义 | 经济金融意义 |
| --- | --- | --- |
| `aggregated_cumreturn_analysis.png` | 全股票累计收益图 | 看股票池横截面的收益分布，以及等权/加权平均基线 |
| `stock_price_trends.png` | 标准化价格趋势图 | 看股票池路径形态与分化 |
| `individual_stocks/README.md` | 每只股票摘要 | 看单股持有表现 |
| `multi_period_performance_heatmap.png` | 多窗口收益热力图 | 看 5/10/21/42/63/126/252 日窗口上的相对强弱 |
| `rolling_sortino_comparison.png` | 滚动 Sortino 对比 | 看风险调整收益是否稳定 |
| `rolling_sharpe_comparison.png` | 滚动 Sharpe 对比 | 看总波动口径下的风险收益稳定性 |
| `rolling_drawdown_comparison.png` | 滚动/累计回撤对比 | 看各股票的下行风险路径 |
| `performance_ranking_over_time.png` | 绩效排名变化图 | 看横截面 leader 是否稳定 |
| `benchmark_comparisons/strategy_vs_simple_avg/nav_comparison.png` | 策略 vs 简单平均基准 | 主动策略是否优于等权平均持有 |
| `benchmark_comparisons/strategy_vs_simple_avg/totalassets_comparison.png` | 策略 vs 简单平均总资产 | 以资产金额视角比较 |
| `benchmark_comparisons/strategy_vs_weighted_avg/*` | 策略 vs 加权平均基准 | 看是否优于加权持有 |
| `benchmark_comparisons/strategy_vs_spy/*` | 策略 vs SPY | 看是否优于市场 ETF 基准 |

## 7. 准确性备注：代码与仓库文档的偏差

这是我认为最重要的“防误读”部分。

### 7.1 `README` / `storage/README` 有一些已经过时

当前实现里，`write_outputs()` 并不会写出下面这些 README 中提到的文件：

- `config.json`
- `benchmark_meta.json`

所以如果后面有人做自动化抓取，不要按 README 假定这些文件存在。

### 7.2 benchmark 字段说明和实际实现不完全一致

代码里构造旧式 benchmark 时，`engine.run()` 调用的是：

- `load_benchmark_components(..., field="adjusted_close")`

也就是说：

- `config.yaml` 里 `backtest.benchmark.field: close` 这项，在旧式 benchmark 路径中并没有真正传进去。
- 实际上是优先读 `adjusted_close`，没有时才回退到 `close`。

这会带来两个准确性后果：

- benchmark 价格口径和配置展示出来的字段名不一定一致。
- 策略 NAV 用的是开盘成交后估值，而旧式 benchmark 倾向于用 `adjusted_close`，两者口径并不完全同构。

### 7.3 `alpha_simple` 不是传统金融学里的 alpha

当前实现里：

- `alpha_simple = excess_return_annual`

所以它不是 CAPM / 多因子回归里的 alpha，只是一个“简单年化超额收益”。

### 7.4 `excess_return_total` 不是复利口径的主动收益

它是：

- 把每日超额收益直接求和

因此更像“日超额收益累计近似值”，而不是严格复利下的主动净值差。

### 7.5 `benchmark_nav` 在组合快照里目前没有被真正填入

`PortfolioSnapshot` 虽然有 `benchmark_nav` 字段，但 engine 创建快照时没有把实际 benchmark 传进去，所以：

- `detailed_portfolio_snapshots.jsonl` 里的 `benchmark_nav` 目前基本可以视为占位字段。

### 7.6 详细日志目录在 run_id 冲突时可能和最终主报告目录分离

实现顺序是：

1. engine 先把 `detailed_*` 文件写到 `storage/reports/backtest/{run_id}`。
2. 随后 `write_outputs()` 才检查目录冲突，并在必要时给最终报告目录追加时间戳后缀。

所以如果 `run_id` 撞名：

- `detailed_trades.jsonl` / `detailed_portfolio_snapshots.jsonl` / `detailed_trading_summary.json`
- 以及最终的 `metrics.json` / `daily_nav.parquet` / `summary.txt`

有可能不在同一个最终目录里。

## 8. 最简结论

如果只抓最重要的几项：

- 主结果指标看 `storage/reports/backtest/{run_id}/metrics.json`。
- 汇总表看 `storage/reports/backtest/{run_id}/metrics_summary.csv`。
- 逐笔执行与组合过程看 `detailed_trades.jsonl`、`detailed_portfolio_snapshots.jsonl`、`detailed_trading_summary.json`。
- 相对基准表现看 `benchmark_nav.parquet` 加上 `metrics.json` 里的 relative metrics。
- 单股 buy-and-hold 基准与横截面分析看 `per_symbol_benchmark/` 整个目录。
- LLM 调用过程审计看 `storage/cache/llm/by_run/{run_id}/` 和 `logs/stockbench/{YYYY-MM-DD}.log`。

