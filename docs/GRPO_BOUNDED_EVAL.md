# 39 题统一输出约束对照

评估日期：2026-11-08。只重新解码，不改权重。两模型使用原 evidence_v2 JSON schema、可引用截图步骤枚举及两处说明各 512 字符上限；greedy、1024 新 token、seed42、单样本，提示、截图、字段别名和评分规则保持。冻结五应用未参与。

| 模型及输出方式 | 终判正确 | guard后正确 | 结构化正确 | 正常误报 | 缺陷检出 | blocked 正确 | 格式错误 | 截断 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v4，无约束 | 19/39 | 18/39 | 1/39 | 0/18 | 2/9 | 5/12 | 15 | 15 |
| checkpoint-10，无约束 | 19/39 | 20/39 | 2/39 | 1/18 | 1/9 | 4/12 | 14 | 14 |
| v4，同一现有约束 | 28/39 | 25/39 | 2/39 | 1/18 | 3/9 | 8/12 | 0 | 0 |
| checkpoint-10，同一现有约束 | 29/39 | 26/39 | 2/39 | 1/18 | 6/9 | 6/12 | 0 | 0 |

相同输出约束下，checkpoint-10 相对 v4 恢复 4 题、退步 3 题；两者均正确 25 题、均错误 7 题。终判正确按完整 JSON 中的 verdict 统计，与本轮原口径一致。guard 后正确仅在完整、可解析回答上将 guard 触发的终判转为 blocked；截断或格式错误不计正确。

按实际系统的 guard 处理，缺陷检出为 v4 0/9、checkpoint-10 2/9。表中的缺陷检出列记录模型原始终判；部分 fail 答案因自身字段或提交状态矛盾被 guard 转为 blocked。

结构化评分沿用原规则：已核验正确、已确认错误、尚待核验、截断无法评分分别统计。字段别名或替代截图引用尚待核验时，不计为已确认事实错误；终判正确也不等于其引用和说明全部正确。自由说明事实正确性未进入此评分。

| 输出方式 | 已核验结构化正确 | 已确认错误 | 无已确认错误但待核验 | 截断无法评分 |
| --- | ---: | ---: | ---: | ---: |
| v4，无约束 | 1 | 9 | 14 | 15 |
| checkpoint-10，无约束 | 2 | 10 | 13 | 14 |
| v4，同一现有约束 | 2 | 22 | 15 | 0 |
| checkpoint-10，同一现有约束 | 2 | 21 | 16 | 0 |

逐题配对、来源与权重一致性核验见 [comparison.json](../reports/rl-review-bounded-20261108/comparison.json)。原无约束目录保持；新回答、清单、进程退出码、运行信息及源码快照位于 [本次报告目录](../reports/rl-review-bounded-20261108)。

重新生成时使用新目录，保留本次报告：

```bash
CUDA_VISIBLE_DEVICES=2 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
  .model-venv/bin/python scripts/evaluate_review_grpo.py \
  --adapter runs/sft-qwen3vl-10apps-v4-review-selected/adapter \
  --schema-constrained --output reports/rl-review-bounded-reproduction/v4
CUDA_VISIBLE_DEVICES=3 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
  .model-venv/bin/python scripts/evaluate_review_grpo.py \
  --adapter runs/rl-review-grpo-retry-v1/checkpoint-10 \
  --schema-constrained --output reports/rl-review-bounded-reproduction/checkpoint-10
```

对本次已保存的四组结果重新汇总：

```bash
.venv/bin/python scripts/summarize_bounded_review_comparison.py
```
