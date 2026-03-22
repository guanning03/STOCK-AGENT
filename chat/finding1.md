**意思**

这里的“调整为离散化的动作”，本质上是把现在这种“方向离散、幅度连续”的决策，改成“方向和幅度都离散”。

当前 baseline 其实已经有离散方向了：`increase / hold / decrease / close`，但真正决定仓位大小的是连续数值 `target_cash_amount`，见 [schemas.py](/home/azanette/code/STOCK-AGENT/stockbench/stockbench/core/schemas.py#L56)；prompt 也明确要求 `increase/decrease` 输出这个总目标仓位，见 [decision_agent_v1.txt](/home/azanette/code/STOCK-AGENT/stockbench/stockbench/agents/prompts/decision_agent_v1.txt#L110)；执行时再用 `cash_change = target_cash_amount - current_position_value` 去下单，见 [executor.py](/home/azanette/code/STOCK-AGENT/stockbench/stockbench/core/executor.py#L47)。  
所以你文档里说的“离散化动作”，更准确地说，是把 `target_cash_amount` 这种连续仓位幅度也收敛成有限几个档位。

**从 W&B 看**

下面这部分是基于本地 [wandb runs](/home/azanette/code/STOCK-AGENT/stockbench/wandb) 的推断，不是已经做过“动作数 ablation”的直接结论。

我看下来有三个很明显的现象：

1. 高频交易的 run 整体最差。  
`GPT-4o-mini` 143 笔、`Gemini` 1045 笔、`Claude` 955 笔，收益只有 6.7% 到 12.2%，都跑输 benchmark `1.1892`，最大回撤大约都在 `-14%` 左右。

2. 中等频率更稳。  
几组 `DeepSeek` 在 93 到 110 笔时，收益大约 13% 到 22%，最大回撤大约 `-5%` 到 `-7%`，Sortino 也明显更好。

3. 很低频、较集中的 run 能冲出高收益，但回撤不一定最好。  
最强那组 `DeepSeek` 只有 43 笔、9 个持仓，收益 54.9%，但回撤 `-11.9%`；`Qwen` 32 笔、6 个持仓，收益 25.6%，但回撤 `-14.9%`。

这说明两件事：

- 动作太细，模型容易过度响应，变成高换手噪声交易。
- 动作太粗，又容易走向过度集中，收益高但回撤也会放大。

**建议设计**

主实验我建议先用 `5` 个动作，不要直接上 `3`，也不要一开始就上 `7`。

推荐基线：

- `strong_buy`
- `buy`
- `hold`
- `sell`
- `strong_sell`

映射建议用“组合权重增减”，不要再让模型输出绝对金额：

- `strong_buy = +5%`
- `buy = +2.5%`
- `hold = 0`
- `sell = -2.5%`
- `strong_sell = -5%`

这样设计比较适合你当前 `max_positions = 20` 的设定，因为等权仓位本来就是约 `5%`。也就是说：

- `buy` 相当于先试探半仓位
- `strong_buy` 相当于直接打到一个标准仓位
- 卖出端同理

执行端建议做成：

- `new_weight = clip(current_weight + delta, 0, 10%)`
- 若卖出后小于 0，直接归 0
- 单日总换手继续走 Risk Gate 限制
- 真正的“一步清仓”不要作为常规动作，交给 Risk Gate 的 `force_close`

所以我会这样定：

- 论文主设定：`5` 动作
- 敏感性实验：`3 / 5 / 7` 动作
- 我对结果的预期：`3` 动作会更稳但可能太粗，`7` 动作更容易回到高换手，`5` 动作大概率是最好的折中

如果你愿意，我下一步可以直接帮你把这段写成一版可放进 `proposal1_workflow_cn.md` 的正式表述，连同“为什么选 5 个动作”和 “3/5/7 ablation 怎么写” 一起补进去。