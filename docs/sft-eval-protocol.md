# 五应用 SFT 前后评估

固定 `qwen-controlled`，只改变是否加载 LoRA。5 个应用 × A/B × target/preservation × 2 次重复，每种权重 40 格。复用历史 base 40 格，adapter 新跑 40 格，按应用分成 5 个 worker。每个 worker 只加载一次模型，执行该应用完整的 8 格。

| 项目 | 已核实内容 |
|---|---|
| 应用 | `005、007、008、040、041`，与 `sft-split.yaml` 的冻结五应用一致 |
| base 来源 | `evaluation-092189cf` 的 16 格，加 `evaluation-40fa4011` 的 24 格；没有环境故障 |
| 历史源码标识 | 40 条原始 run manifest 均为 `b5e494ce2b052e202e770344f0c7b2a8badf3c78f45b41e8897ff07e6c9ce8f1` |
| 模型 | 相同 Qwen3-VL-8B-Instruct，revision `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`；adapter 从该 base 加载 |
| 预算 | 相同 30 动作、600 秒、384 新 token、802816 max_pixels、8 条动作历史、seed 42；两次重复沿用相同 seed，解码 `do_sample=False` |
| 任务 | 40 条历史 manifest 的 public 内容与当前五应用相应 workflow 逐项相同 |
| 操作与审查 | 五应用均走默认 `persistence`；相同截图/鼠标/键盘动作空间，保留既有循环控制、type 效果反馈、审查前 reload、证据截图选择及三字段 guard |

默认提示词及控制逻辑另与已保存的最早可用 SFT 源码快照做了实际比较：

`runs/mantis-phpipam-a321a7e5/source-snapshots/4619930d6af316a66ff44a1fa9211fea01373ec9f7fe6a27e34abd63b28e5a33.tar.gz`

该归档文件 SHA256 为 `4042ca639d7c859c725ab5ff9c89808f6983966fc4f320c02315f887ce7ac9b4`。其中 `PROMPT`、`REVIEW_PROMPT` 与当前默认值逐字相同；`LoopControl`、`action_effect_feedback` 的 AST 相同。实际代码 diff 新增了 business profile 分支，五应用继续使用原有 persistence 分支；LoRA 加载只在配置含 `adapter_path` 时启用。

默认 operation prompt SHA256：`e1943b825a133927ae42c7aac0851580bf70debdf59e5e2543b1e286737e504c`。默认 review prompt SHA256：`f912ac0bf2d3de1bacee0012886f2c0b6d74c3c8b04c48d75d61e4068d909f08`。

该 SFT 快照晚于历史评估，不是 `b5e494ce…` 的历史原件；仓库尚未找到该历史整源 hash 对应的归档，因此上述比较证明的是后期已存快照到当前的默认提示与活动分支一致。历史 40 格自身的来源、任务与预算已由其原始 manifest 核实，历史方法说明见 [动作效果反馈记录](experiment-action-feedback.md)。本次保留该覆盖范围，复用完整历史结果。

执行入口为 `configs/sft-eval.yaml` 和 `scripts/evaluate_sft.py`。当前使用用户指定的四张卡，由主进程协调启动：

| 主机 | GPU | case | 端口 |
|---|---|---|---|
| 402 | 0 | sweeper-040 | 13233 |
| 402 | 1 | sweeper-041 | 13234 |
| 404 | 2 | sweeper-005 | 13230 |
| 404 | 3 | sweeper-007 | 13231 |

`sweeper-008` 的 8 格排队复用先结束的 worker，端口 13232。404 GPU0/1 由其他任务使用。

实际运行时，005 在 404 的 A 版 4 格均出现截图故障，保留结果后将 B 版 4 格迁到 402 GPU0 接续；008 随后使用 404 GPU2。五应用最终各完成 8 格。

在对应主机的仓库目录运行，例如第一个 worker：

```bash
CUDA_VISIBLE_DEVICES=0 LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" .model-venv/bin/python scripts/evaluate_sft.py run --case sweeper-040 --port 13233
```

默认 adapter 为 `runs/sft-qwen3vl-10apps-v1/adapter`。每卡独立写 `runs/sft-lora-v1-sweeper-005` 等对应应用目录。同命令续跑会保留已完成结果，包括 blocked、inconclusive、infra_error，只补未开始或 interrupted 的格。原始运行与中断证据都保留。

五个 worker 结束后合并配对报告：

```bash
.venv/bin/python scripts/evaluate_sft.py report
```

输出 `reports/sft-lora-v1/pairs.csv`、`comparison.json`、`summary.md`，列出每格 base/adapter 的独立判定、模型判定、动作数、调用数及终止原因，并汇总缺陷检出、误报、未完成、环境故障和 target A/B 成对正确。成对指标以应用及重复编号配对，共 10 对；重复不增加应用数。

本轮源码归档与内容哈希见 `reports/sft-lora-v1/evaluation-source.{tar.gz,json}`。评估结束后实施的正常截图绘制同步修复，另以 `capture-paint-replay` 的记录动作重放验证；其结果见 `docs/capture-paint-replay.json`，没有改写本轮 40 格。
