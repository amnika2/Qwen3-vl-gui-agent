# v2 / v3 五应用评估

本轮在同一 `evidence_v1` 审查协议下比较已导出的 v2、v3 LoRA，各运行40格，共80格。两者均为从同一底座训练两轮、按训练应用验证集选出的第2轮权重；五个应用不参与训练或checkpoint选择。

历史v2旧审查协议的25/40保留在 `reports/sft-lora-v2`。本轮配对结果单独输出 `reports/sft-lora-v3`。

## 协议

公共用例为 Akaunting005、Ampache007、AnchorCMS008、EasyAppointments040、emlog041。每个应用运行 A/B × target/preservation × 两次重复，每版8格。每种权重包含10个预期缺陷格、30个正常格。

共享配置为 `configs/agent-eval-evidence.yaml`，suite为 `configs/sft-eval-v3.yaml`。两版均使用同一底座、`qwen-controlled`、操作384/审查768新token、30动作、600秒、802816像素上限、8条动作历史、seed42。操作提示词、审查提示词、选图、入口解析、独立oracle和运行时全部一致，只改变adapter。

权重SHA256：

- v2：`fa713fac95769e1a5e552eb7f0e3414d321526894f390d18a340875d63dd4faf`
- v3：`11188ed55c92cc62d65c0248cb85b898174a916b1cab3fa3a761b63f67da290c`

每个应用的两版权重在同一主机、同一GPU顺序运行：

| 主机 | GPU | 队列 |
|---|---:|---|
| 402 | 0 | v2-005 → v3-005 |
| 402 | 1 | v2-040 → v3-040 → v2-041 → v3-041 |
| 404 | 2 | v2-007 → v3-007 |
| 404 | 3 | v2-008 → v3-008 |

各worker一次加载模型、执行8格。进程通过SSH与nohup后台启动，状态、PID和日志位于 `runs/sft-lora-v3-workers`。404的首次本地启动未产生队列或运行记录即退出，随后改为SSH启动；没有已完成格被重跑，调度记录见 `reports/sft-lora-v3/launcher-fix.json`。

28个共享推理和任务文件组合SHA256为 `55b58e0692f57f327d35f9a0b04068afe51f5c35124a0b407f1f05fc18498273`。每个worker记录实际core和adapter哈希。评估源码归档位于 `reports/sft-lora-v3/evaluation-source.tar.gz/json`；调度启动修正不改变推理核心。3项针对性评估测试及10-worker只读plan通过。

## 指标

- 正确格：模型、独立oracle、预期三者一致，分母40。
- 缺陷检出：10个缺陷格中模型与oracle均确认fail。
- 正常误报：30个正常格中模型报告fail；同时报告正常正确数及blocked。
- target成对正确：同一应用及重复编号中，缺陷版target正确fail、修复版target正确pass，共10对。
- 四格全对：同一应用及重复编号的四格全部正确，共10组。
- 主动且正确结束：模型主动finish且满足正确格条件，分母40。
- 单列模型blocked、oracle inconclusive、审查格式错误和环境故障，均保留在原分母。

字段读值条数、非null读值和结构化审查数用于描述输出覆盖，不能当作逐字读取准确率。错误原因根据实际截图和轨迹另行分析。两次重复不增加应用样本数。

## 运行记录

启动入口为 `.venv/bin/python scripts/launch_sft_eval_v3.py`，逐worker入口为：

```bash
CUDA_VISIBLE_DEVICES=0 .model-venv/bin/python scripts/evaluate_sft_v3.py run \
  --variant v3 --case sweeper-005 --port 13261
```

全部worker结束后生成报告：

```bash
.venv/bin/python scripts/evaluate_sft_v3.py report
```

当前状态：80格已全部完成，四队列均正常退出，环境故障0。v2/v3正确判定25/40→26/40、缺陷检出3/10→6/10、正常误报2/30→0/30、成对正确2/10→4/10；但blocked6→14、格式拒绝5→8，正常正确22/30→20/30。

v3的独立业务证据40/40符合预期，最终14个blocked中，8个为041/040格式拒绝，6个为007的保存或自动命名创建证据判断问题。详细结论见 [结果分析](../reports/sft-lora-v3/findings.md)，原始逐格与哈希见同目录pairs.csv、comparison.json；完成验收保存在 `docs/sft-v3-eval-completion.json`。全部失败及格式拒绝保留，未按原始pass/fail追改得分。
