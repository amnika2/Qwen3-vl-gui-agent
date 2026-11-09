# SFT v3

v3 数据集位于 `datasets/sft-main-10apps-v3`。共1636条，训练1350条、验证286条，10个训练应用、110条完整轨迹及14条真实未提交审查。五个冻结评估应用保持留出。

1512条操作与结束监督逐条复用 v2。124条审查已逐图核对并转换为 `evidence_v1`：逐字段记录任务要求、实际输入原文、最终原文及步骤；不可见值为null。训练101条审查为57 pass、21 fail、23 blocked；验证23条为11 pass、5 fail、7 blocked。合计68 pass、26 fail、30 blocked。原v2数据、模型和报告的8个关键哈希保存在 `sft-v3-preserved-v2.json`。

## 数据生成

四份独立注释覆盖全部124条。标注者查看实际选图，不从私有oracle填充字段原文。完整场景沿用 v2 的分区。注释文件为：

- `sft-v3-cms-annotations.json`：DokuWiki、FlatPress、PluXml、Typesetter，共38条。
- `sft-v3-lite-wolf-mantis-annotations.json`：LiteCart、Wolf CMS，共34条。
- `sft-v3-mantis-annotations.json`：MantisBT，共12条。
- `sft-v3-opencart-impress-phpipam-annotations.json`：OpenCart、Impress、phpIPAM，共40条。

复核发现OpenCart有两次保存。共享选帧逻辑因此保留第一次真实刷新之前的最后一个点击/Enter候选，替代仅保留modal Save；没有实际刷新时保持输入后的首个点击/Enter候选。此规则在全部124条中的选图变化仅涉及12条OpenCart，它们新增的product Save帧均实际查看并显示保存成功，恢复7 pass、5 fail。阶段标签仍是候选，真实保存证据由截图建立。

未修改的保留字段只需核对保存结果，不要求新输入截图。明确的业务拒绝返回表单可用其实际保留的字段证明输入，不能把接受保存后重开的旧值当作输入。这两点已写入训练与推理共用的新提示词。证据仍不足的样本保留blocked。

生成命令：

```bash
.venv/bin/python scripts/merge_sft_v3_annotations.py
.venv/bin/python scripts/build_sft_v3.py
```

构建器要求124条审查全部覆盖，替换审查并打包1006张原图；核验1512条操作样本完全一致、源文件字节保持、分区保持、无留出混入及引用真实性。结果见 `datasets/sft-main-10apps-v3/verification.json` 与 `sft-v3-build.log`。21项针对性测试通过，见 `sft-v3-tests.log`。124个审查答案最长564 tokens，当前768-token审查预算可容纳，见 `sft-v3-answer-tokens.json`。

## 训练

配置为 `configs/sft-train-v3.yaml`。从同一 Qwen3-VL-8B-Instruct 底座、固定revision `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b` 新建语言层LoRA；rank16、alpha32、dropout0.05，视觉层冻结。BF16、SDPA、非重入gradient checkpointing及CPU activation offload沿用 v2；每卡batch1、累积8次、两卡有效batch16，学习率1e-4、cosine warmup3%、seed42，不截断样本。

在402的GPU0、1后台执行：

```bash
CUDA_VISIBLE_DEVICES=0,1 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
  bash scripts/train_sft_v3.sh
```

两轮共170次参数更新，每轮计算训练应用验证集loss、保存checkpoint，再选最低验证loss的模型导出。五应用成绩不参与选择。任务已通过nohup脱离终端，PID、实时日志和退出码分别位于 `runs/sft-qwen3vl-10apps-v3/train.pid`、`train.log`、`train-exit.json`。训练源及注释快照保存在该目录的 `training-source.tar.gz/json`。

输出目录为 `runs/sft-qwen3vl-10apps-v3`，最终adapter位于其 `adapter` 子目录。推理配置为 `configs/agent-review-evidence-v3.yaml`，操作预算384新token，结构化审查768新token。

两轮训练已完成，共170次参数更新，用时2003.97秒（约33.4分钟），训练loss为0.23586，进程正常退出。

| 轮次 | checkpoint | 验证loss |
|---|---|---:|
| 1 | checkpoint-85 | 0.185565874 |
| 2 | checkpoint-170 | **0.115866691** |

按训练应用验证集选择第2轮，已导出 `runs/sft-qwen3vl-10apps-v3/adapter`，权重SHA256为 `11188ed55c92cc62d65c0248cb85b898174a916b1cab3fa3a761b63f67da290c`。完整训练、底座、数据哈希和选择结果见该运行目录的 `completion.json`。

完成记录由 `.venv/bin/python scripts/finalize_sft_v3.py` 生成，位于 `docs/sft-v3-completion.json`。最终核对确认训练实际读取的数据与冻结v3一致、导出权重哈希一致、v2的8个关键文件保持原字节，见 `sft-v3-preservation-result.json`。

随后已完成同一新协议下的v2/v3五应用80格评估：正确判定25/40→26/40、缺陷检出3/10→6/10、正常误报2/30→0/30。操作和观察证据闭合33/40→40/40，但审查格式拒绝与证据理解问题仍造成14次v3 blocked。完整分析见 [五应用结果](../reports/sft-lora-v3/findings.md)。
