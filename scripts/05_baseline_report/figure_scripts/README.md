# Figure Scripts

这个目录把 baseline 报告里用到的 matplotlib 图拆成了单独脚本，便于单张图复现。

## 目录说明

- `01_plot_baseline_primary_nav.py`
- `02_plot_baseline_primary_relative.py`
- `03_plot_baseline_primary_drawdown.py`
- `04_plot_baseline_primary_trade_values.py`
- `05_plot_baseline_trade_count_vs_return.py`
- `06_plot_baseline_trade_count_vs_return_all.py`
- `07_plot_baseline_deepseek_repeatability.py`
- `common.py`

## 用法

在仓库根目录执行，例如：

```bash
conda run --no-capture-output -n stockagent \
  python scripts/05_baseline_report/figure_scripts/01_plot_baseline_primary_nav.py
```

如果要自定义输出路径：

```bash
conda run --no-capture-output -n stockagent \
  python scripts/05_baseline_report/figure_scripts/05_plot_baseline_trade_count_vs_return.py \
  --output scripts/05_baseline_report/outputs/tmp_trade_count_vs_return.png
```

如果后面要补跑更多模型，可以直接配合：

```bash
MODELS_CSV="deepseek-v3.1,gpt-4o-mini,gemini-3-flash-preview-nothinking,claude-sonnet-4-5-20250929,MiniMax-M2.5" \
scripts/05_baseline_report/run_batch_nohup.sh
```

如果现在要复现实验里补跑的 `GLM` 批次，可以直接用：

```bash
MODELS_CSV="glm-4-flash" \
scripts/05_baseline_report/run_batch_nohup.sh
```

默认输入配置使用：

- `scripts/05_baseline_report/selection.json`

默认输出目录使用：

- `scripts/05_baseline_report/outputs/`
