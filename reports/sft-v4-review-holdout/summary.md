# SFT v4：原轨迹重审

本报告重新审查 v3 在冻结五应用上已完成的40条原轨迹。操作与独立 oracle 沿用 v3 原运行；没有重新执行操作。这里报告的是原轨迹重审结果，不能作为新40次端到端评估成绩。

checkpoint 仅按训练应用验证集最低 eval_loss 选择：epoch 2.0，`/home/fs_2026/qwen3-gui-agent/runs/sft-qwen3vl-10apps-v4/checkpoint-170`。冻结五应用的重审结果未参与选择。

| 指标 | v3 原结果 | v4 原轨迹重审 |
| --- | ---: | ---: |
| 正确判定 | 26/40 | 36/40 |
| 缺陷检出 / 召回 | 6/10 | 6/10 |
| 正常误报 | 0/30 | 0/30 |
| blocked | 14/40 | 4/40 |

新审查解析错误 0/40，guard 挡住 1/40。原14条 blocked 恢复 10/14；原26条正确结果退步 0/26。全部40条均保留，解析错误、guard 与 blocked 计入分母。

| 应用 | 原正确 | 重审正确 | 缺陷检出 | 正常误报 | blocked | 恢复 | 退步 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Akaunting (sweeper-005) | 8/8 | 8/8 | 2/2 | 0/6 | 0/8 | 0 | 0 |
| Ampache (sweeper-007) | 2/8 | 6/8 | 0/2 | 0/6 | 2/8 | 4 | 0 |
| Anchor CMS (sweeper-008) | 8/8 | 8/8 | 2/2 | 0/6 | 0/8 | 0 | 0 |
| EasyAppointments (sweeper-040) | 6/8 | 6/8 | 0/2 | 0/6 | 2/8 | 0 | 0 |
| emlog (sweeper-041) | 2/8 | 8/8 | 2/2 | 0/6 | 0/8 | 6 | 0 |

| 原 blocked 原因 | 原条数 | 恢复正确 | 仍 blocked | 重审错误判定 |
| --- | ---: | ---: | ---: | ---: |
| 格式拒绝 | 8 | 6 | 2 | 0 |
| 其余证据判断 | 6 | 4 | 2 | 0 |

退步轨迹：

无。

4个分片均完成；40条源轨迹及动作保持不变，原始文件与所用截图哈希复核通过。四份 adapter、配置、生成参数、源数据、推理代码与 checkpoint 选择记录一致。

完整结果见 [results.jsonl](results.jsonl)，数值与来源哈希见 [summary.json](summary.json)。

来源：
- [method](../../docs/SFT_V4.md)
- [training_completion](../../runs/sft-qwen3vl-10apps-v4/completion.json)
- [training_source](../../runs/sft-qwen3vl-10apps-v4/training-source.json)
- [training_source_archive](../../runs/sft-qwen3vl-10apps-v4/training-source.tar.gz)
- [bounded_inference](../../runs/sft-v4-protocol-workers/bounded-inference-fix.json)
- [bounded_inference_archive](../../runs/sft-v4-protocol-workers/bounded-inference-source.tar.gz)
