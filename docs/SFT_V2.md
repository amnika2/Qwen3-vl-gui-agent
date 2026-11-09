# SFT v2

v1 数据、LoRA 与五应用结果保留，原文件哈希记录于 `sft-v2-preserved-v1.json`。v2 使用独立数据目录 `datasets/sft-main-10apps-v2` 和训练目录 `runs/sft-qwen3vl-10apps-v2`。

正式 v2 共 **1636 条样本（train1350 / validation286）**：1402 条原始操作、110 条真实终态 operation finish、110 条四图终态审查和 14 条实采未提交审查。审查标签为 66 pass、18 fail、40 blocked；全部派生保留原场景分区并通过核验。

数据补齐四项：专家实际完成状态的单图 operation finish；真实未提交前缀的 blocked review；按推理窗口选取最多四张审查截图；相邻字段取不同值的真实保存/重开场景。所有新增和派生样本只来自十个训练应用，原场景的 train/validation 归属沿用到所有派生样本。

新增字段对照在 `runs/field-contrast-aeebb101`，8 条完整场景，LiteCart 与 Wolf CMS 各 4 条。未提交片段在 `runs/unsubmitted-667db84e` 和 `runs/unsubmitted-58a2cc49`，共 14 条，经过实际浏览器输入与控制器 reload 后采集审查截图。已有记录可见性等人工核对项作为独立注释输入，构建器据此重新生成 v2，原始采集文件保留。

`scripts/build_sft_v2.py` 和 `src/guiqa/sft_v2.py` 生成新版本。每条派生样本记录源轨迹、原始文件哈希、真实历史截止步骤、截图选择及可见证据依据。四图窗口只用真实已有截图；原来较早录入的字段可以由后续同屏截图证明，缺少任务所需输入或结果证明时才标 blocked。

训练配置为 `configs/sft-train-v2.yaml`。从固定 revision 的同一 Qwen3-VL-8B-Instruct 底座重新训练语言层 LoRA r16，视觉与原始权重冻结；BF16、batch1、两卡累积8次、有效batch16，与第一版一致。训练两轮，每轮在训练应用的验证集上计算 loss 并保存 checkpoint，选择 validation loss 最低的 checkpoint 导出 adapter。记录两轮 loss 与选择结果，不使用五个评估应用选模型。

在 402 上执行：

```bash
cd /home/fs_2026/qwen3-gui-agent
CUDA_VISIBLE_DEVICES=0,1 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
  .model-venv/bin/python -m torch.distributed.run --standalone --nproc_per_node=2 \
  -m guiqa.train_sft --config configs/sft-train-v2.yaml
```

评估入口为 `scripts/evaluate_sft_v2.py` 与 `configs/sft-eval-v2.yaml`。在当前修复后的截图环境中重新跑 base 40 格和选中 adapter 40 格，采用相同任务、qwen-controlled、提示词、动作和时间预算。按 variant/case 分片，每个 worker 8 格；只使用 402 GPU0/1 与 404 GPU2/3。base 可以在训练前先运行，adapter 等训练选择完成后再运行。

```bash
CUDA_VISIBLE_DEVICES=2 LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" \
  .model-venv/bin/python scripts/evaluate_sft_v2.py run --variant base --case sweeper-005 --port 13240
.venv/bin/python scripts/evaluate_sft_v2.py report
```

结果位于 `reports/sft-lora-v2`，包括逐格配对、正确判定、检出、误报、A/B 成对正确、主动且正确结束、被 guard 阻止和环境故障。所有已完成失败保留；中断格由同一排程继续。

本轮新鲜 base40 已完成：共同正确8/40、检出2/10、误报1/30、环境故障0/40。两轮训练已完成，共170次参数更新，用时约32.9分钟。第1轮验证loss为0.1532796621，第2轮为0.0785623938，选择`checkpoint-170`并导出到`runs/sft-qwen3vl-10apps-v2/adapter`。选择与底座/数据哈希记录见该目录上一级的`completion.json`；40条adapter对照也已完成，base/adapter共80格，推理核心与任务预算一致，环境故障均为0。

本轮五应用结果：正确判定8/40→25/40，主动结束7/40→35/40，其中主动且正确3/40→23/40；缺陷检出2/10→2/10，正常误报1/30→0/30，target成对正确1/10→2/10。操作与结束改善，金额/字段读取、页面状态识别和审查仍影响缺陷检出。完整报告见 [结果分析](../reports/sft-lora-v2/findings.md)。

v1数据、LoRA及原报告的8个关键文件哈希仍与执行前一致；v2模型训练时的数据哈希与正式发布集一致，见 `sft-v2-preservation-result.json`。
