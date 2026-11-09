# 审查数据扩采完成记录

按已批准的配额完成扩采，没有新增测试应用，也没有启动模型训练。

## 实际数量

新增正常采集完成 820 条真实轨迹，保留 810 条审查样本；另外完成 40 条独立校准。Wolf 重复 Slug 的 10 条等价版本轨迹保留原始记录，按清单不重复计入训练样本。失败、调试和被替代的运行另存，不计入有效数量。

| 分区 | 审查样本 | pass / fail / blocked | 父场景 |
| --- | ---: | --- | ---: |
| 训练，原145＋新增510 | 655 | 315 / 213 / 127 | 347 |
| 新开发集 | 150 | 50 / 50 / 50 | 65 |
| 原开发集 | 39 | 18 / 9 / 12 | 25 |
| 五应用最终测试 | 150 | 74 / 49 / 27 | 100 |

训练审查覆盖原10个训练应用。新开发集覆盖7个应用，原39题继续单列保留。最终测试五应用各30条；两条已执行保存的轨迹缺少明确提交响应证据，按截图证据标为 blocked，保持实际分布。

## 数据产物

- `datasets/sft-review-expanded-v1/train.jsonl`：655条事实审查＋原样回放的145条操作，共800条SFT样本。
- `datasets/sft-review-expanded-v1/validation.jsonl`：150条新开发审查。
- `datasets/sft-review-expanded-v1/legacy-validation.jsonl`：原39题的事实协议版本。旧数据目录保持原字节。
- `datasets/rl-review-expanded-v1/train.jsonl`、`validation.jsonl`：655/150条完整 evidence_v2 审查，供后续RL数据选择与奖励核验使用。
- `datasets/review-final-test-expanded-v1/test.jsonl`：150条事实协议最终测试，与训练和开发分开保存。

SFT和测试包同时保存 `*-evidence-v2.jsonl`，可追溯完整原标注。事实协议的说明由程序生成。

## 来源与派生

原始运行、截图、动作、逐帧可见读值、独立oracle、部署版本、标签及采集源快照均在 `runs/review-expansion-v1`。训练和开发来自七个core批次与两个extended批次；测试来自五个final-test批次，40条校准单独记录。

OpenCart采集中迁移到两个分片的已完成行，仍引用 `core-opencart-main` 中的实际原始运行。该目录是来源依赖，没有重复计数。

18条DokuWiki/FlatPress审查任务按修正后的源函数生成派生稿：为追加、后缀替换或保留Content的任务公开初始基准；DokuWiki审查范围集中于Content持久化，summary/minor作为实际附带动作留在原轨迹。原始任务、金标、图片和动作均保留。逐项原文、派生文、原始与输出SHA及生成源SHA见两个extended批次的 `review-task-derivations.json`。

Akaunting原有CSS和西班牙语翻译文件差异没有改动。两版业务文件Money.php逐字匹配固定commit；实际差异及hash已记录，并复制到最终测试包的 `final-test-source-state.json`。

## 已完成的核对

- 810条新增审查均绑定本次真实轨迹、截图步骤、可见字段、金标和固定应用版本。
- 公开任务、规范化需求、父场景和源运行没有跨训练、新开发、旧开发或最终测试分区交叉。
- 原数据九个文件的SHA保持；145条操作回放记录与原记录逐条相等。
- 事实协议994条审查均可解析；训练答案最长551 tokens，新开发最长545，测试最长260，均在现有1024预算内。
- 训练与测试逐应用CPU输入检查通过，RL入口可正常读取150条新开发题；没有加载模型权重或使用GPU。

构建入口为 `scripts/build_review_expansion.py`。本次完整命令及构建源SHA见 `runs/review-expansion-v1/build-verification.json`，源归档为 `packaging-source.tar.gz`。

总完成状态为 `runs/review-expansion-v1/completion.json`；各数据包有独立 `manifest.json`、`media_manifest.json` 和核验文件。输入检查记录在 `input-verification.json`；三路采集的交接核验分别为 `core-completion-verification.json`、`extended-verification.json`、`final-test-verification.json`。
