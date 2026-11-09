# 演示索引

## Akaunting：同一参考脚本的缺陷版和修复版

| 缺陷版 | 修复版 |
|---|---|
| ![截断金额](../runs/20261106T162453Z-228f4963/screenshots/004.png) | ![完整金额](../runs/20261106T162624Z-9cad3d6f/screenshots/004.png) |
| $1,234.57 | $1,234,567.89 |

## emlog：开发轨迹中的输入机制

| 原始 Qwen：追加导致错误输入 | 进度记录：先全选，输入正确 |
|---|---|
| ![追加输入](../runs/20261106T163802Z-a2b71478/screenshots/002.png) | ![完整替换](../runs/20261106T164551Z-0516b110/screenshots/003.png) |

这组开发试点的两个方法最终都未重开验证，均为 inconclusive。截图展示输入阶段的机制变化；最终有效完成率以正式对照表为准。

## Qwen 完整通过正常业务用例

Anchor CMS 原始 Qwen 留出轨迹：创建 hoursnote=Desk hours，保存后检查列表，Agent 与独立判据均为 pass。
11 次浏览器动作、12 次模型调用，14.66 秒。该记录属于正常功能通过，不是缺陷检出。

![模型验证保存后的变量](../runs/20261107T042614Z-6a7449fc/screenshots/011.png)

[完整报告](../runs/20261107T042614Z-6a7449fc/report.md)

Qwen 的逐步动作、原始输出和独立判据分别在每个 run 的 trace.jsonl、model.jsonl、private/oracle.json 中。
