# 当前进度

启动日期：2026-11-06，更新日期：2026-11-09（Asia/Shanghai）。项目位于 `/home/fs_2026/qwen3-gui-agent`，原方案保留不变。

2026-11-09 项目完整性补齐：加入任务清单运行、实时状态查询、保留历史尝试的续跑/失败重跑、完整 SFT 配置和按配置的就绪检查；补上结束前计划核对、执行超时反思及中断/预算/错误状态。110 项相关测试通过，四次真实浏览器运行均为 Agent/独立判据 pass，跨任务经验召回已核对。[交付与验收说明](AGENT_DELIVERY.md)

2026-11-09：已加入完整 Agent 运行时，入口为 `configs/agent-full-grpo.yaml`。任务计划、滚动上下文摘要、重复动作反思、跨任务 SQLite 经验及 MCP 工具发现/调用均接入实际执行循环。本地 334 项测试通过；真实两任务验证为编辑 pass、创建 blocked，第二次任务已读取第一次的经验。实现、命令和各项证据见 [Agent 运行说明](AGENT_RUNTIME.md)。

## 已验证事项

- 五个应用均从官方 base/reference 两个提交构建。确定性参考脚本四格、每格两次，40/40 符合预期；提交与实验 ID 见 [案例清单](../cases/index.csv) 和 [M0 报告](../reports/milestone-M0.md)。
- Qwen3-VL-8B-Instruct 权重约 17.5 GB，4 个分片全部通过固定 revision 的官方 LFS SHA256 校验。截图、模型生成、真实浏览器动作、独立判据、JSON/Markdown/JUnit 报告已连通。
- 用户当前指定计算资源为 **server402 GPU 0、1 和 server404 GPU 2、3**。两个 worker 各用一张卡，每次任务独立应用、数据库、Unix socket、缓存和浏览器会话；并发隔离验证见 `reports/concurrency.json`。
- 五个应用正式对照完成 **120/120 次**。固定脚本缺陷检出 10/10；原始 Qwen 与进度记录版均为 **0/10**，正常误报分别 **0/30、1/30**。进度记录未提高有效缺陷检出。
- 2 次基础设施故障保留在正式分母：Anchor CMS 启动读取超时、Chromium 截图协议错误。模型未完成、无证据的 pass/fail 和已取得证据却未结束分别列出。
- Ampache、Anchor CMS 两个原定候选均已纳入留出。Ampache 最后 24 次按版本拆到两卡执行，已完成。推理、动作、进度逻辑、公共任务和预算在历次判据修正中保持一致，见 `docs/protocol-extension-freeze.json`、`docs/controller-extension-history.json`。
- CPU 测试通过 35 项，包含坐标、动作解析、金额、独立判据、未启动排程分母、合成页面、GET/POST 列表观察和启动读取超时回归。源码交接包独立解压后也通过 35 项，见 `docs/tests.log`、`docs/handoff-smoke.log`、`reports/smoke.xml`。

## 证据路径

- 正式汇总：[reports/summary.md](../reports/summary.md)；逐次数据：`reports/results.csv`；逐条件计数：`reports/by-condition.csv`；异常明细：`reports/failures.csv`。
- 演示：[reports/demo.md](../reports/demo.md)，包括 Akaunting 真实缺陷/修复截图，以及 emlog 输入机制的开发轨迹。演示明确记录两个模型试点最终均未完成验证。
- 官方数据固定到 `f10cd684be52322667f1373c8295624e13791b8f`，200 条原始记录位于 `vendor/SWEeper-Bench/test.jsonl`；来源和 SHA256 见 `docs/data-manifest.json`。
- 模型 revision 为 `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`；来源、大小和校验见 `docs/model-manifest.json`、`docs/model-verification.json`。
- 402 环境与依赖：`docs/doctor-model.json`、`docs/model-environment.freeze.txt`、`requirements.lock.txt`。Torch 2.14.1+cu130、Torchvision 0.29.1、Transformers 4.57.6。
- 正式源码快照：`reports/protocol-source.tar.gz`；追加留出快照：`reports/protocol-extension-source.tar.gz`。历史冻结与修正记录保留在对应 round 文件中。
- 交接包：`reports/guiqa-source-and-demo.tar.gz`；校验：`docs/release-manifest.json`。包含源码、配置、汇总、演示截图与原始轨迹。
- 最终核对：`docs/final-verification.json`，120 个条件完整且唯一，两个模型方法除方法开关外配置相同，全部引用截图与报告存在，固定脚本 40/40 符合矩阵。

## 遇到的问题与处理

- Docker socket 无权限，改用项目目录内的 PHP/MariaDB 原生进程；不修改共享服务。
- NFS 上大量小文件使实例初始化过慢。应用、数据库和会话改在 402 本地 `/tmp/guiqa-*` 运行；截图、结构化结果和日志仍保存在共享项目目录。
- Akaunting 需要官方离线缓存与 router，已从固定镜像提取，见 `docs/akaunting-fixture-manifest.json`。端口参数化；离线策略只作用于本次 PHP 进程。无需使用用户提供的 API key。
- Anchor CMS 的 Composer `type: CMS` 不符合新 schema。在独立依赖目录归一化为 `project`，保留原锁文件；应用业务源码不改。使用项目自带 PHP 7.4，启动阶段处理已废弃的 `get_magic_quotes_gpc` 警告，并关闭会访问失效更新地址的 URL fopen；变量读写仍运行原始业务代码。
- 第二轮判据曾被重开后的未保存输入覆盖；现锁定每次提交后的首次有效重开证据，记录见 `docs/evaluator-repair.md`。旧排程与轨迹保留。
- Ampache 追加评测发现详情容器和列表行复用同一 ID，以及 hash 路由与 Continue 的 POST 列表读取未被认可。最终按真实列表响应和可见业务行核对，覆盖 GET/POST 两路径。三个历史批次的完整排程和故障均保留，新批次单列；模型控制器不改。诊断见 `docs/failure-analysis.md`。

## 里程碑与尚未通过事项

- M0：五应用真实环境及四格校准完成；M1：Qwen 真实浏览器闭环完成，Anchor CMS 普通变量创建有完整通过轨迹；M2：120 次对照完成；M3：CLI、JUnit、CPU CI 工作流、独立解压测试和源码演示包已交付。
- 进度记录方法没有提高本组任务的有效缺陷检出；未完成操作、未保存、未重开等失败均保留在分母中。机制分析见 `docs/failure-analysis.md`。
- 循环打断方法 `qwen-controlled`（`--method qwen-controlled`）经两轮开发：循环打断 + 受控终局审查 + 证据门槛与格式恢复。最终 40 次同版代码复测（`evaluation-8db9d71b`、`evaluation-d70aab0e`）取得缺陷检出 2/10（sweeper-040 A target 2/2，Qwen 首次 oracle 确认检出）、正常误报 0/30、中位动作 30 → 6，无 action_budget 与 invalid_output_twice 终止；34/40 仍为执行未完成，剩余瓶颈在操作阶段。中间迭代批次与逐次结论见 `docs/experiment-loop-control.md`。结果只作开发参考，不计入留出成绩。
- 动作效果反馈（输入落空时向模型历史回传纠正指令，仅 type 类动作）已加入 `qwen-controlled`：40 次复测（`evaluation-092189cf`、`evaluation-40fa4011`）指标保持 2/10 检出、0/30 误报，成对正确 1/5；轨迹证据确认 005 的输入落空已消除，剩余 33/40 执行未完成集中于模型不填写表单全部必填字段。click 效果反馈经试验证明会误伤聚焦点击，已移除。结论与批次见 `docs/experiment-action-feedback.md`；环境侧可修项已尽，下一步建议转向 SFT 行为学习。
- SFT 数据管道 P0/P1 已完成：`configs/sft-split.yaml` 固定 5 个评测留出应用与独立训练候选；`src/guiqa/sft_data.py` + `sft_flows.py` 使用推理侧相同的 PROMPT、动作历史、截图约定，`sft` 已接入 CLI 与 run_case。早期 040/041 冒烟样本保留用于格式验证，均不进入训练集。
- SFT 十应用数据已完成：8 个新采应用各 12 个不同场景，加原 PluXml/Typesetter 去重保留的 6 条轨迹，共 **102 条轨迹、1398 条样本（train1164/validation234）**，919 张打包图片，审查 27 fail/75 pass。成品 `datasets/sft-main-10apps-v1` 已通过源哈希、图片、提示词/历史、实际标签与完整场景隔离核验及两名子智能体只读复核。真实业务拒绝使用同源的操作/审查 profile，证据字段保持真实；原五个评测应用默认协议保留。原始数据、失败与隔离批次保留，WonderCMS 辅助数据未混入成品。详情见 [SFT 数据进度](SFT_DATA_STATUS.md)。第一轮 LoRA SFT 已完成。
- 十应用 LoRA SFT 已接通训练、多图 assistant-only loss、checkpoint 恢复与推理 adapter 加载。两卡前 4 步保存并成功恢复，现已完成 1 epoch / 73 步，训练 loss 0.247、验证 loss 0.18925；正式 adapter 在 `runs/sft-qwen3vl-10apps-v1/adapter`，五应用 40 次评估已完成，正确判定 6/40 → 23/40，检出 2/10 → 3/10、误报 0/30 → 1/30，保留 8 次截图故障；[结果分析](../reports/sft-lora-v1/findings.md)。训练记录与执行命令见 [SFT 训练](SFT_TRAINING.md)。
- 本轮复现的导航截图故障已修复：正常截图等待完整页面绘制，实际失败的 14 动作轨迹重放通过，相关采集测试 7 项通过。验证见 `docs/capture-paint-replay.json`，原评估故障与源码快照保留。
- CI 工作流已提供，本地烟测通过；尚未在远端 GitHub Actions 执行。未配置目标仓库，没有提交或推送。

## 可执行命令

```bash
.venv/bin/python scripts/run_402.py --gpu 0 doctor
.venv/bin/python -m guiqa calibrate --case sweeper-005
.venv/bin/python scripts/run_402.py --gpu 0 run --case sweeper-041 --build A --workflow target --method qwen-base --port 13201
.venv/bin/python scripts/build_report.py
```

完整安装与各方法复跑见 [README](../README.md)。本轮两个 GPU worker 已完成并退出，重跑会生成新的实验 ID 与实例。

- SFT v2：1636条样本（train1350/validation286），补110条operation finish、14条真实未提交review及字段对照，四图证据按真实可见性核对。两轮170步已完成，val1=0.15328、val2=0.07856，选第2轮；base与adapter各40格均完成，正确判定8/40→25/40、主动结束7/40→35/40（正确主动结束3→23）、检出2/10保持、误报1/30→0/30，环境故障均为0。详情见 [v2记录](SFT_V2.md)。
- 审查证据组织与读值格式 `evidence_v1` 已接入独立配置：保留发生可见变化的输入候选帧，最多四图带阶段角色，动作日志限定当前证据区间；输出逐字段请求值、输入原文、结果原文及步骤。训练生成端共用格式，已生成5条真实标注预览，原v1/v2保持。实现与验证见 [审查证据说明](REVIEW_EVIDENCE.md)。
- SFT v3：1636条正式数据已生成，训练1350、验证286；124条新格式审查为68pass/26fail/30blocked，1512条操作样本逐条复用v2。21项针对性测试和独立数据验收通过；从同一底座新建LoRA、在402 GPU0/1完成两轮170步训练，验证loss为0.18557、0.11587，选第2轮并导出adapter。v2的8个关键文件保持原字节，见 [v3记录](SFT_V3.md)。
- v2/v3同一新审查协议下的五应用80格评估已完成：正确25/40→26/40，检出3/10→6/10，误报2/30→0/30，环境故障均0；独立业务证据符合预期33/40→40/40，但blocked6→14、格式拒绝5→8。主要剩余损失为041保留Username引用不合格式、040字段混读及格式拒绝、007保存与无输入创建证据判断。见 [评估结果](../reports/sft-lora-v3/findings.md)。
- SFT v4：1642条样本（训练1354、验证288），1512条操作监督保留，130条审查采用evidence_v2，补8条保留字段基准、修正4条Wolf字段引用、新采6条Copy/Cancel对照。两轮170步及全部验证已完成；验证loss为0.16157、0.10000，两轮25条训练应用审查正确11、16，最终选第2轮并独立导出review-selected。原40条轨迹重审正确26→36、blocked14→4、解析错误8→0，缺陷检出仍6/10、误报0/30，原26条正确无退步。同23条训练应用审查为v3旧16/23、v4新14/23，存在2条fail误pass；两个实际UI场景仍未完成有效输入。见 [v4记录](SFT_V4.md)。
