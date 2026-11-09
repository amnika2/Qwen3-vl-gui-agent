# 十应用第一轮 SFT

本轮已完成 1 epoch / 73 步，训练 loss **0.24705**、234 条验证样本 loss **0.18925**。正式 adapter 为 `runs/sft-qwen3vl-10apps-v1/adapter`（约 167 MiB），完成记录为同目录上一级的 `completion.json`。五应用真实 GUI 评估已完成 40 次：正确判定 **6/40 → 23/40**，缺陷检出 **2/10 → 3/10**，正常误报 **0/30 → 1/30**，保留 8 次截图环境故障。逐格数据和分析见 [结果报告](../reports/sft-lora-v1/findings.md)。

训练入口为 `python -m guiqa.train_sft`，配置为 `configs/sft-train.yaml`。数据使用已发布的 `datasets/sft-main-10apps-v1`：1164 条训练样本、234 条验证样本，按完整场景分开。原始发布目录保持不变。

Qwen3-VL-8B-Instruct 使用 BF16，冻结视觉编码器和原始权重，在语言层 attention 和 MLP 投影上训练 rank 16、alpha 32 的 LoRA，共 43,646,976 个参数。两张 4090 各 batch 1，累积 8 次，有效 batch 16；学习率 1e-4、cosine 调度，训练一轮共 73 个优化器步骤。

输入保留原始 system、user 和多图顺序，像素范围与推理一致。全部样本完整输入，最长 10170 tokens / 11 张图。loss 只覆盖 assistant JSON 和 EOS。通过模型原生 `logits_to_keep` 仅计算答案位置所需的词表 logits，其 loss 与完整 masked loss、语言层参数梯度已用真实小尺寸 Qwen3-VL 验证一致。统计见 `sft-tokenization.json`。

本轮评估源码保存在 `reports/sft-lora-v1/evaluation-source.tar.gz`，内容哈希与本轮排程一致。评估完成后，正常截图路径改用 Playwright 等待完整页面绘制，解决导航切换时截图失败的问题；已重放一条实际失败轨迹，14 个动作全部成功截图，独立判据确认缺陷。重放记录为 `docs/capture-paint-replay.json`，采集相关测试为 `docs/sft-capture-tests.log`（7 passed）。本轮 8 条原始截图故障保留在评估结果中。

本轮先执行前 4 步，保存 `checkpoint-4` 的 adapter、optimizer、scheduler 与两卡 RNG，再从它续完同一轮。续训遇到长审查样本的显存不足后，使用 PyTorch `save_on_cpu(pin_memory=True)` 暂存训练激活，从第 4 步恢复并继续，图片和输入长度完整保留。训练日志和最终 adapter 位于 `runs/sft-qwen3vl-10apps-v1`；每 10 步保存断点，保留最近两个。完整训练后，对 234 条验证样本计算一次 loss。

在 402 上运行：

```bash
cd /home/fs_2026/qwen3-gui-agent
CUDA_VISIBLE_DEVICES=0,1 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
  .model-venv/bin/python -m torch.distributed.run --standalone --nproc_per_node=2 \
  -m guiqa.train_sft --config configs/sft-train.yaml
```

断点续训在同一命令末尾加 `--resume-from runs/sft-qwen3vl-10apps-v1/checkpoint-<步数>`，恢复学习率和优化器并跳过已完成的数据。`training_manifest.json` 记录配置、数据 SHA256、依赖版本与可训练参数数目。

五个冻结应用沿用 `qwen-controlled`，A/B × target/preservation × 两次重复，每种权重 40 格。复用已有 40 条 base 结果，新增 adapter 的 40 格。每个应用一个进程，每张卡执行该应用的完整 8 格：

```bash
CUDA_VISIBLE_DEVICES=0 LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" \
  .model-venv/bin/python scripts/evaluate_sft.py run --case sweeper-005 --port 13230
.venv/bin/python scripts/evaluate_sft.py report
```

其余四个进程分别指定 `sweeper-007/008/040/041`，使用独立端口与空闲 GPU，分配见 [评估协议](sft-eval-protocol.md)。再次执行同一条命令会续跑剩余格。五个进程结束后执行 `report`，汇总保存在 `reports/sft-lora-v1/`，每应用排程在 `runs/sft-lora-v1-sweeper-<编号>/`，原始轨迹路径记录于其 `completed.json`。

训练实现参考 [Qwen3-VL 官方微调入口](https://github.com/QwenLM/Qwen3-VL/blob/main/qwen-vl-finetune/qwenvl/train/train_qwen.py)，使用项目现有的 Transformers/PEFT 实现，保留发布数据的消息格式。
