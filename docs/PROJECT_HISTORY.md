# 开发过程与历史结果

当前运行入口见 [项目首页](../README.md)。

# GUIQA

给出业务用例，让 Qwen3-VL-8B-Instruct 通过截图操作浏览器，并用同次运行的独立证据核对结果。

项目路径：`/home/fs_2026/qwen3-gui-agent`。进度、实测结果及剩余工作见 [docs/STATUS.md](STATUS.md)。原始方案文件保留不变。

SFT 十个训练应用的数据已全部生成并核验：**102 条完整轨迹、1398 条样本，train 1164 / validation 234，919 张打包图片**。统一成品为 `datasets/sft-main-10apps-v1`，包含 8 个官方表单应用与 2 个自建表单补充应用；审查标签为 27 fail、75 pass。完整场景隔离、源码与截图来源、操作及审查提示词一致性均已核验。原五个评测应用保持留出，WonderCMS 的排序数据另列。数据、质量记录与重建命令见 [SFT 数据进度](SFT_DATA_STATUS.md)。第一轮 LoRA SFT 已完成，训练 loss 0.247、验证 loss 0.18925，五应用评估已完成，正确且有独立证据的判定为 6/40 → 23/40；见 [SFT 训练](SFT_TRAINING.md) 和 [结果报告](../reports/sft-lora-v1/findings.md)。

五个应用的环境校准 40/40 符合预期，正式对照已完成 120/120 次。原始 Qwen 与进度记录版有效缺陷检出均为 0/10，正常误报分别为 0/30、1/30；进度记录未提高有效缺陷检出。固定脚本检出 10/10。两次环境故障保留在分母，完整数据见 [正式报告](../reports/summary.md)。

v2 已补齐操作终止、真实未提交审查、四图证据对齐及字段对照，形成 **1636 条样本（train1350 / validation286）**；已从同一底座完成两轮训练，按训练应用验证集选中第2轮checkpoint（loss 0.07856）。五应用新基线与 adapter 各40次已完成，正确判定8/40→25/40，缺陷检出均为2/10，见 [v2 记录](SFT_V2.md)。

新审查格式 `evidence_v1` 已接入：保留有效输入候选帧，按阶段标注截图，先逐字段抄读原文再判断，训练生成端复用同一格式。启用配置及5条真实标注预览见 [审查证据说明](REVIEW_EVIDENCE.md)。

v3 已生成完整1636条数据，124条审查全部完成逐图标注并使用新格式；1512条操作与结束样本复用v2，原分区保持。两轮LoRA训练已完成170步，验证loss为0.18557、0.11587，选中第2轮并导出 `runs/sft-qwen3vl-10apps-v3/adapter`；详情见 [v3训练记录](SFT_V3.md)。

v2/v3在同一新审查协议下各40次五应用评估已完成：正确判定25/40→26/40、缺陷检出3/10→6/10、正常误报2/30→0/30，环境故障均为0。v3独立业务证据40/40符合预期，14次blocked集中于格式和审查问题，见 [v3评估分析](../reports/sft-lora-v3/findings.md)。

v4 已完成1642条数据和两轮170步训练；两轮训练应用审查验证正确11/25、16/25，最终选第2轮。模型为 `runs/sft-qwen3vl-10apps-v4-review-selected/adapter`。原40条轨迹重审正确26→36、blocked14→4、解析错误8→0，缺陷检出仍为6/10、正常误报0/30。训练应用验证仍有缺陷误判，两个实际UI场景仍未完成有效操作。见 [v4记录](SFT_V4.md)。

## 带计划、记忆和 MCP 的 Agent

完整运行入口使用 `configs/agent-full-grpo.yaml`，加载最新 GRPO 权重。模型维护任务计划和完成状态，滚动总结移出上下文窗口的历史，反思重复动作，并通过 MCP 检索和保存跨任务的操作经验。浏览器动作与外部 MCP 工具由同一个工具注册表调度。

```bash
.model-venv/bin/pip install -e '.[model,agent]'
CUDA_VISIBLE_DEVICES=2 LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" OMP_NUM_THREADS=4 \
  .model-venv/bin/python -m guiqa run --case sweeper-008 --build B --workflow target \
  --method qwen-controlled --config configs/agent-full-grpo.yaml --port 13381
```

每次运行额外保存 `task-state.json`、`context-events.jsonl`、`tool-catalog.json`、`tool-calls.jsonl` 和 `experience.json`。默认经验库是 `.runtime/agent-memory.sqlite3`；后续任务自动检索相关程序经验。配置、工具接入和两任务联调命令见 [Agent 运行说明](AGENT_RUNTIME.md)。

2026-11-09 本地 334 项测试通过。真实两任务联调中，编辑任务 pass，创建任务 blocked；任务状态、上下文压缩、MCP 检索与保存、跨任务经验读取均已实测。架构核对和业务结果分别见 [运行时验证](../reports/agent-runtime-20261109-verified/runtime-verification.json) 与 [业务任务结果](../reports/agent-runtime-20261109-verified/summary.json)。

## 本机运行

```bash
.venv/bin/python -m guiqa doctor
.venv/bin/python -m guiqa calibrate --case sweeper-041
.venv/bin/python -m guiqa run --case sweeper-041 --build A --workflow target --method script
.venv/bin/python -m guiqa run --case sweeper-040 --build B --workflow preservation --method script
.venv/bin/python -m guiqa report --run-dir runs/<实际运行目录>
.venv/bin/pytest -q
```

单次 `run` 退出码：0 独立证据通过；1 证实产品失败；2 执行未完成/证据不足；3 基础设施故障。`calibrate/evaluate` 检查已知四格矩阵，全部符合预期返回 0，否则返回 4。缺陷版 target 的产品失败是校准预期的一部分。

模型下载及运行：

```bash
.model-venv/bin/python scripts/download_model.py
.venv/bin/python scripts/run_402.py --gpu 0 doctor
.venv/bin/python scripts/run_402.py --gpu 0 run --case sweeper-041 --build A --workflow target --method qwen-base
.venv/bin/python scripts/run_402.py --gpu 0 evaluate --suite configs/dev.yaml --methods script,qwen-base
```

当前已授权 GPU 为 **server402 的物理 0、1，以及 server404 的物理 2、3**。`run_402.py` 通过 SSH 登录 402，并设置项目动态库路径和 GPU 映射；共享的 `/home/fs_2026` 无需复制权重。每个进程使用映射后的 `cuda:0`。第二个并行任务使用 `--gpu 1` 及不同的 `--port`，例如 13201。每次运行拥有独立数据库目录、Unix socket、应用副本、缓存与浏览器会话；固定端口对应 worker，不对应版本。

## 新环境准备

原生运行方式已在 Ubuntu 24.04、Python 3.10、PHP 8.3.6、MariaDB 10.11.14、Chromium 156 上验证。Anchor CMS 单独使用项目目录下的 PHP 7.4.3。

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m playwright install chromium
.venv/bin/python -m guiqa prepare
.venv/bin/python scripts/fetch_sources.py
bash scripts/bootstrap_native.sh
.venv/bin/python scripts/bootstrap_php74.py
.venv/bin/python scripts/fetch_akaunting_fixtures.py
bash scripts/build_apps.sh
.venv/bin/python -m guiqa doctor
.venv/bin/python -m guiqa calibrate --case sweeper-041
```

`bootstrap_native.sh` 只下载并解包 Debian 软件包到 `vendor/runtime/`，不执行系统安装，不需要 Docker。它同时准备本次 Chromium 所需的动态库；无系统依赖安装权限时，用 `LD_LIBRARY_PATH="$PWD/vendor/runtime/root/usr/lib/x86_64-linux-gnu" .venv/bin/python -m guiqa doctor` 检查。`prepare` 会重新生成候选索引，应在首次安装使用；已有运行证据保存在 `runs/`。

模型环境可新建 venv 后安装 `pip install -e '.[model]'`。与当前驱动匹配的组合为 PyTorch 2.14.1、Torchvision 0.29.1、CUDA 13.0（[官方安装命令](https://pytorch.org/get-started/previous-versions/#v2141)）。本机 `.model-venv` 通过 `--system-site-packages` 只读复用 `/home/fs_2026/anaconda3/envs/minimind` 中的 PyTorch 2.14.1+cu130、Transformers 4.57.6，并在项目 venv 中补装 Torchvision；没有改动原环境。实际依赖快照见 `docs/model-environment.freeze.txt`，CPU 环境见 `requirements.lock.txt`。

## 证据和复跑

每次 `run` 都从新实例开始，这就是 `reset`。CLI 自动执行启动、健康检查、运行与关闭；退出时只关闭自己创建的进程。失败目录保留，重跑生成新 ID，不覆盖旧轨迹。

402 上的应用、数据库、会话在本机 `/tmp/guiqa-*` 中运行，退出后清理；截图和日志即时写入共享项目目录。这个选择避免 NFS 上大量实例小文件的初始化开销。

`runs/<id>/` 包含：

- `manifest.json`：模型 revision、预算、视口、配置哈希及 GPU 映射。
- `trace.jsonl`、`model.jsonl`、`screenshots/`：动作、原始模型输出、解析错误、耗时及图像。
- `result.json`、`report.md`、`junit.xml`：Agent 判定和独立判定分别保存。
- `private/`：版本映射、提交事件、独立判据、实例版本及启动日志、应用错误日志。

运行被中断时，已完成步骤立即落盘，`result.json` 保留 `interrupted` 状态。可运行 `report` 重建 Markdown/JUnit，或重新发起一个干净任务。批量排程先落盘，未启动任务保留在分母与 CSV 中；`summarize --run-dir runs/<批次目录>` 可重建汇总。

Agent 只得到公共用例、当前截图和短动作历史。登录属于固定 fixture，模型从已认证业务页面开始。源码、DOM、数据库、版本映射和 oracle 从不加入推理输入。独立判据核对本次真实提交、同一对象、重开后的页面值；猜中缺陷却没有触发/观察证据，仍记为 inconclusive。

## 来源与实现

数据来自 [SWEeper-Bench](https://huggingface.co/datasets/zlab-princeton/SWEeper-Bench)，固定 revision 与原始 SHA256 见 `docs/data-manifest.json`，原文件在 `vendor/SWEeper-Bench/test.jsonl`。数据许可 CC-BY-4.0，保留 SWEeper-Bench 作者署名。各应用保持其上游许可证，提交号见 `cases/index.csv`。

新写的部分包括 Playwright 浏览器适配、六类动作的入口校验、Transformers 本地推理、调度、逐案例独立判据和报告。参考 CUA-Lite 的 Qwen3-VL 归一化坐标及 `computer_use` 协议，采用独立薄适配，没有引入其训练/服务依赖；支持该协议中的单个 `left_click` 调用。动作集合以本项目提示词为准。

Akaunting 复用官方镜像内的离线缓存 fixture 和 PHP router，来源及校验值见 `docs/akaunting-fixture-manifest.json`。router 的端口从原始 13200 参数化；离线 API 策略仅施加到本次 PHP 进程。官方 workflow 实际是创建商品后检查列表价格，按该流程实现。应用业务代码从固定提交复制。两版复用相同锁文件和前端构建产物。

Ampache 复用已构建的 `vendor`、`public/lib` 和 `public/dist`，使用合成管理员与私有智能播放列表。Anchor CMS 在独立目录归一化 Composer 元数据，保留原锁文件，见 `docs/anchor-dependency-manifest.json`。其 PHP 7.4 启动兼容处理与离线更新设置见 [案例说明](../cases/README.md)，应用业务源码不改。所有五例的校准入口都是 `calibrate --case <case_id>`。

`scripts/verify_sources.py` 核对 10 个 checkout 的提交与业务源码、原始数据 SHA256。Akaunting base 的跟踪目录含构建后的 CSS，以及 `.gitattributes` 造成的西班牙语文件行尾差异；这些与业务源码变更分开记录在 `docs/source-verification.json`。

CPU CI 执行解析、坐标、状态判定与合成页面假响应测试；`scripts/check_evidence.py` 额外验证真实 emlog 上的假响应和浏览器故障轨迹。这些结果与真实 Qwen 成绩分别标记。`qwen-improved` 加入动作进度记录：记录全选、输入、最近动作和刷新，依据见 `docs/experiment-progress-ledger.md`。

正式评测及报告重建：

```bash
.venv/bin/python scripts/run_402.py --gpu 0 evaluate --suite configs/dev.yaml --methods script,qwen-base,qwen-improved --port 13201
.venv/bin/python scripts/run_402.py --gpu 1 evaluate --suite configs/holdout.yaml --methods script,qwen-base,qwen-improved --port 13202
.venv/bin/python scripts/run_402.py --gpu 0 evaluate --suite configs/holdout-ampache.yaml --methods script,qwen-base,qwen-improved --port 13201
.venv/bin/python scripts/run_402.py --gpu 1 evaluate --suite configs/holdout-anchor.yaml --methods script,qwen-base,qwen-improved --port 13202
.venv/bin/python scripts/build_report.py
```

以上每张卡的两条批量命令顺序执行。`build_report.py` 按 `configs/analysis.yaml` 中指定的实验 ID 从结构化原始结果生成逐次数据、逐条件计数、汇总、失败表和演示索引。新跑的批次会返回新实验 ID，把它加入该配置后再重建报告。历史中断与判据修正前的排程单独保留，包含未启动的计划格子。每轮独立报告。

Ampache 最后一轮按版本拆分到两张卡，合计仍为 24 次：

```bash
.venv/bin/python scripts/run_402.py --gpu 0 evaluate --suite configs/holdout-ampache-A.yaml --methods script,qwen-base,qwen-improved --port 13201
.venv/bin/python scripts/run_402.py --gpu 1 evaluate --suite configs/holdout-ampache-B.yaml --methods script,qwen-base,qwen-improved --port 13202
```

批量 suite 的 `builds` 可选 A/B 子集，省略时运行两版；结果包含版本字段。全局汇总跨这两个批次计算 target 配对。

交接包生成命令为 `.venv/bin/python scripts/package_release.py`，产物 `reports/guiqa-source-and-demo.tar.gz` 包含源码、配置、汇总和演示轨迹。模型、原始数据、应用依赖按上述固定来源准备。校验信息见 `docs/release-manifest.json`；交接源码与正式运行源码快照分别保存。
