新 SFT v5 上的两步多题 Dr. GRPO 及同协议 39 题开发评估均已完成。本轮没有得到可用的开发收益：原始终判保持 27/39，经过证据一致性 guard 后的正确数从 27 降到 25，结构化核验正确从 9 降到 8。每次更新均使用两道不同题、两个不同应用的完整 G4，实际训练覆盖 **4 道题、3 个应用、2 次更新**。

| 同协议开发指标 | SFT v5 | GRPO v5 |
| --- | ---: | ---: |
| 原始终判正确 | 27/39 | 27/39 |
| guard 后正确 | 27/39 | 25/39 |
| 结构化核验正确 | 9/39 | 8/39 |
| guard 后正常正确 | 14/18 | 13/18 |
| guard 后缺陷正确 | 5/9 | 4/9 |
| guard 后 blocked 正确 | 8/12 | 8/12 |
| 格式错误 / 截断 | 0 / 0 | 0 / 0 |

39 道题的原始终判逐题全部不变，8 条回答的结构化内容发生变化。LiteCart Country 的基准/结果证据与 PHPIPAM Hostname 的输入证据分别新增一次 guard 阻断，没有 guard 恢复。已确认结构化错误为 22→23 条；没有已确认错误但仍待核验的回答两者均为 8 条。开发数据、提示、原图、评分规则、推理源码及设置逐项配对，唯一差异为 adapter。原始回答保存在 `reports/rl-review-facts-20261108/grpo-v5`；[配对开发比较](../reports/rl-review-facts-20261108/v5-grpo/comparison.md)、[评估一致性核验](../reports/rl-review-facts-20261108/grpo-v5/verification.json)已完成，评估进程退出码为 0。冻结五应用保持留出。

训练于 2026-11-08 北京时间 20:24:22 在 402 物理 GPU0 持久启动，20:27:28 结束。训练及收尾用时约 186 秒，峰值显存 21168 MiB。监督进程 PID 为 4054573，训练进程 PID 为 4054574；两者均已退出。[启动、命令及日志](../runs/rl-review-grpo-grouped-v5-ops/launch.json)、[退出状态](../runs/rl-review-grpo-grouped-v5-ops/exit.json)、[完成记录](../runs/rl-review-grpo-grouped-v5/completion.json)均已保存。

初始化模型为 `runs/sft-qwen3vl-review-v5/adapter`，权重 SHA256 为 `9112257baf0360a915fac1dd7ef8146adc3794ce93798646a58c58f3eb1cad23`。模型输出 `evidence_facts_v1` 事实对象，程序根据事实生成说明后，交由原已核验的结构化评分器计算二元奖励。正确事实与结论为 +1，已确认错误为 -1，无法确认或截断保持不可评分。每组四个回答必须全部可评分，且同时包含正、负回答，才有资格参与更新。

训练池沿用原已校准的 32 道训练题，来自 10 个训练应用，标签为 pass 12、fail 10、blocked 10。公开任务、动作记录和原始截图保持；系统提示换为事实协议。训练限定规则来自 `configs/rl-review-reward-rules-retry.yaml`。39 道开发题与冻结五应用均不进入训练。

| 固定配置 | 数值 |
| --- | ---: |
| 最大更新次数 | 2 |
| 每次更新的不同题数 | 2 |
| 每题回答数 | 4 |
| 生成 batch / 梯度累积 | 8 / 8 |
| 每次更新最多扫描题数 | 12 |
| 每题最多采样组数 | 2 |
| 学习率 / beta | 2e-6 / 0.02 |
| 生成预算 / temperature | 1024 tokens / 0.8 |

使用 `dr_grpo`，关闭奖励标准差归一化。采样采用原生 Transformers 随机生成；不生成自由说明。入选 G4 缓存同一版本 actor 刚生成的 prompt 与 completion tokens，父类 TRL 直接消费这些候选，计算参考 logprob 和原 Dr. GRPO loss。若预算内不足两道有效题，程序在优化器更新前停止并记录未完成状态。本次四道题均在第一组采样时有效，因此没有重采样、跳组或预算扩展。

| 更新 | 应用 / 标签 | G4 奖励 | 梯度 L2 |
| --- | --- | --- | ---: |
| 1 | Typesetter / fail | +1, +1, -1, +1 | 0.0648380 |
| 1 | FlatPress / blocked | -1, +1, -1, +1 | 同次更新 |
| 2 | Typesetter / pass | +1, -1, +1, -1 | 0.0641617 |
| 2 | ImpressCMS / fail | -1, +1, +1, -1 | 同次更新 |

实际共采样 4 个 G4、16 个回答，其中正奖 9 条、负奖 7 条，未知或截断为 0。题目标签覆盖 fail 2、blocked 1、pass 1。[逐次采样](../runs/rl-review-grpo-grouped-v5/sampling-attempts.jsonl)、[最终组选择](../runs/rl-review-grpo-grouped-v5/sampling-selections.jsonl)、[实际更新](../runs/rl-review-grpo-grouped-v5/update-steps.jsonl)按题目、应用和标签关联。

负例涉及的具体能力如下；候选编号从 0 开始。

| 组 / 候选 | 已核验的错误 |
| --- | --- |
| 0 / 2 | 缺少提交证据，并给出错误终判。 |
| 1 / 0 | 终判 blocked 正确，但将未建立的读值写成 `{value:null,step:5}`；契约要求整个读值为 `null`。原始 JSON 可以解析，错误在读值表示。 |
| 1 / 2 | 将保持不变的 Subject 写成 edit，使用已明确无效的输入/结果引用，声称存在未建立的提交证据，并给出错误终判。 |
| 2 / 1、3 | 把真实输入 `Hazel Workshop Schedule` 读成 `Hazel Workshop`；终判正确，输入事实错误。 |
| 3 / 0 | 最终字段比较尚未建立，应为 `null`，回答却声称不匹配 `false`；终判正确。 |
| 3 / 3 | 终判错误。 |

这些差异包含读值、阶段引用、字段意图、证据是否建立及终判。7 条负例中只有 3 条终判错误，其余为结构化事实或表示错误。各类错误可以重叠，完整 proof paths 和原始回答见 [能力审计](../runs/rl-review-grpo-grouped-v5/training-audit.json)及 [rollouts](../runs/rl-review-grpo-grouped-v5/rollouts.jsonl)。本次入选组没有混入仍待核验的字段或引用。

两次更新的 actor 均发生变化，梯度有限且非零；参考 adapter 前后哈希相同，视觉参数冻结。缓存生成 tokens、实际训练张量及生成/forward 两条 processor 路径均核对一致。原 v4、v5 的整套 adapter 盘文件及训练绑定源码哈希保持。CPU 预检覆盖完整 32 题，另有 8 项定向测试，其中包括 tiny Qwen3-VL 与真实 TRL 父类的一次两题更新。[完整预检](../runs/rl-review-grouped-v5-config-preflight/verification.json)与 [测试记录](../runs/rl-review-grouped-v5-preflight/test-result.json)已保存。

最终导出为 `runs/rl-review-grpo-grouped-v5/adapter`，权重 SHA256 为 `9d9c1bd1feafc64d8ad946c01bf4282e7a626be528fe152ae9fe3d2bbbd71c8d`。两次完整检查点为 `checkpoint-1`、`checkpoint-2`。独立配置 [agent-review-evidence-grpo-v5.yaml](../configs/agent-review-evidence-grpo-v5.yaml)已通过 `load_config` 检查，默认选用配置继续保留 v4。

本次完成的是多题有效组训练链路与固定小预算试验。后续扩大采集应增加相互独立的场景及易混淆的成对案例，并保留原 39 题作为连续开发对照。两次更新的覆盖说明实际使用了哪些训练对照，算法效果由同协议开发评估及后续更广场景验证。
