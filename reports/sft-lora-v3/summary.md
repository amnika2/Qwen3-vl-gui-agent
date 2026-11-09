# sft-lora-v3

v2和v3均加载已选LoRA，在相同evidence_v1协议下重新评估；每种权重计划40格。

| 权重 | 完成 | 正确格 | 正常正确 | 缺陷检出 | 正常误报 | 成对正确 | 四格全对 | 主动且正确 | blocked | 审查格式错误 | 环境故障 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| v2 | 40/40 | 25/40 | 22/30 | 3/10 | 2/30 | 2/10 | 1/10 | 24/40 | 6 | 5 | 0 |
| v3 | 40/40 | 26/40 | 20/30 | 6/10 | 0/30 | 4/10 | 4/10 | 24/40 | 14 | 8 | 0 |

正确格要求模型、独立判定和预期三者一致。blocked、inconclusive、格式错误和环境故障均保留在分母中。
失败不重试，interrupted的后续同格结果覆盖报告视图，原始行保留。重复不增加应用样本数。
逐应用推理核心一致性：{"sweeper-005": true, "sweeper-007": true, "sweeper-008": true, "sweeper-040": true, "sweeper-041": true}。

字段读值计数表示已输出的非null原文数量，不表示OCR或语义读取正确率。

v2：主动finish 36/40，独立判定inconclusive 7，中位动作 9.5；结构化审查 35，输入/结果可见读值 39/39。
guard原因：{"review_evidence_not_established:submission_succeeded": 1}。

v3：主动finish 35/40，独立判定inconclusive 0，中位动作 11.0；结构化审查 32，输入/结果可见读值 52/56。
guard原因：{"review_evidence_not_established:submission_succeeded": 2, "review_evidence_not_established:requested_value_entered": 1}。

逐格配对及原文引用见 [pairs.csv](pairs.csv)，权重和推理核心哈希见 [comparison.json](comparison.json)。
