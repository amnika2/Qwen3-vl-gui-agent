# 审查证据与字段读值

`evidence_v1` 将审查分为逐字段读取、提交证据确认和结果比较。配置入口为 `configs/agent-review-evidence.yaml`（v2权重）或 `configs/agent-review-evidence-v3.yaml`（v3权重），运行方法为 `qwen-controlled`。原配置继续使用历史审查格式。

## 证据组织

`src/guiqa/review_evidence.py` 根据公开动作历史和截图差异选择最多四张真实截图：

| 角色 | 选取依据 |
|---|---|
| `input_candidate` | 最近发生可见变化的编辑后截图 |
| `input_attempt` | 没有可见变化时，保留最近一次编辑尝试 |
| `post_edit_action` | 所选输入后的候选提交动作；存在实际刷新时保留其前的最后一次点击/Enter，否则保留首个点击/Enter |
| `outcome_candidate` | 进入审查前的页面 |
| `after_reload` / `record_view_candidate` | 实际刷新或打开记录后的页面 |

同一步合并角色，因此最多四张。角色表示候选阶段，保存是否成功仍须根据截图判断。选帧使用 `difference > 0`，操作阶段原有的 `.03` 输入效果反馈阈值保持原值。实际005轨迹的金额输入差异为 `.02083`，必须保留；后续落在列表 body 的输入差异为0。

上下文明确列出实际提供的截图步骤。审查动作日志从最早选帧前两步开始，保留聚焦、全选和当前编辑过程，减少先前编辑轮次的干扰。完整动作日志继续保存在原运行目录。选图不使用 DOM、网络提交数据或私有 oracle。

## 审查输出

每个任务字段先输出可见原文和对应截图步骤，再输出结论：

```json
{
  "action": "finish",
  "field_readings": [{
    "field": "Header",
    "requested_value": "Shoremark Final",
    "input_value": "Shoremark Final",
    "input_step": 10,
    "observed_value": "Shoremark Edited",
    "observed_step": 12
  }],
  "submission_step": 11,
  "requested_value_entered": true,
  "submission_succeeded": true,
  "final_record_visible": true,
  "verdict": "fail",
  "observation": "输入和保存已确认，重开后的标题仍为旧值。",
  "evidence_steps": [10, 11, 12]
}
```

不可读或被裁剪的值及其步骤使用 `null`。明确可见的空字段或空表格可以记录空字符串，并在 observation 中说明。金额原文保留符号、分隔符和小数位，再结合公开任务和界面格式解释。`submission_step` 表示截图中的提交结果证据；一次点击本身不能证明保存成功。

`parse_review` 在模型输出入口验证格式和截图引用。所有读值和提交引用必须来自实际传入的图片。原有证据门槛及 `last_edit_not_submitted` 保留。结果和 Markdown 报告记录逐字段原文、提交步骤和选图角色。

操作阶段仍使用384新token预算，结构化审查单独使用768；动作数30、图片上限802816及历史动作窗口8保持原配置。

## 训练数据生成

训练和推理共用选图、上下文构造和审查提示词。原始采集继续使用 `configs/agent.yaml`；实际读值由独立标注文件提供，再构建新格式：

```bash
.venv/bin/python scripts/build_review_evidence.py \
  --source datasets/sft-main-10apps-v2 \
  --annotations docs/review-evidence-annotations.json \
  --output datasets/sft-review-evidence-preview-final
```

初次链路验证生成5条预览：DokuWiki、Typesetter、LiteCart，训练3条、验证2条；2 pass、2 fail、1 blocked，共14张图片。随后正式v3已完成全部124条审查的逐图标注并合并完整训练集，详见 [v3记录](SFT_V3.md)。场景归属继承 v2，冻结五应用不进入训练数据；构建时确认原v2字节保持。

`SftRecorder.final_review` 也支持由标注者提供完整新答案。旧 teacher 不会自动生成逐字段原文，CLI 原始采集因此仍用旧配置，然后经显式注释 builder 转换。v3已完成两轮新格式SFT训练。

## 验证

58项针对性检查通过，结果保存在 `docs/review-evidence-tests.log`，包含真实 Chromium 保存和重开、后续无效输入保留原证据、引用范围、训练与推理格式一致以及旧格式回归。

实际模型检查使用 v2 adapter，在404 GPU2执行两个训练应用样本，只向模型提供公共任务、动作和截图：

```bash
CUDA_VISIBLE_DEVICES=2 .model-venv/bin/python scripts/verify_review_evidence.py
```

首次检查保存在 `runs/review-evidence-smoke-v2`：DokuWiki通过；Typesetter引用了未提供的早期截图，被入口校验拒绝。随后将日志收窄到当前证据区间，原失败记录保留。最终检查记录于 `runs/review-evidence-smoke-final`：两条新格式均通过解析、最终判定均与标注一致；逐字段原文完全一致为1/2。Typesetter仍把step10输入中的Shoremark Final读成Shoremark Edited，输出已明确暴露这一读值错误。读值匹配仅忽略字段标签大小写，字段值逐字比较。该输入与结果的区分已纳入v3逐字段监督。

上述小规模链路记录使用v2权重。随后已完成v2/v3同一新协议下的五应用80格评估，见 [完整结果](../reports/sft-lora-v3/findings.md)：缺陷检出3/10→6/10、误报2/30→0，但最终正确25/40→26/40，仍受格式拒绝与审查证据理解影响。

新模式单次运行会保存 `agent-config.json`，报告中的复跑命令引用该配置，包含实际 adapter、审查格式和预算。
