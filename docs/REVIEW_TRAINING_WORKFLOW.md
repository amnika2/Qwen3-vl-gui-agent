按 2026-11-08 确定的顺序执行：统一输出约束对照、整理审查监督、小规模 SFT、重新验证多题 GRPO。当前系统继续选用 v4，实验权重单独保存。

1. 原 39 题、原提示和图片、greedy、1024 tokens、原验证规则；对 v4 与 retry checkpoint-10 同时启用现有 evidence_v2 schema，两处说明各最多 512 字符。保留全部无约束结果。输出位于 `reports/rl-review-bounded-20261108`。
2. 从 `datasets/rl-review-pilot-v2` 的 145 条训练审查整理监督；39 条开发审查保持原场景分区。新协议 `evidence_facts_v1` 让模型输出读值、阶段、提交状态、引用和结论，由 `src/guiqa/review_facts.py` 从这些结构化字段生成说明。原始数据、图片和标注另存不改。
3. 从已选 v4 adapter 继续 SFT，使用新的优化器；审查训练搭配等量原操作回放，学习率 `2e-5`，固定两轮，最后一轮作为此次实验结果。开发集用于比较，不把错例搬进训练。v4 和新 SFT 均使用同一事实协议重新评估。训练配置为 `configs/sft-train-review-v5.yaml`。
4. 基于新 SFT 重新检验 GRPO。仍使用 `dr_grpo`、不归一化奖励标准差、独立冻结参考。每次优化前积累不同问题的完整有效 G4，保留所有采样及选择原因；无法集齐有效组时在优化前停止，并记录覆盖不足。

冻结的五个应用 `sweeper-005/007/008/040/041` 不参与本轮数据整理、训练、奖励校准或模型选择。原 39 题是训练应用的开发场景。

本轮可用算力为 server402 GPU0/1 与 server404 GPU2/3。运行中的回答即时落盘；SFT 按轮保存检查点，恢复时使用 `--resume-from` 指向本次 SFT 检查点，初始化 v4 adapter 与恢复优化器是两个不同操作。

第二步已完成：145 条训练审查的结构化金标保持；新增 40 条的 68 张去重关键原图经 Codex 核对。145 条原操作回放覆盖十个应用、92 个场景。数据构建的四项测试通过。原图审查清单、覆盖缺口与数量见 `docs/review-sft-data-audit-20261108.md`。

新协议的全部 184 条审查转换检查通过。CPU 输入预检确认训练审查答案平均 253.7 tokens、最长 545 tokens；39 条开发答案平均 248.8 tokens、最长 450 tokens。五图审查和操作回放均可通过训练 collator，结果在 `reports/sft-review-v5-preflight/input-check.json`。

第一步已完成，两种权重均完成 39 题、零截断和零格式错误。v4 终判正确 28/39、证据 guard 后 25/39、原始缺陷终判检出 3/9；checkpoint-10 分别为 29/39、26/39、6/9。实际通过 guard 的缺陷检出分别为 0/9 和 2/9。后者 blocked 原始终判正确数从 8/12 降到 6/12，正常误报均为 1/18。两模型结构化核验正确均为 2/39。四分片完整合并、原始数据与权重保持检查通过，详见 `docs/GRPO_BOUNDED_EVAL.md`。

第三步已完成：北京时间 2026-11-08 20:05:29 在 402 GPU0/1 启动，固定两轮、38 次更新，训练用时 736.4 秒，退出码 0。两轮开发 loss 为 0.0287490、0.0265709；按预定规则导出第二轮。初始化支持已通过真实小型 Qwen3-VL/PEFT 的加载和一次更新测试。训练目录为 `runs/sft-qwen3vl-review-v5`，通过 `scripts/launch_sft_review_v5.py` 启动和恢复。两轮 checkpoint 均保留优化器、调度器和双卡 RNG，`completion-verification.json` 核对通过。

新 SFT adapter SHA256 为 `9112257baf0360a915fac1dd7ef8146adc3794ce93798646a58c58f3eb1cad23`。原 v4 权重与训练数据哈希保持。

事实协议下的 v4 与新 SFT 均已完成 39 题。它们的对照只在事实协议内比较，原 evidence_v2 约束对照另存。

| 指标 | v4 事实协议 | 审查 SFT v5 |
| --- | ---: | ---: |
| 原始终判正确 | 28/39 | 27/39 |
| guard 后正确 | 26/39 | 27/39 |
| guard 后正常正确 | 13/18 | 14/18 |
| guard 后缺陷检出 | 3/9 | 5/9 |
| guard 后 blocked 正确 | 10/12 | 8/12 |
| 结构化核验正确 | 4/39 | 9/39 |
| 原始正常误报 | 0/18 | 1/18 |
| 格式错误 / 截断 | 1 / 0 | 0 / 0 |

此次小 SFT 的改善集中在事实一致性和缺陷检出，总正确数提升一题，blocked 有退步。两模型平均生成约 253–257 tokens。逐题比较和确认错误、待核验分类见 `reports/rl-review-facts-20261108/v4-v5/comparison.md`。训练与恢复记录见 `docs/SFT_REVIEW_V5.md`。

第四步入口及两题同批更新已通过八项测试，其中包含真实 TRL 的小模型更新；32 条真实多图输入的生成与训练预处理 token 路径一致。完整新 adapter、训练题和规则的 CPU 预检通过。

第四步已完成：新的 8B GRPO 于北京时间 2026-11-08 20:24:22 在 402 GPU0 启动，20:27:28 结束，退出码 0。固定两次更新，每次包含两道题的完整有效 G4；四道题首组均有效，共覆盖三应用，16 个回答中 9 正、7 负。两次更新分别为 Typesetter fail + FlatPress blocked，以及 Typesetter pass + ImpressCMS fail；梯度非零、参考冻结、原 v4/v5 盘权重保持。输出 `runs/rl-review-grpo-grouped-v5`，进程、命令及退出记录 `runs/rl-review-grpo-grouped-v5-ops`。

GRPO 最终 adapter 的同源 39 题评估已完成。相对新 SFT v5，39 题原始终判全部不变，正确数仍为 27/39；guard 后正确从 27/39 降为 25/39，结构化核验正确从 9/39 降为 8/39。实际缺陷检出从 5/9 降为 4/9，正常正确从 14/18 降为 13/18，blocked 保持 8/12。两模型均零格式错误和零截断。退步涉及 LiteCart Country 的基准/结果证据，以及 PHPIPAM Hostname 的输入读值。逐题结果见 `reports/rl-review-facts-20261108/v5-grpo/comparison.md`，训练审计见 `docs/GRPO_GROUPED_V5.md`。

本次两步试验未得到可用收益，预算内没有追加更新。默认仍为 v4；新 SFT 和 GRPO 均独立保存，提供各自事实协议配置。所有本轮训练与评估进程均已退出。

后续按用户提出的方向扩充数据与评估。当前 SFT 有 145 条训练审查，GRPO 池有 32 题，本次实际有效更新仅 4 题。39 条开发审查对应 25 个公开任务/规范化需求键，包含相关的版本与阶段变体。只读内容核对未发现现有训练与开发在这两种键上的交叉；旧场景指纹包含命名和分区，扩采需要改用任务内容键去重。核对记录为 `docs/review-data-content-overlap-20261108.json`。扩充方案见 `docs/REVIEW_DATA_EXPANSION.md`；冻结五应用继续用于最终测试，不参与训练或选模。
