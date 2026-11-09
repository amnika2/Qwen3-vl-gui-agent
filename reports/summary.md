# GUIQA 对照结果

计划 120 次，已完成 120 次，未完成排程 0 次。

qwen-base：有效检出 0/10，正常误报 0/30；qwen-improved：有效检出 0/10，正常误报 1/30。

模型：冻结 Qwen3-VL-8B-Instruct，BF16，server402 两张 RTX 4090。
30 次浏览器动作、600 秒执行预算，每条件 2 次；开发 2 个应用，留出 3 个应用。
固定脚本使用预先编写定位，两个 Qwen 方法使用相同业务用例及截图；改进版增加动作进度文本。

| 数据 | 方法 | 缺陷检出 | 正常误报 | 执行未完成 | 环境故障 | 成对正确 | 中位秒 | 中位动作 | 中位模型调用 | 峰值显存 MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| development | qwen-base | 0/4 | 0/12 | 16/16 | 0/16 | 0/4 | 37.14 | 30.00 | 31.00 | 17201 |
| development | qwen-improved | 0/4 | 1/12 | 15/16 | 0/16 | 0/4 | 39.52 | 30.00 | 31.00 | 17245 |
| development | script | 4/4 | 0/12 | 0/16 | 0/16 | 4/4 | 13.53 | 3.50 | 0.00 | 0 |
| holdout | qwen-base | 0/6 | 0/18 | 11/24 | 2/24 | 0/6 | 34.89 | 30.00 | 31.00 | 17201 |
| holdout | qwen-improved | 0/6 | 0/18 | 12/24 | 0/24 | 0/6 | 32.62 | 30.00 | 31.00 | 17258 |
| holdout | script | 6/6 | 0/18 | 0/24 | 0/24 | 6/6 | 8.27 | 4.00 | 0.00 | 0 |

[逐次数据](results.csv) · [执行异常与判定不一致](failures.csv) · [历史校准和开发试点](development-and-calibration-trials.csv) · [演示](demo.md)

主分析取选定判据版本下的首次排程，模型失败和单次环境故障不从分母删除。未启动排程也保留在 CSV 和分母中。
判据修正前的整批排程按原因单列保留，不挑选单次成功记录替换。
执行未完成指独立判据未取得有效终局证据；Agent 的 blocked 和已有证据却未判定另表列出。
两次重复检查环境波动，不增加应用样本数。

## 按终止状态的耗时

| 数据 | 方法 | 终止原因 | 次数 | 中位秒 |
|---|---|---|---|---|
| development | qwen-base | action_budget | 12 | 42.98 |
| development | qwen-base | invalid_output_twice | 4 | 6.67 |
| development | qwen-improved | action_budget | 11 | 50.14 |
| development | qwen-improved | invalid_output_twice | 2 | 14.21 |
| development | qwen-improved | model_finished | 3 | 9.99 |
| development | script | script_completed | 16 | 13.53 |
| holdout | qwen-base | action_budget | 16 | 39.29 |
| holdout | qwen-base | infrastructure_failure | 2 | 15.70 |
| holdout | qwen-base | invalid_output_twice | 1 | 22.16 |
| holdout | qwen-base | model_finished | 5 | 14.66 |
| holdout | qwen-improved | action_budget | 21 | 33.75 |
| holdout | qwen-improved | model_finished | 3 | 14.07 |
| holdout | script | script_completed | 24 | 8.27 |

## Agent 判定分布

| 数据 | 方法 | pass | fail | blocked | blocked 但已有独立证据 | 已判定但证据不足 |
|---|---|---|---|---|---|---|
| development | qwen-base | 0 | 0 | 16 | 0 | 0 |
| development | qwen-improved | 2 | 1 | 13 | 0 | 2 |
| development | script | 12 | 4 | 0 | 0 | 0 |
| holdout | qwen-base | 5 | 0 | 19 | 6 | 0 |
| holdout | qwen-improved | 3 | 0 | 21 | 9 | 0 |
| holdout | script | 18 | 6 | 0 | 0 | 0 |

## 逐应用结果

| 应用 | 方法 | 缺陷检出 | 正常误报 | 证据不足 | 环境故障 | Agent blocked |
|---|---|---|---|---|---|---|
| sweeper-005 | qwen-base | 0/2 | 0/6 | 8/8 | 0/8 | 8/8 |
| sweeper-005 | qwen-improved | 0/2 | 0/6 | 8/8 | 0/8 | 8/8 |
| sweeper-005 | script | 2/2 | 0/6 | 0/8 | 0/8 | 0/8 |
| sweeper-007 | qwen-base | 0/2 | 0/6 | 4/8 | 0/8 | 6/8 |
| sweeper-007 | qwen-improved | 0/2 | 0/6 | 4/8 | 0/8 | 5/8 |
| sweeper-007 | script | 2/2 | 0/6 | 0/8 | 0/8 | 0/8 |
| sweeper-008 | qwen-base | 0/2 | 0/6 | 3/8 | 2/8 | 5/8 |
| sweeper-008 | qwen-improved | 0/2 | 0/6 | 6/8 | 0/8 | 8/8 |
| sweeper-008 | script | 2/2 | 0/6 | 0/8 | 0/8 | 0/8 |
| sweeper-040 | qwen-base | 0/2 | 0/6 | 4/8 | 0/8 | 8/8 |
| sweeper-040 | qwen-improved | 0/2 | 0/6 | 2/8 | 0/8 | 8/8 |
| sweeper-040 | script | 2/2 | 0/6 | 0/8 | 0/8 | 0/8 |
| sweeper-041 | qwen-base | 0/2 | 0/6 | 8/8 | 0/8 | 8/8 |
| sweeper-041 | qwen-improved | 0/2 | 1/6 | 7/8 | 0/8 | 5/8 |
| sweeper-041 | script | 2/2 | 0/6 | 0/8 | 0/8 | 0/8 |

按应用、版本、workflow 和方法分组的完整计数见 [by-condition.csv](by-condition.csv)。
逐次 CSV 记录模型调用耗时之和；首次模型加载耗时另列，后续运行复用已加载权重。

## 校准与资源

5 个应用的独立参考脚本四格校准均为 8/8 符合预期，详见 [M0 报告](milestone-M0.md)。
前三个应用的源码和预算冻结见 `docs/protocol-freeze.json`；原候选 Ampache 和 Anchor CMS 经脚本校准后加入留出，
推理、动作和进度控制器逐字节一致性检查见 `docs/protocol-extension-freeze.json`，对应源码快照均保留。

## 保留的历史轮次

此前计划 216 次，完成/中断落盘 80 次，未完成排程 136 次。每个计划格子均保留，见 [中断轮次全部排程](aborted-runs.csv)。

| 实验 | 原因 |
|---|---|
| evaluation-866f0006 | NFS 初始化过慢，改用本地临时盘 |
| evaluation-072cb9fa | NFS 初始化过慢，改用本地临时盘 |
| evaluation-075e36a6 | 独立判据被未保存编辑覆盖，修正后重跑 |
| evaluation-e2204e99 | 独立判据被未保存编辑覆盖，修正后重跑 |
| evaluation-616e92c0 | Ampache 详情容器与列表行同 ID 导致判据超时，修正选择器后重跑 |
| evaluation-eaee5537 | Ampache 判据漏认 hash 路由和 AJAX 重开，修正后完整重跑，控制器不变 |
| evaluation-4050024b | Ampache Continue 使用 POST 读取列表，补齐该观察路径后两卡完整重跑，控制器不变 |
