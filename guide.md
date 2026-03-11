# Lightweight Workflow Optimization for Trading Agents

基于 **StockBench** 的轻量级 Trading Agent Workflow 优化研究。

## 1. 项目简介

本项目关注 **trading agent 的 workflow 优化**，而不是单纯构建一个新的 benchmark。我们的核心目标是：

> 在尽量不增加系统复杂度、不过度消耗 token 的前提下，改进现有 trading agent 的决策流程，使其在真实股票交易环境中表现得更稳定、更可执行，并具备更好的风险控制能力。

本项目以 **StockBench** 为基础参考。StockBench 将股票交易任务建模为一个真实市场环境中的连续决策问题：Agent 接收每日价格、基本面和新闻信息，对股票做出 sequential buy / sell / hold 决策，并通过收益与风险指标进行评估。

## 2. 背景与动机

StockBench 的设计强调两个特点：

1. **workflow 尽量 minimal**，避免过于复杂的 agent 结构引入额外偏置；
2. **场景尽量 realistic**，贴近零售投资者的日常决策流程。

其 workflow 大致包含四个阶段：

1. **Portfolio Overview**：先浏览全部候选股票；
2. **In-Depth Stock Analysis**：再挑选少数股票做深入分析；
3. **Decision Generation**：生成 increase / decrease / hold 决策；
4. **Execution and Validation**：将目标仓位转成可执行交易，并检查流动性约束。

这一设计很适合作为研究起点，但仍存在几个可优化点：

- 随着股票池扩大，模型稳定性可能下降；
- 新闻是有效信息源，但原始新闻文本较耗 token；
- 算术错误与输出格式错误会影响执行质量；
- agent 对市场状态变化较敏感，在不同 regime 下表现不稳定；
- 历史动作如果直接以长文本输入，容易造成上下文冗余。

因此，本项目希望在 **不显著增加推理负担** 的条件下，对 workflow 做轻量改进。

## 3. StockBench 基础设定（本项目参考）

本项目参考 StockBench 的核心设定：

- 投资标的：从 **DJIA 中选出的 20 只股票**；
- 评测区间：**2025-03-03 至 2025-06-30**，共 **82 个交易日**；
- 输入信息：
  - 持仓股票过去 7 天历史动作；
  - 前 48 小时内最多 5 条相关新闻；
  - 被选中股票的基本面数据（如市值、P/E、股息率、52 周高低点等）；
- 评估指标：
  - Final Return
  - Maximum Drawdown
  - Sortino Ratio

这些设定保证了实验具有一定现实性，也为 workflow 优化提供了统一比较基准。

## 4. 本项目的核心思路

本项目不尝试大幅重写 StockBench，而是在其 **minimal workflow** 基础上做几项轻量级增强：

### 4.1 Top-K 候选股票粗筛

在完整深入分析之前，先对全部候选股票做一次廉价筛选，只保留 **Top-K（如 3 或 5 只）** 进入深度分析阶段。

粗筛信号可以来自以下低成本特征：

- 是否已有持仓；
- 新闻方向是否明显偏利多/利空；
- 当前价格相对短期均线的位置；
- 是否接近 52 周高点或低点。

**目标：**
- 减少后续上下文长度；
- 降低多资产场景下的决策噪声；
- 让 agent 把注意力集中在更可能需要调仓的股票上。

### 4.2 新闻压缩为结构化事件卡片

不直接输入多条原始新闻，而是先将新闻压缩为简短的 **event cards**：

- `event_type`: 财报 / 并购 / 宏观 / 法律风险 / 指引变动
- `direction`: bullish / bearish / neutral
- `horizon`: short-term / medium-term
- `summary`: 一句话摘要

**目标：**
- 保留新闻中最关键的事件与方向信息；
- 显著降低 token 消耗；
- 提升模型对新闻信号的可读性与可对比性。

### 4.3 离散化仓位决策

将自由形式的仓位目标，改为离散动作：

- `strong_buy`
- `buy`
- `hold`
- `sell`
- `strong_sell`

再由程序将其映射为固定仓位变化，如：

- `strong_buy` = +5%
- `buy` = +2%
- `hold` = 0
- `sell` = -2%
- `strong_sell` = -5%

**目标：**
- 降低 arithmetic error；
- 降低 schema error；
- 使 agent 输出更稳定、更容易执行。

### 4.4 规则化风险约束（Risk Gate）

在决策生成后加入一个轻量的 rule-based 风控层，例如：

- 单只股票最大权重不超过 10%；
- 单日总换手率不超过 20%；
- 浮亏超过阈值时禁止继续加仓；
- 行业集中度不超过预设上限。

**目标：**
- 提升组合稳定性；
- 降低极端调仓行为；
- 在几乎不增加 token 的情况下改善风险收益表现。

### 4.5 市场状态标记（Regime Tag）

在 prompt 中增加一个很短的全局市场状态输入：

- `bullish`
- `bearish`
- `sideways`

该状态可由简单规则生成，例如通过指数短中期收益、波动率水平等确定。

**目标：**
- 让 agent 在不同市场环境下采取不同交易节奏；
- 缓解模型在 bearish market 中容易失效的问题。

### 4.6 历史动作压缩

将过去 7 天历史动作由逐日文本记录，压缩为持仓状态摘要：

- current weight
- average entry price
- unrealized PnL
- holding days
- last action
- last action reason

**目标：**
- 减少冗余上下文；
- 保留对当前决策最有帮助的持仓状态信息。

## 5. 改进后的 Workflow

改进后的 trading workflow 可以概括为：

1. **Market Overview**：浏览全部股票的价格、新闻摘要、当前持仓；
2. **Top-K Shortlisting**：根据简单信号筛出少数重点股票；
3. **Structured Analysis**：对重点股票读取事件卡片与基本面数据；
4. **Discrete Decision Generation**：输出离散调仓动作；
5. **Risk Gate**：通过规则检查仓位、换手率与集中度约束；
6. **Execution**：将动作映射为具体仓位与股数并执行。

相较于原始 workflow，该流程仍然保持简单，但更强调：

- 信息压缩；
- 候选筛选；
- 输出规范化；
- 风险控制。

## 6. 研究问题

本项目重点关注以下几个研究问题：

### RQ1
轻量级 workflow 优化是否能够在不明显增加 token 消耗的前提下，提升 trading agent 的收益稳定性？

### RQ2
结构化新闻压缩是否能够保留新闻信号的有效性，同时减少长上下文带来的噪声？

### RQ3
离散化决策与规则化风控是否能有效减少执行错误，并改善最大回撤与风险调整收益？

### RQ4
市场状态标记是否能增强 trading agent 在不同 market regime 下的适应能力？

## 7. 预期贡献

本项目的预期贡献包括：

1. 提出一套 **轻量级 trading agent workflow 优化方案**；
2. 在 StockBench 框架下系统评估各模块的有效性；
3. 展示如何在 **低复杂度、低 token 成本** 条件下提升 agent 的执行稳定性与风险控制能力；
4. 为后续 trading agent 的 workflow 研究提供一个更清晰的优化方向：
   - 不是单纯堆更大模型；
   - 而是通过更合理的信息组织和决策流程提升整体表现。

## 8. 实验设计建议

为了保证实验结构清晰，本项目可以采用以下对比方式：

### Baseline
- 原始 StockBench workflow

### Proposed Variants
- Baseline + Top-K Shortlisting
- Baseline + Event Card Compression
- Baseline + Discrete Positioning
- Baseline + Risk Gate
- Baseline + Regime Tag
- Full Lightweight Workflow

### Evaluation Metrics
- Final Return
- Maximum Drawdown
- Sortino Ratio
- Token Usage
- Arithmetic / Schema Error Rate

## 9. 项目定位

本项目更适合被表述为：

> **面向真实股票交易场景的 Trading Agent Workflow Optimization**

而不是：

> 构建一个新的 benchmark evaluation framework

换句话说，**benchmark 在这里是验证手段，不是研究主角**。研究主线应聚焦于：

- 如何更高效地组织输入信息；
- 如何让 agent 做出更稳健、可执行、低成本的交易决策；
- 如何在 minimal workflow 框架内提升整体表现。

## 10. 后续工作

后续可以进一步探索：

- 更细粒度的新闻事件分类；
- 行业暴露与组合层面的动态风险预算；
- 更强的 regime-aware prompt 设计；
- 在不同股票池规模上的泛化能力评估；
- 在不同 market window 下的鲁棒性分析。

## 11. 参考资料

- StockBench: *Can LLM Agents Trade Stocks Profitably In Real-world Markets?*
- StockBench Project Page
- StockBench GitHub Repository

---

如果将本项目用于开题、proposal 或仓库首页，这份 README 的核心信息可以概括为：

> 本研究不是单纯评估 trading agent，而是以 StockBench 为基础，研究如何通过轻量级 workflow 优化，在有限 token 预算下提升 trading agent 的稳定性、可执行性和风险控制能力。