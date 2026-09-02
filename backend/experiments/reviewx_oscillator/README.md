# ReviewX 阻尼振子闭环实验

本实验在每个重复 80 个观测点、相同优化器评估上限下比较两轮二阶阻尼系统辨识。Round 1 使用远离共振的单频稳态采样；ReviewX 根据残差、Fisher 信息条件数和参数误差生成安全候选，Qwen 只能从可行候选中选择并解释 Plan Delta；Round 2 只重跑受影响节点。

最终留出种子在 `config/frozen_protocol.json` 中冻结，Qwen prompt 只含开发集聚合诊断，不含最终留出观测或指标。主分析为至少 2,000 次成对 Bootstrap，并报告 Wilcoxon、符号置换检验、逐种子结果、参数误差与固定预算 guardrail。CI 跨 0 时自动输出 `BOUNDARY`。

种子 `3001-3030` 已因工程预演而退役，只保留其来源说明，不得用于正式推断。正式未见集冻结为 `5001-5030`；自动化测试使用独立的 `99001+` 种子空间，不会提前消耗正式未见集。

正式代表运行：

```bash
cd backend
./.venv/bin/python -m experiments.reviewx_oscillator.run \
  --config experiments/reviewx_oscillator/config/frozen_protocol.json \
  --output ../docs/tempdocs/0902reviewx_oscillator_run \
  --provider qwen \
  --require-real-api
```

缺少百炼凭据或网络不可用时命令会明确失败，不会回退为 mock。`human_signoff.json` 始终由开发运行初始化为 `pending`；真实批准必须在受信 ReviewX 签核页面中由负责人完成。
