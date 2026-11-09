审查 SFT v5 已完成固定两轮训练，训练进程退出码为 0。导出第 2 轮 adapter；训练与数据完整性核验通过。判断能力的比较结果由 [同一 facts-only 协议的事实评估报告](../reports/rl-review-facts-20261108/v4-v5/comparison.md) 汇总。

## 数据与输出

数据为 `datasets/sft-review-v5`，训练 290 条：145 条审查与 145 条原操作回放。145 条审查的 pass/fail/blocked 为 75/33/37；其中原 v4 已用于 SFT 的 105 条为 59/21/25，另 40 条来自既有真实补采轨迹。

回放仅从原训练分区抽取，十个应用各 14 或 15 条，覆盖 92 个场景；整条操作记录保持，包括提示与答案字符串。开发集继续使用原 39 条场景，pass/fail/blocked 为 18/9/12。训练与开发按场景 ID、场景指纹、来源运行隔离，冻结五应用保持留出。

模型输出 `evidence_facts_v1`，保留逐字段的可见读值与引用步骤、输入和结果比较、提交状态、最终记录可见性与 verdict。两处自由说明从模型答案移除，由 `src/guiqa/review_facts.py` 根据结构化事实生成。新系统提示与推理评估共用；用户任务、动作历史、原图及其说明保持。原结构化金标和样本 ID 均保留。

新 40 条训练审查由 Codex 查看全部 68 张去重关键原图并对照取消动作 trace，未发现结构化金标错误。原 `human_pixel_review=pending` 保留，另记录 Codex 核图来源。逐条清单为 [pixel audit](sft-review-v5-pixel-audit.json)，数量和成对案例覆盖见 [数据审计](review-sft-data-audit-20261108.md)。

构建与输入检查：

```bash
.venv/bin/python scripts/build_sft_review_v5.py
.venv/bin/python -m pytest -q tests/test_sft_review_v5.py
```

4 项定向测试通过，覆盖原数据与回放保持、开发金标无损转换、回放分布及场景隔离。CPU 输入检查位于 [input-check.json](../reports/sft-review-v5-preflight/input-check.json)：训练审查答案平均 253.7、最长 545 tokens，开发平均 248.8、最长 450 tokens；多图审查与操作 collator 检查通过。

## 初始化与训练

配置为 [sft-train-review-v5.yaml](../configs/sft-train-review-v5.yaml)。从保留的 `runs/sft-qwen3vl-10apps-v4-review-selected/adapter` 初始化可训练语言 LoRA，新建优化器和学习率调度；底座为固定 revision 的 Qwen3-VL-8B-Instruct，视觉参数冻结。

| 项目 | 配置或实测 |
| --- | --- |
| 主机与 GPU | 402 GPU 0、1 |
| 两卡有效 batch | 16，单卡 batch 1，累积 8 |
| 学习率 / seed | 2e-5 / 42 |
| 轮次与更新 | 固定 2 轮，共 38 次 |
| 可训练参数 / 视觉可训练参数 | 43,646,976 / 0 |
| 最终导出规则 | 固定第 2 轮；未按 39 题的答案选 checkpoint |
| Trainer 训练耗时 | 736.40 秒 |
| 启动至进程退出，北京时间 | 2026-11-08 20:05:29—20:18:17 |
| 训练 loss | 0.03998797 |

| 轮次 | checkpoint | 开发 loss |
| --- | --- | ---: |
| 1 | checkpoint-19 | 0.02874902 |
| 2 | checkpoint-38 | 0.02657093 |

运行目录为 `runs/sft-qwen3vl-review-v5`。初始化与导出权重 SHA256：

```text
v4: d1c1c403d2fed4a5a788fbdfab3f55ae6d70a2a6af98d806ae6dc5422fac72ee
v5: 9112257baf0360a915fac1dd7ef8146adc3794ce93798646a58c58f3eb1cad23
```

实际启动时保留 `training-source.tar.gz/json`。`plan.json` 与 `launch.json` 记录配置、数据、初始 adapter 哈希、主机、GPU、PID 及完整命令；`train.log` 为原始日志；`train-exit.json` 的退出码为 0。完整结果在 [completion.json](../runs/sft-qwen3vl-review-v5/completion.json)，完整性检查在 [completion-verification.json](../runs/sft-qwen3vl-review-v5/completion-verification.json)。检查确认原 v4 权重和数据保持、最终 adapter 哈希一致、固定两轮均保留。

## 持久运行与中断恢复

本次使用以下入口准备和启动，训练 worker 脱离 SSH 会话运行，日志持续写入运行目录：

```bash
.venv/bin/python scripts/launch_sft_review_v5.py prepare
.venv/bin/python scripts/launch_sft_review_v5.py launch
```

每轮 checkpoint 均保留 `optimizer.pt`、`scheduler.pt`、`trainer_state.json` 和两卡的 `rng_state_0/1.pth`。若训练中断，在上个完整 checkpoint 恢复，例如：

```bash
.venv/bin/python scripts/launch_sft_review_v5.py launch \
  --resume-from runs/sft-qwen3vl-review-v5/checkpoint-19
```

恢复入口继续在 402 GPU 0、1 运行，追加原 `train.log`，另存新的 attempt 启动与退出记录。每次恢复加载该 checkpoint 的 adapter、优化器、调度、步数和 RNG，训练总预算仍为两轮。尚未保存的更新从上个 checkpoint 重算。

完成后执行：

```bash
.venv/bin/python scripts/launch_sft_review_v5.py verify
```

该核验已在本次训练结束后通过。39 题事实评估单独保存原始回答、评分明细和统一协议对照，结果见文首链接。
