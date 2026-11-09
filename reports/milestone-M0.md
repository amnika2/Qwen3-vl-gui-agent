# M0：真实应用校准

| 应用 | case | 缺陷 target | 修复 target | 两版 preservation | 证据 |
|---|---|---|---|---|---|
| akaunting | sweeper-005 | fail 2/2 | pass 2/2 | pass 4/4 | [calibration-928458dc](calibration-928458dc/summary.md) |
| easyappointments | sweeper-040 | fail 2/2 | pass 2/2 | pass 4/4 | [calibration-e3907693](calibration-e3907693/summary.md) |
| anchor-cms | sweeper-008 | fail 2/2 | pass 2/2 | pass 4/4 | [calibration-b28d8479](calibration-b28d8479/summary.md) |
| ampache | sweeper-007 | fail 2/2 | pass 2/2 | pass 4/4 | [calibration-61bcc58a](calibration-61bcc58a/summary.md) |
| emlog | sweeper-041 | fail 2/2 | pass 2/2 | pass 4/4 | [calibration-67017ff4](calibration-67017ff4/summary.md) |

共 40 次独立运行，40/40 符合预期矩阵。每格两次，每次从新数据库与浏览器会话开始。
这是确定性 Playwright 脚本的环境与独立判据校准；模型实验见 [正式报告](summary.md)。
开发：emlog、Akaunting；留出：Easy!Appointments、Ampache、Anchor CMS。
历史部署失败与中断记录保留在其他 calibration-* 批次。
