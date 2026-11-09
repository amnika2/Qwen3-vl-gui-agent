本轮在第 42 步提前停止，保留 10/20/30/40 步完整检查点，继续选用 SFT v4。10/20/30 步均未改善独立验证集的缺陷检出，后续训练和自动评估队列已停止。[结果诊断](GRPO_PILOT_DIAGNOSIS.md)。

原计划用选定的 SFT v4 做 50 步 Dr. GRPO 小规模验证。训练在 402 GPU0，独立评估使用 402 GPU1、404 GPU2/3，运行产物单独保存。

v2 首次有效更新已经确认：首组奖励 `[1.2, 1.2, -0.83333333, 1.2]`，梯度 L2 为 `0.15089887548`，主 adapter 参数实际改变，参考和原 v4 磁盘权重保持不变。[首次更新证据](../runs/rl-review-grpo-pilot-ops-v1/v2-first-update-verification.json)。

训练固定选择 32 题：10 fail、12 pass、10 blocked，覆盖十个训练应用。其中 26 题来自针对性补采，6 题作为其他应用的锚点。保留了正确输入后保存失败、输入未建立、取消保存，以及正常保存和预期拒绝之间的对照。39 道验证题按场景、来源运行与训练题分开，五个留出应用继续保持独立。

参数为 G4、单卡 microbatch 1、梯度累积 4、学习率 `2e-6`、beta `0.02`、最多生成 1024 tokens。每题的四个回答顺序生成，收齐后计算组内奖励。参考 adapter 精确复制 v4 后冻结，视觉层冻结。每 10 步保存模型、优化器、调度器、RNG 和训练状态。

32 题按 seed 42 打乱一次，随后固定顺序循环。保存后的 `ready.json` 标记整个检查点已写完，评估进程再开始读取。恢复时明确加载主 adapter 和冻结参考 adapter，恢复优化器与训练步数。CPU 双 adapter 实测确认恢复权重一致、参考参数冻结、CUDA 未初始化。

基准 v4 与 10/20/30/40/50 步检查点使用同一套 39 题，greedy 解码，不加 schema 约束。逐项保存原始回答，比较终判正确、结构化正确、缺陷检出、正常误报、正确 blocked、格式错误和截断。该协议针对当前 RL 生成流程；此前带 schema 的 40 条留出成绩使用另一套题和解码协议。

奖励使用已经修复的统一评分函数。仍有未知证据的同题 G4 整组任务奖励置零；截断回答保留无法打分。日志同时记录有效奖励差异和实际非零梯度更新，训练步数与有效学习次数分别统计。自由文本解释中的矛盾仍单独于结构化奖励。

运行记录：

- [训练配置](../configs/rl-review-grpo-pilot.yaml)
- [v2 训练计划](../runs/rl-review-grpo-pilot-v2/plan.json)
- [训练日志](../runs/rl-review-grpo-pilot-ops-v1/train-v2.log)
- [基准验证](../reports/rl-review-grpo-pilot-baseline-v1/summary.json)
- [自动评估状态](../reports/rl-review-grpo-pilot-v2/status.json)
- [检查点对比](../reports/rl-review-grpo-pilot-v2/comparison.md)

基准和 10/20/30 步检查点评估已由后台 watcher 完成，比较表及逐条回答保留。40 步权重保留，未启动评估。当前选用配置保留 v4。

最初的 v1 启动用于检查完整运行流程，完成 4 步但未发生有效任务梯度更新。检查发现 Transformers 的双 adapter 恢复路径会漏载根目录主 adapter，TRL 的跨轮随机采样恢复也会改变题序，随后停止 v1 并修复。原代码、配置与日志保留在 `runs/rl-review-grpo-pilot-v1/source-snapshot`，停止记录为 `runs/rl-review-grpo-pilot-ops-v1/superseded.json`。

恢复命令保留如下。本轮验证结果支持先修正实际训练题的奖励覆盖，再从 v4 重做短试；当前检查点不继续训练。

```bash
CUDA_VISIBLE_DEVICES=0 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
.rl-venv/bin/python -u scripts/train_review_grpo_pilot.py \
  --output runs/rl-review-grpo-pilot-v2 --resume
```
