# 带计划、上下文记忆与 MCP 的 GUI Agent

`configs/agent-full-grpo.yaml` 在现有截图操作与终局审查之间加入任务计划、滚动摘要、反思和长期经验。底座及 GRPO 权重沿用已有产物，运行时能力由程序接入。

操作与终局审查使用 GRPO adapter；计划、上下文摘要和经验总结使用同一模型的原始 Instruct 权重，在这些调用期间临时停用 adapter。两种角色共用已加载的底座。

## 一次任务怎样执行

1. 在新应用实例中打开业务页面，通过本地 MCP 经验服务检索相关操作经验。
2. 模型根据任务、截图和经验生成计划，标记 `pending / in_progress / done`。
3. 每一步读取任务、当前截图、近期动作、历史摘要、计划、工具结果和输入进度，选择浏览器动作或 MCP 工具。
4. 默认每三次操作更新计划、摘要和反思。原始历史继续写入轨迹；移出四条近期历史窗口的动作进入摘要。
5. 重复动作触发反思，反思结果进入下一步输入。工具错误作为工具结果返回，模型可以修正参数后继续。
6. 模型结束操作后，复用现有截图证据审查，记录最终结果，再总结程序经验并通过 MCP 保存。

任务状态在 `task-state.json` 中持续落盘。终局审查读取当前运行的截图与动作；独立业务判据在 agent 结束后执行。长期经验总结的输入仅含字段意图、动作类型、按键、执行反馈和观察状态。原任务文本在数据库中用于匹配，检索结果只向模型返回程序经验与来源。

## 运行

在项目根目录执行：

```bash
.model-venv/bin/pip install -e '.[model,agent]'
CUDA_VISIBLE_DEVICES=2 LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" OMP_NUM_THREADS=4 \
  .model-venv/bin/python -m guiqa run --case sweeper-008 --build B --workflow target \
  --method qwen-controlled --config configs/agent-full-grpo.yaml --port 13381
```

`agent_runtime` 配置含义：

| 配置 | 用途 |
| --- | --- |
| `refresh_actions` | 每多少次浏览器操作更新计划与摘要 |
| `planner_adapter` | `base` 使用底座做规划与总结；`actor` 使用当前 adapter |
| `memory_database` | 持久化 SQLite 经验库，相对项目根目录解析 |
| `memory_namespace` | 本轮任务使用的经验空间 |
| `memory_top_k` | 启动时检索的经验条数 |
| `max_tool_calls` | 每次任务中模型主动调用外部工具的预算 |
| `mcp_servers` | 额外连接的 MCP 服务 |

默认经验空间为 `interactive`。新建经验空间可直接更改 `memory_namespace`。固定基准评测继续使用原有无运行时记忆配置；`evaluate` 会提示改用冻结配置，避免前一次任务的经验改变下一次的评测条件。

## MCP 工具

内置 `memory` 服务是独立子进程，使用官方 MCP Python SDK 2.3.0，通过 stdio 执行协议连接、工具发现和调用。它提供：

- `memory__search_experiences`：按任务文本、应用和流程检索历史程序经验。
- `memory__save_experience`：在终局审查后保存本次操作经验。

SQLite 实现和关键词排序位于 `src/guiqa/agent_memory.py`。浏览器原生工具和 MCP 工具的名称、参数 schema、调用者与返回结果都会保存到运行目录。

额外的 stdio 或 Streamable HTTP 服务配置示例：

```yaml
agent_runtime:
  # 其余选项沿用 agent-full-grpo.yaml
  mcp_servers:
    - name: helper
      command: /absolute/path/to/python
      args: [/absolute/path/to/server.py]
    - name: documents
      url: http://127.0.0.1:8000/mcp
```

服务启动后自动发现工具，以 `服务名__工具名` 交给模型。模型调用格式为：

```json
{"action":"tool","name":"helper__lookup_hint","arguments":{"topic":"replace"}}
```

浏览器也支持统一工具格式，例如 `browser__click`，同时继续支持原有 `{"action":"click","coordinate":[180,300]}`。工具参数在进入执行器时校验，调用结果进入下一步模型上下文。

## 运行证据

| 文件 | 内容 |
| --- | --- |
| `task-state.json` | 当前计划、阶段、历史摘要、反思、已召回经验 |
| `context-events.jsonl` | 每次更新的原因、被压缩动作和模型更新 |
| `tool-catalog.json` | 浏览器及 MCP 工具的统一目录 |
| `tool-calls.jsonl` | 工具参数、结果和调用者 |
| `experience.json` | 本次保存的程序经验及来源运行 |
| `model.jsonl` | 操作、上下文更新、经验总结与审查的原始模型输出 |
| `report.md` | 任务结果、计划与记忆摘要、截图和复跑入口 |

## 两任务联调

```bash
CUDA_VISIBLE_DEVICES=2 LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" OMP_NUM_THREADS=4 \
  .model-venv/bin/python -u scripts/validate_agent_runtime.py --port 13381
```

这个入口连续运行 Anchor CMS 修复版的变量编辑和变量创建任务，每次使用新应用实例，共享同一份独立经验库。它核对两次真实任务结果、历史压缩、MCP 检索与保存，以及第二次任务是否召回第一次任务的经验。输出目录包含 `summary.json`、每次任务的位置、配置、经验库和源码快照。

针对运行时的测试入口：

```bash
LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" .venv/bin/pytest -q tests/test_agent_runtime.py
```

测试使用真实浏览器与 MCP stdio 服务，覆盖跨进程经验读取、经验空间隔离、历史截断后的信息保留、模型主动工具调用、外部工具错误和浏览器动作参数校验。

## 2026-11-09 验证结果

本地全套测试为 **334 passed**，记录位于 `reports/agent-runtime-20261109-verified/tests.xml`。真实运行使用 server404 GPU 2，操作与审查加载已训练的 GRPO adapter。

| 任务 | Agent / 独立判据 | 上下文更新 | 历史压缩至 | 召回经验 |
| --- | --- | --- | --- | --- |
| 编辑 greeting 变量 | pass / pass | 4 次 | 步骤 4 | 0 条 |
| 创建 hoursnote 变量 | blocked / inconclusive | 5 次 | 步骤 4 | 上一任务 1 条 |

第二个任务填写了表单，但在确认保存前结束，计划中保留了未完成的保存和检查步骤。两次任务均完成终局审查、任务状态落盘、经验总结及 MCP 保存；第二次启动时经 MCP 实际读取了第一次的经验。

架构核对见 [runtime-verification.json](../reports/agent-runtime-20261109-verified/runtime-verification.json)，原始业务判定见 [summary.json](../reports/agent-runtime-20261109-verified/summary.json)。公开轨迹副本分别是 [编辑任务](../examples/agent-runtime/edit/report.md) 与 [创建任务](../examples/agent-runtime/create/report.md)，[来源记录](../examples/agent-runtime/provenance.json) 保留原始运行位置和文件哈希。本地原始目录还保存了实际配置、SQLite 经验库、依赖版本和源码快照。
