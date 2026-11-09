# sft-lora-v2

当前截图环境下重新执行 base 和 selected adapter，每种权重 40 格。

| 权重 | 完成 | 正确格 | 缺陷检出 | 正常误报 | 四格全对 | target成对正确 | 主动finish | 中位动作 | 未完成 | 环境故障 |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 40/40 | 8/40 | 2/10 | 1/30 | 0/10 | 1/10 | 7/40 | 7.0 | 30 | 0 |
| adapter | 40/40 | 25/40 | 2/10 | 0/30 | 2/10 | 2/10 | 35/40 | 9.0 | 5 | 0 |

主动finish计数来自model_proposed_finish，分母含全部已运行格。四格全对按应用和重复编号计数。
失败全部保留；interrupted的后续同格记录覆盖报告视图，原始行保留。

逐应用推理核心一致性：{"sweeper-005": true, "sweeper-007": true, "sweeper-008": true, "sweeper-040": true, "sweeper-041": true}。
推理文件与五应用任务/fixture的逐文件hash见comparison.json；训练和builder源码不参与推理hash。

base guard原因：{"review_evidence_not_established:requested_value_entered,submission_succeeded": 2, "review_evidence_not_established:requested_value_entered": 6}。
base 主动finish细分：agent/oracle按预期共同正确 3；blocked或oracle inconclusive 4；主动finish后的环境故障 0。
adapter guard原因：{"last_edit_not_submitted": 5}。
adapter 主动finish细分：agent/oracle按预期共同正确 23；blocked或oracle inconclusive 12；主动finish后的环境故障 0。

逐格结果和证据来源见 [pairs.csv](pairs.csv)。重复不增加应用数。
