按“交易次数”看，这 9 个完整年度 run 的关系更像是“过多交易明显变差”，而不是简单线性关系。粗看相关性，`trades_count` 和年化收益的相关系数大约是 `-0.50`。

| 模型 | 交易次数 | 年化收益 | 最大回撤 |
| --- | ---: | ---: | ---: |
| Qwen2.5-7B | 32 | 26.1% | -14.9% |
| DeepSeek-V3.1 | 43 | 56.0% | -11.9% |
| DeepSeek-V3.1 | 50 | 12.9% | -13.2% |
| DeepSeek-V3.1 | 93 | 18.3% | -6.9% |
| DeepSeek-V3.1 | 96 | 22.3% | -5.1% |
| DeepSeek-V3.1 | 110 | 13.4% | -7.3% |
| GPT-4o-mini | 143 | 12.4% | -14.6% |
| Claude Sonnet 4.5 | 955 | 6.9% | -14.6% |
| Gemini 3 Flash Preview | 1045 | 7.7% | -14.2% |

我的结论是：

1. 最好的结果集中在 `32-110` 笔，不在 `900+` 笔。
2. `955/1045` 这种级别已经不是“抓更多机会”，而是在做高频小修小补。
3. 但“低交易次数”本身也不自动等于高收益，关键是低频下有没有足够高 conviction。Qwen `32` 笔不错，GPT-4o-mini `143` 笔却一般。

**为什么 1000+ 笔反而收益低**

主要不是手续费本身，而是“低质量频繁调仓”把 alpha 稀释了。

证据很直接：

- Claude `955` 笔，Gemini `1045` 笔，分别在 `226/245` 个交易日里都有交易，平均每天约 `4.2` 笔。
- 这两组大部分都是小单。Claude 约 `90%` 的交易小于 `$1,000`，Gemini 约 `77%` 小于 `$1,000`。
- 中位单笔金额只有 `417` 和 `630`，对一个 `$100k` 组合来说，更像连续微调，不像高 conviction 换仓。

这说明问题更像：

- 模型对短期噪声过度响应，频繁改目标仓位。
- 很多交易只是把仓位来回拧一点点，没形成真正的方向性暴露。
- 胜率和赔率都不够高时，交易越多，越容易把组合拖回均值。

我反而不认为“手续费把收益吃掉”是主因。按你当前 `1bps commission + 2bps slippage` 粗算：

- Claude 全年显性摩擦成本大约 `$164`
- Gemini 大约 `$261`

这只占 `$100k` 初始资金的 `0.16%-0.26%`。解释不了从优质 run 的 `20%-50%+` 年化掉到 `7%` 左右。主因还是决策质量，不是 cost。

**7b 小模型为什么交易次数低**

更接近“它自己大部分时间就在 hold”，不是“系统大量默认 hold”。

我把 7b 的决策缓存扫了一遍，结果是：

- 总决策日：`249`
- 最终动作分布：`4483 hold / 47 increase / 4 decrease`
- 最终 `hold` 占比：`98.88%`
- `211` 天是“全股票全 hold”
- 只有 `38` 天出现任何非 hold 动作

所以，7b 的低交易次数，主因就是它最终产出的策略本身极度保守，几乎天天都是 hold。

但你怀疑的“指令遵循不足”也不是完全没有。它确实经常在重试里犯错：

- `117/249` 天发生了 retry
- 其中 `107` 天 retry 后动作发生变化
- 共改动了 `491` 个 symbol-action

也就是说，7b 对 `action + target_cash_amount` 这套输出约束确实吃力，尤其容易把 `increase` 和当前持仓值搞反。系统在这里会先重试，必要时才 fallback 为 hold，逻辑在 [dual_agent_llm.py](/home/azanette/code/STOCK-AGENT/stockbench/stockbench/agents/dual_agent_llm.py#L573) 和 [dual_agent_llm.py](/home/azanette/code/STOCK-AGENT/stockbench/stockbench/agents/dual_agent_llm.py#L894)。另外，接近零 delta 的错误动作还会被强制改成 hold，见 [llm_decision.py](/home/azanette/code/STOCK-AGENT/stockbench/stockbench/backtest/strategies/llm_decision.py#L924)。

但关键点是：我在 7b 的“最终接受的决策”里，没有看到明显的 fallback-hold 理由命中。也就是：

- 系统有默认 hold 兜底机制
- 7b 也确实常常需要重试
- 但这次 run 低交易次数的主因，仍然是它最终通过校验后的输出本来就几乎全是 hold

一句话总结：

- `1000+` 笔交易差，不是因为“更勤奋”，而是因为“更噪声”。
- `7b` 交易少，主要是“模型真的偏向 hold”，不是“系统大规模代替它 hold”；只是它的指令遵循偏弱，会进一步把少量本可交易的动作压回去。

如果你愿意，我下一步可以把这段直接整理成一版可贴进 [finding1.md](/home/azanette/code/STOCK-AGENT/chat/finding1.md) 或 [proposal1_workflow_cn.md](/home/azanette/code/STOCK-AGENT/chat/proposal1_workflow_cn.md) 的正式分析文字。