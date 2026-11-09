# e2e60-20261109

计划 60 次；已启动 60 次；未启动 0 次。

E2E 成功要求 Agent 与独立判据均符合该任务预期，正确检出缺陷并输出 fail 计为成功。执行完成指独立判据为 pass 或 fail。未启动、blocked 和环境错误保留在计划分母。

## 模型对照

| 组 | E2E 成功 | 执行完成 | 未启动 | 环境错误 | 平均动作 | 平均调用 | 平均耗时(秒) | 输入/输出 tokens |
|---|---|---|---|---|---|---|---|---|
| base-full | 3/10 (30.0%) | 4/10 | 0 | 0 | 25.30 | 41.30 | 230.36 | 1231144/32894 |
| sft-full | 2/10 (20.0%) | 4/10 | 0 | 0 | 9.90 | 18.50 | 140.20 | 527370/16147 |
| grpo-full | 6/10 (60.0%) | 9/10 | 0 | 0 | 12.90 | 21.80 | 153.50 | 626677/17073 |

## Harness 消融

| 组 | E2E 成功 | 执行完成 | 未启动 | 环境错误 | 平均动作 | 平均调用 | 平均耗时(秒) | 输入/输出 tokens |
|---|---|---|---|---|---|---|---|---|
| grpo-full | 6/10 (60.0%) | 9/10 | 0 | 0 | 12.90 | 21.80 | 153.50 | 626677/17073 |
| grpo-no-planning | 3/10 (30.0%) | 5/10 | 0 | 0 | 7.60 | 13.60 | 85.74 | 356493/8332 |
| grpo-no-memory | 2/10 (20.0%) | 3/10 | 0 | 2 | 7.30 | 12.30 | 76.48 | 284426/8243 |
| grpo-no-reflection | 4/10 (40.0%) | 6/10 | 0 | 0 | 9.10 | 16.70 | 113.66 | 464713/12227 |

均值按已启动任务计算；tokens 与生成耗时来自全部模型角色的实际计数。缺失计数在 CSV 中为 0，并由 usage_recorded 标记；以下列出已启动任务的计数覆盖。

- base-full：10/10 次有完整计数。
- sft-full：10/10 次有完整计数。
- grpo-full：10/10 次有完整计数。
- grpo-no-planning：10/10 次有完整计数。
- grpo-no-memory：10/10 次有完整计数。
- grpo-no-reflection：10/10 次有完整计数。

## 配对比较

差值方向为比较组减参考组；消融统一使用完整 GRPO Harness 减去关闭单项机制的组。

| 比较组 | 参考组 | E2E 差值(百分点) | 已观察配对 | 改善 | 退步 | 持平 |
|---|---|---|---|---|---|---|
| sft-full | base-full | -10.0 | 10/10 | 1 | 2 | 7 |
| grpo-full | sft-full | +40.0 | 10/10 | 4 | 0 | 6 |
| grpo-full | base-full | +30.0 | 10/10 | 4 | 1 | 5 |
| grpo-full | grpo-no-planning | +30.0 | 10/10 | 3 | 0 | 7 |
| grpo-full | grpo-no-memory | +40.0 | 10/10 | 5 | 1 | 4 |
| grpo-full | grpo-no-reflection | +20.0 | 10/10 | 3 | 1 | 6 |

## 逐任务差异

仅双方均已启动的配对计算差异；计数缺失时 token 差异留空。

| 比较组 / 参考组 | 任务 | 版本 | E2E Δ | 动作 Δ | 调用 Δ | 耗时 Δ(秒) | 输出 tokens Δ |
|---|---|---|---|---|---|---|---|
| sft-full / base-full | sweeper-005 · target · 0 | A | 0.00 | -10.00 | -13.00 | -30.04 | -1247.00 |
| sft-full / base-full | sweeper-005 · target · 0 | B | 0.00 | -10.00 | -13.00 | -33.91 | -1247.00 |
| sft-full / base-full | sweeper-007 · target · 0 | A | 0.00 | -24.00 | -30.00 | -90.23 | -1703.00 |
| sft-full / base-full | sweeper-007 · target · 0 | B | 0.00 | 7.00 | 12.00 | 83.03 | 956.00 |
| sft-full / base-full | sweeper-008 · target · 0 | A | 0.00 | -22.00 | -35.00 | -137.37 | -2329.00 |
| sft-full / base-full | sweeper-008 · target · 0 | B | 1.00 | -22.00 | -36.00 | -153.77 | -2568.00 |
| sft-full / base-full | sweeper-040 · target · 0 | A | 0.00 | -25.00 | -37.00 | -191.91 | -2876.00 |
| sft-full / base-full | sweeper-040 · target · 0 | B | -1.00 | -25.00 | -40.00 | -231.27 | -3433.00 |
| sft-full / base-full | sweeper-041 · target · 0 | A | -1.00 | -23.00 | -36.00 | -137.57 | -2362.00 |
| sft-full / base-full | sweeper-041 · target · 0 | B | 0.00 | 0.00 | 0.00 | 21.45 | 62.00 |
| grpo-full / sft-full | sweeper-005 · target · 0 | A | 0.00 | 1.00 | -2.00 | -47.97 | -672.00 |
| grpo-full / sft-full | sweeper-005 · target · 0 | B | 1.00 | -6.00 | -12.00 | -103.95 | -1406.00 |
| grpo-full / sft-full | sweeper-007 · target · 0 | A | 1.00 | 2.00 | 2.00 | 13.74 | 166.00 |
| grpo-full / sft-full | sweeper-007 · target · 0 | B | 1.00 | -7.00 | -12.00 | -63.68 | -980.00 |
| grpo-full / sft-full | sweeper-008 · target · 0 | A | 0.00 | 0.00 | 0.00 | -9.68 | -154.00 |
| grpo-full / sft-full | sweeper-008 · target · 0 | B | 0.00 | 0.00 | 0.00 | 10.63 | 130.00 |
| grpo-full / sft-full | sweeper-040 · target · 0 | A | 0.00 | 25.00 | 35.00 | 206.69 | 2496.00 |
| grpo-full / sft-full | sweeper-040 · target · 0 | B | 1.00 | 15.00 | 22.00 | 144.71 | 1696.00 |
| grpo-full / sft-full | sweeper-041 · target · 0 | A | 0.00 | 0.00 | 0.00 | -16.21 | -318.00 |
| grpo-full / sft-full | sweeper-041 · target · 0 | B | 0.00 | 0.00 | 0.00 | -1.29 | -32.00 |
| grpo-full / base-full | sweeper-005 · target · 0 | A | 0.00 | -9.00 | -15.00 | -78.02 | -1919.00 |
| grpo-full / base-full | sweeper-005 · target · 0 | B | 1.00 | -16.00 | -25.00 | -137.86 | -2653.00 |
| grpo-full / base-full | sweeper-007 · target · 0 | A | 1.00 | -22.00 | -28.00 | -76.49 | -1537.00 |
| grpo-full / base-full | sweeper-007 · target · 0 | B | 1.00 | 0.00 | 0.00 | 19.35 | -24.00 |
| grpo-full / base-full | sweeper-008 · target · 0 | A | 0.00 | -22.00 | -35.00 | -147.06 | -2483.00 |
| grpo-full / base-full | sweeper-008 · target · 0 | B | 1.00 | -22.00 | -36.00 | -143.14 | -2438.00 |
| grpo-full / base-full | sweeper-040 · target · 0 | A | 0.00 | 0.00 | -2.00 | 14.77 | -380.00 |
| grpo-full / base-full | sweeper-040 · target · 0 | B | 0.00 | -10.00 | -18.00 | -86.55 | -1737.00 |
| grpo-full / base-full | sweeper-041 · target · 0 | A | -1.00 | -23.00 | -36.00 | -153.77 | -2680.00 |
| grpo-full / base-full | sweeper-041 · target · 0 | B | 0.00 | 0.00 | 0.00 | 20.16 | 30.00 |
| grpo-full / grpo-no-planning | sweeper-005 · target · 0 | A | 0.00 | 14.00 | 20.00 | 160.58 | 2045.00 |
| grpo-full / grpo-no-planning | sweeper-005 · target · 0 | B | 1.00 | 7.00 | 10.00 | 108.17 | 1311.00 |
| grpo-full / grpo-no-planning | sweeper-007 · target · 0 | A | 0.00 | 1.00 | 2.00 | 35.10 | 529.00 |
| grpo-full / grpo-no-planning | sweeper-007 · target · 0 | B | 1.00 | 3.00 | 5.00 | 53.79 | 573.00 |
| grpo-full / grpo-no-planning | sweeper-008 · target · 0 | A | 0.00 | -1.00 | 1.00 | 14.48 | 280.00 |
| grpo-full / grpo-no-planning | sweeper-008 · target · 0 | B | 0.00 | 1.00 | 2.00 | 16.10 | 263.00 |
| grpo-full / grpo-no-planning | sweeper-040 · target · 0 | A | 0.00 | 19.00 | 26.00 | 151.49 | 1963.00 |
| grpo-full / grpo-no-planning | sweeper-040 · target · 0 | B | 1.00 | 9.00 | 13.00 | 93.21 | 1163.00 |
| grpo-full / grpo-no-planning | sweeper-041 · target · 0 | A | 0.00 | 0.00 | 1.00 | 19.56 | 236.00 |
| grpo-full / grpo-no-planning | sweeper-041 · target · 0 | B | 0.00 | 0.00 | 2.00 | 25.15 | 378.00 |
| grpo-full / grpo-no-memory | sweeper-005 · target · 0 | A | 0.00 | 5.00 | 8.00 | 86.11 | 981.00 |
| grpo-full / grpo-no-memory | sweeper-005 · target · 0 | B | 1.00 | -6.00 | -8.00 | -15.74 | -304.00 |
| grpo-full / grpo-no-memory | sweeper-007 · target · 0 | A | 1.00 | 8.00 | 15.00 | 106.33 | 1400.00 |
| grpo-full / grpo-no-memory | sweeper-007 · target · 0 | B | 1.00 | 7.00 | 14.00 | 110.20 | 1321.00 |
| grpo-full / grpo-no-memory | sweeper-008 · target · 0 | A | -1.00 | 1.00 | 3.00 | 12.03 | 15.00 |
| grpo-full / grpo-no-memory | sweeper-008 · target · 0 | B | 1.00 | 0.00 | 1.00 | 31.44 | 302.00 |
| grpo-full / grpo-no-memory | sweeper-040 · target · 0 | A | 0.00 | 25.00 | 35.00 | 216.45 | 2612.00 |
| grpo-full / grpo-no-memory | sweeper-040 · target · 0 | B | 1.00 | 15.00 | 22.00 | 155.06 | 1812.00 |
| grpo-full / grpo-no-memory | sweeper-041 · target · 0 | A | 0.00 | 1.00 | 3.00 | 36.01 | 346.00 |
| grpo-full / grpo-no-memory | sweeper-041 · target · 0 | B | 0.00 | 0.00 | 2.00 | 32.28 | 345.00 |
| grpo-full / grpo-no-reflection | sweeper-005 · target · 0 | A | -1.00 | 7.00 | 9.00 | 70.96 | 893.00 |
| grpo-full / grpo-no-reflection | sweeper-005 · target · 0 | B | 0.00 | 0.00 | 0.00 | 17.62 | 297.00 |
| grpo-full / grpo-no-reflection | sweeper-007 · target · 0 | A | 1.00 | 2.00 | 3.00 | 33.97 | 480.00 |
| grpo-full / grpo-no-reflection | sweeper-007 · target · 0 | B | 0.00 | 2.00 | 2.00 | 34.82 | 377.00 |
| grpo-full / grpo-no-reflection | sweeper-008 · target · 0 | A | 0.00 | -7.00 | -9.00 | -46.37 | -577.00 |
| grpo-full / grpo-no-reflection | sweeper-008 · target · 0 | B | 1.00 | 2.00 | 3.00 | 14.88 | 187.00 |
| grpo-full / grpo-no-reflection | sweeper-040 · target · 0 | A | 0.00 | 17.00 | 21.00 | 113.18 | 1393.00 |
| grpo-full / grpo-no-reflection | sweeper-040 · target · 0 | B | 1.00 | 15.00 | 22.00 | 150.21 | 1702.00 |
| grpo-full / grpo-no-reflection | sweeper-041 · target · 0 | A | 0.00 | 0.00 | -1.00 | -1.51 | -90.00 |
| grpo-full / grpo-no-reflection | sweeper-041 · target · 0 | B | 0.00 | 0.00 | 1.00 | 10.60 | 184.00 |

[逐次结果](results.csv) · [逐任务配对差异](paired-differences.csv) · [结构化汇总](summary.json)
