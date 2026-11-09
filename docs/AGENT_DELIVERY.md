# Agent 使用与交付

项目处理真实 Web 应用中的业务回归任务：读取业务要求，通过截图、鼠标和键盘操作，提交后检查同一记录，输出有截图依据的 `pass / fail / blocked`。应用部署、登录和独立业务判据由案例适配器提供。

## 功能闭环

| 环节 | 实现与检查方式 |
| --- | --- |
| 启动准备 | `doctor --config` 检查所选配置的浏览器、PHP、模型文件、模型依赖、CUDA 和 MCP 配置 |
| 任务输入 | `run` 运行单个案例；`tasks --task-file` 运行命名业务任务清单 |
| 模型切换 | `agent-full-base.yaml`、`agent-full-sft.yaml`、`agent-full-grpo.yaml` 共用完整运行时 |
| 计划与执行 | 任务计划、鼠标键盘操作、截图、工具反馈和周期状态更新 |
| 纠错与反思 | 输入无可见变化、重复动作、执行超时和 MCP 工具错误反馈到后续决策 |
| 结束任务 | 结束提议与当前计划核对；记录最终判定或预算、中断、错误原因 |
| 上下文与经验 | 历史压缩、SQLite 程序经验、MCP 检索和保存 |
| 查看进度 | `status` 读取正在执行的任务结果、计划和下一步目标 |
| 中断与重跑 | 关闭本次应用、浏览器和 MCP；保留结果；清单续跑与失败项重新执行 |
| 输出与交付 | 单次 JSON、Markdown、JUnit、截图与模型输出，以及任务清单汇总报告 |

## 配置业务任务

`configs/tasks-demo.yaml` 包含三个可直接运行的任务。清单中的配置路径以项目根目录为基准：

```yaml
name: 站点变量业务检查
config: configs/agent-full-base.yaml
tasks:
  - id: edit-variable
    case: sweeper-008
    build: B
    workflow: target
  - id: create-variable
    case: sweeper-008
    build: B
    workflow: preservation
```

每项任务包含唯一 `id`、已部署的 `case`、版本 `build: A/B`、流程 `workflow: target/preservation`。任务要求由对应案例的 `public.json` 定义，独立判据核对本次业务操作。案例列表和流程见 [案例说明](../cases/README.md)。新增业务应用时接入案例适配器、公共任务和独立判据，再加入任务清单。

```bash
CUDA_VISIBLE_DEVICES=2 LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" OMP_NUM_THREADS=4 \
  .model-venv/bin/python -m guiqa tasks --task-file configs/tasks-demo.yaml \
  --config configs/agent-full-grpo.yaml --port 13381
```

每个任务创建独立的应用与浏览器会话，模型加载后供后续任务复用。每份清单在自己的运行目录保存经验库；后续任务可以读取之前保存的程序经验。

## 任务进度和中断

启动时输出 `session_dir`，单次任务执行完输出对应 `run_dir`。另一终端可随时查询：

```bash
.venv/bin/python -m guiqa status --limit 5
.venv/bin/python -m guiqa status --run-dir runs/<session_id>
.venv/bin/python -m guiqa status --run-dir runs/<run_id>
```

清单状态包含当前任务、已完成的尝试和报告位置。单次状态包含动作数、Agent 结果、当前计划、历史摘要、反思和下一步目标。

按 Ctrl+C 后，当前任务保存中断状态和报告，并关闭本次应用、浏览器和 MCP 服务。退出码为 `130`。随后使用原清单目录：

```bash
CUDA_VISIBLE_DEVICES=2 LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" OMP_NUM_THREADS=4 \
  .model-venv/bin/python -m guiqa tasks --resume runs/<session_id> --port 13381
```

续跑会跳过已有结果的任务。中断项从新的应用实例开始执行，随后运行尚未开始的任务；原中断现场保留在旧的 `run_dir`。重新执行使用清单已经保存的配置。

要重跑已有结果中未成功的任务，添加 `--retry-failed`。每次尝试保留独立目录，清单汇总展示最新结果及尝试次数。缺陷版任务正确执行验证且 Agent、独立判据均为 `fail`，计为成功；未完成、环境错误和错误终判会进入失败重跑。

## 结果文件

| 位置 | 内容 |
| --- | --- |
| 清单 `tasks.yaml` | 原始任务清单副本 |
| 清单 `session.json` | 实际配置、当前任务与每次尝试的索引 |
| 清单 `memory.sqlite3` | 此清单共享的程序经验 |
| 清单 `report.md` | 每项任务的最新结果和单次报告链接 |
| 单次 `result.json` | Agent、独立判据、终止原因、动作数和耗时 |
| 单次 `task-state.json` | 计划、上下文和终止状态 |
| 单次 `report.md`、`junit.xml` | 可阅读报告与测试工具输出 |
| 单次 `screenshots/`、`trace.jsonl`、`model.jsonl` | 真实操作与原始模型证据 |
| 单次 `tool-calls.jsonl`、`experience.json` | 浏览器/MCP 调用和经验保存记录 |

## 功能验收

2026-11-09 在 server404 GPU 2/3 使用既有 GRPO adapter 完成验收：

| 真实业务任务 | Agent / 独立判据 | 动作数 |
| --- | --- | --- |
| Anchor CMS 编辑 greeting 变量 | pass / pass | 8 |
| Anchor CMS 创建 hoursnote 变量 | pass / pass | 8 |
| emlog 编辑昵称 | pass / pass | 6 |
| 初始计划提示修正后，独立重跑创建变量 | pass / pass | 10 |

首份清单中的创建任务实际召回编辑任务保存的程序经验；后续资料编辑任务也读取了前两次的经验。四次运行均保存了截图、终局判定、任务状态、MCP 调用、经验和报告。初始计划提示修正后的独立运行生成了填写名称、填写值、保存、检查列表四项具体子任务。

本地清单目录为 `runs/tasks-20261109T085650Z-783aaf2e`，独立复验目录为 `runs/tasks-20261109T090114Z-51294618`。汇总记录位于 `reports/agent-delivery/acceptance.json`，测试原始记录位于 `reports/agent-delivery-tests.xml`；完整运行目录由运行命令在本地生成。

CPU、真实 Chromium 与 MCP 测试共 **110 项通过**，覆盖正常执行、工具错误、上下文压缩、未完成计划继续操作、预算退出、中断收尾、任务清单续跑及历史尝试保留。结束前继续操作和中断场景使用确定动作模型配合真实浏览器/MCP 验证；上表四次业务任务使用真实 Qwen3-VL 推理。

```bash
LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" \
  xargs .venv/bin/pytest -q < configs/ci-tests.txt
```

训练权重来自既有 SFT/GRPO 产物。训练与模型评估入口见 [README](../README.md) 和 [训练流程](REVIEW_TRAINING_WORKFLOW.md)。
