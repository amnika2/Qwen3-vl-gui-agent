# evaluation-1f3f9be9

计划 24 次；已启动 24 次。

| 方法 | 缺陷检出 | 正常误报 | 执行未完成 | 环境故障 | 成对正确 | 中位耗时(秒) |
|---|---|---|---|---|---|---|
| qwen-controlled | 2/6 | 4/18 | 17/24 | 0/24 | 1/6 | 20.76 |

逐次结果、终止原因、动作数及模型调用见 [results.csv](results.csv)。
固定脚本使用预编写定位；Qwen 使用自然语言和截图。重复运行不增加应用样本数。

数据分组：reuse-former-holdout。正式分析批次由 configs/analysis.yaml 指定。
