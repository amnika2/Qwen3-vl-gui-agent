# SFT v4

v4 训练集位于 `datasets/sft-main-10apps-v4`。已完成两轮LoRA、两轮checkpoint的训练应用审查验证、实际UI验证和五应用原40条轨迹重审，最终选择第2轮。原轨迹重审正确26/40→36/40，原14条blocked恢复10条、原26条正确无退步。训练应用验证仍显示缺陷判断和复杂操作存在问题。

最终模型为 `runs/sft-qwen3vl-10apps-v4-review-selected/adapter`，配置为 `configs/agent-review-evidence-v4-selected.yaml`。原训练自动导出的模型、v3模型、数据及评估记录均保留。

## 审查方法

新协议为 `evidence_v2`。每个字段只输出一条记录，将值和截图步骤合成 `{value, step}` 对象，并区分 `edit`、`preserve`、`generated` 三种意图。可见空框记为 `""`，未见或不可读记为 `null`。保留字段按公开任务给出的基准比较；只要求“保持不变”时，需要可见的原始基准。自动创建和应用生成值通过创建前、提交响应及新记录建立证据。

`submission.status` 独立记录 `accepted`、`rejected` 或 `unknown`，再比较最终字段是否符合任务。证据组织最多保留5张真实截图，涵盖基准、输入、提交响应和重开结果；结束动作可提名实际截图步骤。逐图说明其位于本步动作执行后，并提供公开动作历史。生成时按 JSON schema 限定结构和可引用步骤。

共享实现位于 `src/guiqa/review_contract.py`、`review_context.py`，推理接入位于 `model.py`、`runner.py`。固定权重验证中观察到说明文字重复，已为两处观察文字设置512字符上限；现有人工答案的此类文字最长483字符。实际解码回放确认到达上限后只允许闭合字符串。53项定向测试通过，见 `docs/sft-v4-method-tests.log`。此次调整保持训练样本、提示词及训练核心不变。

未限制说明长度的首轮诊断输出保存在 `reports/sft-v4-protocol/v3-new-unbounded-pilot`。限制长度后的推理源和原因记录保存在 `runs/sft-v4-protocol-workers/bounded-inference-source.tar.gz`、`bounded-inference-fix.json`。

## 数据

| 项目 | 数量 |
|---|---:|
| 全部样本 | 1642 |
| 训练 / 验证 | 1354 / 288 |
| 原样保留的操作与结束监督 | 1512 |
| 新格式审查 | 130 |
| 审查 pass / fail / blocked | 71 / 26 / 33 |

原124条审查沿用 v3 的人工可见读值，并转换为新契约。补看8条保留字段基准：FlatPress的4个Subject和Wolf的4个Title。Wolf原4条Title引用落在Metadata页，现已改为同次真实运行中的Page Title截图，读值仍为 `About us`。这8条审查使用5张证据图。

新增6条OpenCart真实运行审查：3条无输入的Copy创建为pass，3条修改Name后用Back取消、重开仍为旧名的流程为blocked。训练4条、验证2条，场景名称和分区在采集前确定。记录保存在 `runs/sft-v4-curriculum-7ed40cb4`，包含原始截图、动作trace、响应及独立核验结果。

构建核验确认1512条操作样本逐条保持、源文件哈希保持、130条审查均通过契约与证据要求校验、新采样原图与trace哈希对应、训练与验证场景交集为0。审查答案最长762 tokens，p95为643；推理审查预算为1024新token。结果见 `docs/sft-v4-data-audit.json`、`sft-v4-answer-tokens.json` 和数据目录的 `verification.json`。

训练应用仍为 `sweeper-034/053/084/105/107/125/142/150/185/195`。冻结五应用 `sweeper-005/007/008/040/041` 保持留出，不进入训练或checkpoint选择。

## 生成与训练

数据源和生成器为 `docs/sft-v4-annotations.json`、`src/guiqa/sft_v4.py`、`scripts/annotate_sft_v4.py`、`scripts/build_sft_v4.py`。补采源为 `configs/sft-v4-curriculum.yaml`、`scripts/collect_sft_v4_curriculum.py`；该次采集同时保存了配置和采集器快照。

本次发布使用已保存的补采运行构建：

```bash
.venv/bin/python scripts/annotate_sft_v4.py
.venv/bin/python scripts/build_sft_v4.py \
  --curriculum runs/sft-v4-curriculum-7ed40cb4/reviews.jsonl
```

新增补采通过 `.venv/bin/python scripts/collect_sft_v4_curriculum.py` 生成独立运行目录，输出其 `reviews.jsonl` 路径。

构建日志为 `docs/sft-v4-build.log`。原124条转换产物另外保存在 `datasets/sft-main-10apps-v4-pre-curriculum`，固定权重方法验证使用的快照为 `datasets/sft-v4-protocol-validation`。

训练配置为 `configs/sft-train-v4.yaml`。从同一 `Qwen3-VL-8B-Instruct` 底座、固定revision `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b` 新建语言层LoRA，rank16、alpha32、dropout0.05，视觉层冻结。学习率1e-4、两卡有效batch16、seed42、两轮训练。Trainer先按训练应用验证loss导出checkpoint，随后按同一训练应用开发集的实际审查指标复核两轮并确定最终模型。

```bash
CUDA_VISIBLE_DEVICES=0,1 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
  bash scripts/train_sft_v4.sh
```

运行目录为 `runs/sft-qwen3vl-10apps-v4`。两轮共170次更新，用时2096.47秒（34.94分钟），训练loss为0.21907，进程退出码为0。

| 轮次 | checkpoint | 验证loss |
|---|---|---:|
| 1 | checkpoint-85 | 0.1615688503 |
| 2 | checkpoint-170 | **0.1000030860** |

已导出 `runs/sft-qwen3vl-10apps-v4/adapter`，SHA256为 `d1c1c403d2fed4a5a788fbdfab3f55ae6d70a2a6af98d806ae6dc5422fac72ee`。训练数据与完成记录的哈希一致，v3保留清单中的9个文件保持原字节。

训练日志为 `train.log`，完整结果为 `completion.json`、`train-exit.json`，启动信息为 `launch.json`、`train.pid`，训练时源代码快照为 `training-source.tar.gz/json`。后续检查由 `.venv/bin/python scripts/launch_sft_v4_checks.py` 在402 GPU0/1和404 GPU2/3启动，源快照及队列记录位于 `runs/sft-v4-check-workers`。

## 验证

固定v3权重的23条训练应用审查对比已完成：旧协议正确16/23、格式拒绝5次；直接采用新协议正确11/23、格式拒绝3次。新增Copy/Cancel两条验证均未判对。v4第2轮同一23条正确14/23、格式拒绝0次，新增2条均正确。其5条fail验证为3条blocked、2条误pass，缺陷审查仍有明显损失。公开提示词与图片哈希逐条配对，见 [训练应用审查验证](../reports/sft-v4-protocol/summary.md)。

两轮checkpoint均额外完成全部25条训练应用审查开发样本，选模规则在第1轮评估前固定为：正确审查数最多，其次误将非pass判为pass最少，再其次完整验证loss最低。第1轮正确11/25、解析错误2次；第2轮正确16/25、解析错误0次，故最终仍选第2轮。两轮在这5条fail样本上的检出均为0；第1轮将其全判blocked，第2轮误pass2条。选模只读取训练应用开发结果，未读取五应用重审结果。

第1轮开发结果由 `scripts/validate_review_checkpoint_shards.py` 在3张空闲GPU上分片生成，位于 `reports/sft-v4-checkpoint-85-validation/parts/combined`。选择与导出命令为 `.venv/bin/python scripts/select_sft_v4_review_checkpoint.py`，规则、来源哈希和两轮结果保存于 `runs/sft-qwen3vl-10apps-v4-review-selected/selection.json`。最终导出权重与已完成36/40原轨迹重审的权重SHA相同。

实际UI验证使用LiteCart、Wolf各一个验证场景。Wolf首轮遇到自动消失的成功提示导致取证超时，已将提示的可见性和文本合为一次DOM快照读取；原失败保存在 `reports/sft-v4-live/v3-wolf-observer-timeout`，修复和源码记录见 `runs/sft-v4-protocol-workers/wolf-observer-fix.json`。v3修复后和v4的两场景均因有效输入未建立而未通过，独立判定为inconclusive、模型为blocked，全部结果保留于 `reports/sft-v4-live`。

五应用检查复用上一轮v3的40条真实操作轨迹及独立判定，仅重新组织截图并审查。原14条blocked与26条正确均保留，报告统计恢复与退步；这些结果不参与checkpoint选择。四分片汇总由 `.venv/bin/python scripts/summarize_sft_v4_holdout.py` 生成。

| 原轨迹审查指标 | v3 | v4 |
|---|---:|---:|
| 正确判定 | 26/40 | 36/40 |
| 正常结果判对 | 20/30 | 30/30 |
| 缺陷检出 | 6/10 | 6/10 |
| 正常误报 | 0/30 | 0/30 |
| blocked | 14/40 | 4/40 |
| 解析错误 | 8/40 | 0/40 |

恢复的是6条emlog保留Username正常任务和4条Ampache自动创建正常任务。剩余4条为Ampache重命名2条、EasyAppointments Mobile2条，均为缺陷轨迹。Mobile空框原文已正确读成`""`，但保存接受、重开及值比较尚未建立。Ampache仍将保存后旧值解释成提交被拒，并混淆弹窗与返回列表。见 [原轨迹重审](../reports/sft-v4-review-holdout/summary.md) 和 [剩余失败分析](../reports/sft-v4-review-holdout/failures.md)。
