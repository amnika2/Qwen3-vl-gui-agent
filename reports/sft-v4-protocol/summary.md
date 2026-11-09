# v4训练应用审查验证

本报告使用训练应用验证集的固定截图与动作历史，衡量审查阶段。五个冻结应用的端到端成绩另行评估。

23条原验证样本逐条匹配运行和标签；v3旧、新协议使用同一adapter，v3/v4新协议使用相同代码、512字符说明上限、生成参数、提示词和图片。

| 指标 | v3旧协议 | v3新协议 | v4新协议 |
|---|---:|---:|---:|
| 正确判定 | 16/23 | 11/23 | 14/23 |
| 正确率 | 69.6% | 47.8% | 60.9% |
| 格式拒绝 | 5 | 3 | 0 |
| 证据要求拦截 | 3 | 12 | 4 |
| fail误判pass | 0 | 0 | 2 |
| pass误判fail | 0 | 0 | 0 |
| blocked标签判pass | 0 | 1 | 1 |
| 字段原文完全一致 | 65/106 | 66/108 | 66/108 |

| 新增2条验证样本 | v3新协议 | v4新协议 |
|---|---:|---:|
| 正确判定 | 0/2 | 2/2 |
| 正确率 | 0.0% | 100.0% |
| 格式拒绝 | 1 | 0 |
| 证据要求拦截 | 0 | 0 |
| fail误判pass | 0 | 0 |
| pass误判fail | 0 | 0 |
| blocked标签判pass | 0 | 0 |
| 字段原文完全一致 | 2/7 | 3/7 |

格式拒绝统计所有解析错误，包含重复字段、引用步骤不合法；被格式或证据要求挡住的预测按blocked保留在正确率分母中。
字段原文完全一致按非null标注计数，包含可见空串。新协议增加基准和字段，其分母与旧协议不同，应分别阅读这些数量。

| 标签→预测 | v3旧协议 | v3新协议 | v4新协议 |
|---|---:|---:|---:|
| pass→pass | 8 | 5 | 10 |
| pass→fail | 0 | 0 | 0 |
| pass→blocked | 3 | 6 | 1 |
| fail→pass | 0 | 0 | 2 |
| fail→fail | 2 | 0 | 0 |
| fail→blocked | 3 | 5 | 3 |
| blocked→pass | 0 | 1 | 1 |
| blocked→fail | 1 | 0 | 2 |
| blocked→blocked | 6 | 6 | 4 |

无说明长度上限的初次试跑实际完成2/23条；观察到说明文字重复耗尽生成预算后停止，并加入512字符上限。该试跑独立保留，主比较使用完整有上限运行。[原输出](v3-new-unbounded-pilot/predictions.jsonl)。

## 实际UI运行

| 运行 | 场景 | Agent | Oracle | 预期 | 两项均符合 |
|---|---|---|---|---|---|
| [v3-lite](../sft-v4-live/v3-lite/result.json) | fc-lite-code-country | blocked | inconclusive | fail | False |
| [v3-wolf](../sft-v4-live/v3-wolf/result.json) | fc-wolf-service-breadcrumb | blocked | inconclusive | pass | False |
| [v4-lite](../sft-v4-live/v4-lite/result.json) | fc-lite-code-country | blocked | inconclusive | fail | False |
| [v4-wolf](../sft-v4-live/v4-wolf/result.json) | fc-wolf-service-breadcrumb | blocked | inconclusive | pass | False |

- [v3-lite实际轨迹](../../runs/20261108T061230Z-a674df84)：`controlled_review_guarded`。
- [v3-wolf实际轨迹](../../runs/20261108T061911Z-d05a4315)：`controlled_review_guarded`。
- [v4-lite实际轨迹](../../runs/20261108T064023Z-c6ab416d)：`controlled_review`。
- [v4-wolf实际轨迹](../../runs/20261108T064151Z-97d1e078)：`controlled_review_guarded`。

## 来源

[完整比较与逐样本prompt、图片哈希](comparison.json)记录所有代码、权重、输入和输出来源。
[汇总脚本](../../scripts/summarize_sft_v4_validation.py)的SHA256为 `120ce643ac278a92e4e7d9ed911397657c5d42af4d9115e250c46bac16eed7eb`。
- v3-old：[manifest](v3-old/manifest.json)、[原始预测](v3-old/predictions.jsonl)、[指标](v3-old/summary.json)。
- v3-new：[manifest](v3-new/manifest.json)、[原始预测](v3-new/predictions.jsonl)、[指标](v3-new/summary.json)。
- v3-curriculum：[manifest](v3-curriculum/manifest.json)、[原始预测](v3-curriculum/predictions.jsonl)、[指标](v3-curriculum/summary.json)。
- v4-new：[manifest](v4-new/manifest.json)、[原始预测](v4-new/predictions.jsonl)、[指标](v4-new/summary.json)。
- [training-source.tar.gz](../../runs/sft-qwen3vl-10apps-v4/training-source.tar.gz)，SHA256 `7f2d9e1ac3a05ffea55d9ddedbf95d1b9a61a03046b26c299ffb4f6038d05d71`。
- [training-source.json](../../runs/sft-qwen3vl-10apps-v4/training-source.json)，SHA256 `ced1b23848cfb60cac6bfb3295766a414315f59868ab4ae7ef253f5c3c9ffbb9`。
- [bounded-inference-source.tar.gz](../../runs/sft-v4-protocol-workers/bounded-inference-source.tar.gz)，SHA256 `79462b25b3e18b5d2ea7f7daf457b1fa1fb407973de6a4421a2b32895a5cf7a0`。
- [bounded-inference-fix.json](../../runs/sft-v4-protocol-workers/bounded-inference-fix.json)，SHA256 `836971059ff1697d9f60cd7206b3fd649cb734aa4e70d2ac4ae0dd636fabbdf2`。
