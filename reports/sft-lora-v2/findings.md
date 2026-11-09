# v2 五应用配对结果

本轮重新执行当前截图环境下的 base40 与 selected adapter40，80 格全部完成，环境故障均为 0，所有失败保留。10 个 worker 的 27 个推理核心、任务及共享 agent 配置文件的组合哈希完全相同：`2f8e44a8054f66267d7a41469e1ee1d1af4474a46b2d87b1323a16f9d36ec3db`。各应用 A/B × target/preservation × 两次重复保持相同 prompt、30 动作和 600 秒预算。

v2 使用同一 Qwen3-VL-8B-Instruct 底座，从底座重新训练 2 epoch，共 170 optimizer steps，train1350/validation286。训练应用验证损失为 epoch1 `0.1532796621`、epoch2 `0.0785623938`，按较低验证损失选择 epoch2 的 `checkpoint-170`；五个 adapter worker 的权重哈希均与该导出一致。选择没有使用冻结五应用成绩，详见 [训练完成记录](../../runs/sft-qwen3vl-10apps-v2/completion.json)。

| 指标 | 新 base | v2 selected adapter |
|---|---:|---:|
| 模型与独立判定共同正确 | 8/40 | 25/40 |
| 正常格共同正确 | 6/30 | 23/30 |
| 主动 finish | 7/40 | 35/40 |
| 主动 finish 且共同正确 | 3/40 | 23/40 |
| 主动 finish 但 blocked/inconclusive | 4/40 | 12/40 |
| 缺陷检出 | 2/10 | 2/10 |
| 正常误报 | 1/30 | 0/30 |
| A/B × 两流程四格全对 | 0/10 | 2/10 |
| target A/B 成对正确 | 1/10 | 2/10 |
| 模型 blocked | 30/40 | 15/40 |
| 独立判定 inconclusive | 30/40 | 5/40 |
| 环境故障 | 0/40 | 0/40 |
| 中位动作数 | 7 | 9 |

结束行为和普通表单任务明显改善。adapter 的 35 次主动结束中，23 次共同正确，12 次最终 blocked；其余 2 次正确结果来自控制器触发的审查，共计 25 次正确。041 昵称编辑从 0/8 改善到 8/8，每格 6 动作，贡献了两组四格全对及两组 target A/B 成对正确。四格与成对指标均按应用和重复编号配对，共 10 组，重复不增加应用数。

base 有 8 次证据字段 guard；adapter 的这类 guard 为 0，另有 5 次 `last_edit_not_submitted`。adapter 的其余 10 次 blocked 来自模型自身审查，说明 guard 次数下降后，审查仍会错失已经完成的有效业务证据。

缺陷检出总数没有增加：base 命中 040 的两次缺陷版 target，adapter 命中 041 的两次缺陷版 target。007 两次缺陷版 target 已正确输入、提交并重开得到旧名称，独立判定均为 fail，但模型审查仍将 requested_value_entered 设为 false 并报告 blocked。040 一次缺陷版 target 同样已有有效提交和持久化差异证据，却未被审查确认；另三次 Mobile target 未完成有效业务检查。普通 Phone 编辑四格则全部正确。

005 的失败机制包含页面状态和金额读取错误。以 [A target r0](../../runs/20261107T182351Z-05f11903/report.md) 为例，step11 已真实正确提交，step12 保存列表显示 `$1,234.57`，独立判定确认其与期望 `$1,234,567.89` 不符。随后模型打开记录，再把原 Save 位置当作输入位置；step17/22/27 的 type 落在 Items 列表 body，页面全选而金额未改变。审查仍声称观察值是 `1234567.89`、与期望一致并想报告 pass，最后由 `last_edit_not_submitted` guard 挡为 blocked。两次 A target 和两次 B target 均受后续编辑问题影响；本应用共触发该 guard 5 次。这些轨迹的初次保存已经完成，不能解释为最初缺少保存。

008 缺陷版 target 在先前提交后看到旧值，又开始下一次编辑；末次提交后未重新打开 edit，最终独立证据未闭合。它与 007、040、005 共同说明，后续改进需要同时处理完成后的页面状态、精确字段/金额读取和审查证据关联。仅增加主动 finish 数量，仍会留下本轮 12 次主动结束但 blocked 的结果。

v1 数据、训练与原评估均保留，见 [v1 结果](../sft-lora-v1/findings.md)。本表使用本轮新跑的 base，完整逐格结果见 [pairs.csv](pairs.csv)，指标、guard 原因及逐 worker 哈希见 [comparison.json](comparison.json)。本次结果对应 v2 数据与两轮训练的整体变化。
