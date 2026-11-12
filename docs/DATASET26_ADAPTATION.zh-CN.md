# dataset26 工程适配报告

本轮将 `dataset26-v1` 接入 Actor、Reviewer、独立 Oracle、SFT 数据入口和端到端运行入口。Reviewer 继续采用“读取事实 → 引用证据 → 判断结果”，检查对象按任务要求确定。

Actor 的动作参数由 [actions.py](../src/guiqa/actions.py) 统一定义，解析器、浏览器执行器、工具目录和循环检测共用。新增 `navigate`、`select`、`upload`，支持发送真实键盘事件的 `input_method=keyboard`，滚动范围按现有轨迹扩展为 ±2716。新任务提示和进度提示围绕业务目标取证；查看、搜索、分页等任务不再被要求执行表单保存。

原生选择和上传使用 [dataset26_controls.py](../src/guiqa/dataset26_controls.py) 声明的公开控件工具。控件名称、动作和 CSS selector 同时进入操作样本与运行时公开上下文，上传文件路径也明确提供。Actor 根据截图、动作历史和公开工具执行任务，无需从截图猜测 CSS selector。

[business_review.py](../src/guiqa/business_review.py) 定义统一审查协议。`requirements` 包含公开检查项的 `id/object/expected`；每个输出检查项补充 `observed/evidence_steps/status`，状态为 `pass/fail/unknown`。出现明确违反时总判决为 `fail`，全部检查通过时为 `pass`，其余为 `blocked`。检查项由 [public_requirements](../src/guiqa/dataset26_review_labels.py) 从应用与公开任务生成，模型须覆盖全部检查项并引用实际提供的截图。

| 类别 | Reviewer 与 Oracle 对应检查内容 |
| --- | --- |
| F：表单填写、编辑、保存 | 正确输入、提交结果、同一对象重开后的字段值，以及要求保留的字段。 |
| V：表单校验 | 校验错误是否对应目标字段，合法输入是否接受，非法输入是否正确拒绝，原值是否按要求保留。 |
| O：查看与交互 | 目标对象的值、选项和提示是否正确；按任务决定是否需要提交。 |
| L：删除、移动、排序、状态切换 | 原始目标、实际操作、删除后的存在性、其他对象保留情况、最终顺序或状态。 |
| N：导航、搜索、展示与权限 | 正确控件和目标页面、筛选结果的包含与排除项、权限拒绝及受限内容的可见性。 |
| X：文件处理 | 正确文件与控件、实际上传或导入请求、重开后的文件链接、文档内容或归一化结果。 |
| A：身份验证 | 实际登录请求、账号状态对应的接受或拒绝结果、受保护页面访问结果。 |
| B：复杂业务流程 | 多阶段操作作用于同一业务对象，数量、位置、日期及备注等约束在最终状态成立。 |

[dataset26_oracle.py](../src/guiqa/dataset26_oracle.py) 独立记录实际目标、页面字段和网络请求，依据这些观测判断业务结果，不消费 Reviewer 的检查结果或判决。未完成目标操作或必要观测不足时返回 `inconclusive`，环境故障返回 `infra_error`。文件观测绑定实际文件、控件与请求；兼容浏览器未提供 multipart 文件正文、FileGator 使用 GET 分块探测的实际行为。Grocy 还核对商品、数量、来源位置、目标位置、日期和操作先后关系。

[runner.py](../src/guiqa/runner.py) 已接入新协议的提示、解析、终审与独立 Oracle，并由 [dataset26_execution.py](../src/guiqa/dataset26_execution.py) 创建独立应用实例、准备任务和上传文件。运行产物保留 `condition_id`、复跑命令及连续且唯一的轨迹步号。采集器也已统一使用浏览器执行器；真实浏览器回归覆盖了导航、原生控件、键盘事件及 `DatasetRecorder.step` 的截图记录。

通用入口也已同步：[tasks.py](../src/guiqa/tasks.py) 按冻结清单识别新应用；[evaluate.py](../src/guiqa/evaluate.py) 对新数据默认仅调度 target，按实际可用条件选择 preservation；[doctor.py](../src/guiqa/doctor.py) 检查新包、执行模块和受约束审查依赖。训练入口在加载模型权重前校验应用隔离、A/B 配对、样本配额及审查协议。新 E2E 入口严格读取原 16 个条件，恢复记录绑定数据和实现哈希，每个运行使用独立记忆库；模型判错时进程返回失败状态。

940 条原轨迹生成 940 个统一审查样本和 4700 个操作样本。旧审查按字节保存在每条轨迹的 `private/recorded-review.json`，规范样本可从备份完整重建。原轨迹、原始截图、观测事实和判决均保持不变，标签仍为 **417 fail、523 pass**。001、049、196、055 共 180 条轨迹补充引用已有的输入、提交、原始列表或选中目标截图；074 保留已有多阶段证据。审查最多使用 5 张图片。

| 验证 | 已确认结果与产物 |
| --- | --- |
| 全套回归 | `.rl-venv` 隐藏 CUDA 后 **430 passed**，见 [final-pytest.log](results/dataset26/regression.json)。随后两处文件请求观测窄修完成后，Oracle 专项 **17 passed**。 |
| 数据完整性 | **5640 个样本**、2883 张图片、75 个上传文件通过校验；940 份原审查和原轨迹字节、观测事实及判决均无变化，见 [audit.json](results/dataset26/audit.json) 与 [review-normalization.json](results/dataset26/review-normalization.json)。 |
| Actor 兼容性 | 4700 个操作全部可解析，原有 1200 个不兼容项归零，见 [actor-compatibility.json](results/dataset26/actor-compatibility.json)。 |
| 训练入口 | 全部 5640 样本通过结构与资源检查；真实 processor/collator 验证了 1 图操作和 5 图审查两样本，序列分别为 1156、5149 tokens，损失仅覆盖 assistant JSON 与 EOS。见 [training-compatibility.json](results/dataset26/training-compatibility.json)。 |
| 独立 Oracle 校准 | **26 应用、52 个 A/B 目标条件全部匹配**，结果为 26 fail、26 pass；其中原 16 个独立 E2E 条件的 ID 和任务完全一致。均使用 `script_reference`，`model_calls=0`，见 [dataset26-oracle-coverage.json](results/dataset26/oracle-coverage.json)。 |

上述数据校验对应最终 manifest SHA256：`35bc98196f8b524a811645f8c9564fa4b2f0b0a7f04f63aaa81de750dbf7d293`。Oracle 校准验证脚本执行与判据，不代表模型端到端能力。

命令从仓库根目录执行，使用现有环境：

```bash
# 构建、再生数据
.venv/bin/python scripts/build_dataset26.py

# 全量结构检查与真实 processor/collator smoke
.model-venv/bin/python scripts/verify_dataset26_training.py

# 真实应用脚本执行与独立 Oracle 校准
.venv/bin/python scripts/run_dataset26_e2e.py --method script --output runs/dataset26-v1/e2e-script

# SFT 训练入口
.model-venv/bin/python -m guiqa.train_sft --config configs/sft-train-dataset26-v1.yaml

# 模型端到端评测入口
.model-venv/bin/python scripts/run_dataset26_e2e.py --method qwen-controlled --output runs/dataset26-v1/e2e-model

# 全套回归使用现有 RL 环境，在 CPU 上运行
CUDA_VISIBLE_DEVICES='' .rl-venv/bin/python -m pytest -q
```

`.model-venv` 的 TRL 为 0.13.0，不能运行仓库现有 GRPO 测试所需接口；`.rl-venv` 的 TRL 为 0.27.2，本轮全套回归使用该环境并隐藏 CUDA。两者 Transformers 均为 4.57.6，本轮未升级依赖。

历史 `scripts/export_sft.py` 与现有 RL/GRPO 数据及奖励入口继续服务旧协议。本轮已适配新 SFT 与 E2E 入口；新 SFT 仅以训练集和验证集进入训练流程，测试集用于独立评估。当前尚未开始新数据训练或模型 E2E，旧 RL 协议保留。
