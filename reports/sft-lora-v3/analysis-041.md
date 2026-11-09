# 041：emlog 正常操作因保留字段格式错误失分

两版各完成8格、全部6动作主动结束。v3取得完整独立业务结果8/8，但最终共同正确仅2/8：两个缺陷版target正确fail，其余六个正常任务均已正确保存、重开，Nickname原文也读取正确，却因附加Username保留字段的输入步骤为null而被新格式入口拒绝。

| 指标 | v2 | v3 |
|---|---:|---:|
| 模型、独立判定、预期共同正确 | 7/8 | 2/8 |
| 正常任务共同正确 | 5/6 | 0/6 |
| 缺陷检出 | 2/2 | 2/2 |
| 正常误报 | 0/6 | 0/6 |
| 独立业务结果明确 | 7/8 | 8/8 |
| 独立判定pass / fail / inconclusive | 5 / 2 / 1 | 6 / 2 / 0 |
| 主动结束 | 8/8 | 8/8 |
| 主动且正确结束 | 7/8 | 2/8 |
| 模型blocked | 0/8 | 6/8 |
| 审查格式错误 | 0/8 | 6/8 |
| last_edit_not_submitted | 0/8 | 0/8 |
| 环境故障 | 0/8 | 0/8 |
| target A/B成对正确 | 1/2 | 0/2 |
| 四格全对 | 1/2 | 0/2 |
| 中位动作数 | 6 | 6 |

## 六个正常格丢分的具体位置

| 条件 | v3 run_id | Nickname正确输入与重开值 | 原始Username读值 |
|---|---|---|---|
| A preservation r0 | 20261108T052419Z-ff1673c1 | Board Pen Name | evanRusk |
| A preservation r1 | 20261108T052438Z-db04e82b | Board Pen Name | evanRusk |
| B target r0 | 20261108T052457Z-e897e92f | Saved Pen Name | jon-peck |
| B target r1 | 20261108T052518Z-249c887f | Saved Pen Name | jon-peck |
| B preservation r0 | 20261108T052536Z-5c966305 | Board Pen Name | evanRusk |
| B preservation r1 | 20261108T052554Z-fbaa8624 | Board Pen Name | evanRusk |

六条均使用step3输入候选、step4保存结果、step5重开结果和step6控制器刷新。原始审查均提议pass，Nickname一项为非null输入原文、`input_step=3`、正确的最终原文及`observed_step=5`。错误来自第二项：

```json
{
  "field": "Username",
  "requested_value": "unchanged",
  "input_value": "evanRusk",
  "input_step": null,
  "observed_value": "evanRusk",
  "observed_step": 5
}
```

B target的Username为`jon-peck`，同样给出非null的`input_value`和null的`input_step`。入口约定要求非null原文必须引用实际提供的截图，因此六条统一报 `input_value must cite a supplied screenshot step`，最终为 `review_invalid_output`，agent保持blocked。这里没有重复Nickname reading、没有不可读结果的null-step错误，也没有触发last_edit guard。

已直接查看A preservation r0的[step3](../../runs/20261108T052419Z-ff1673c1/screenshots/003.png)、[step4](../../runs/20261108T052419Z-ff1673c1/screenshots/004.png)、[step5](../../runs/20261108T052419Z-ff1673c1/screenshots/005.png)：Nickname依次正确输入、显示`Saved successfully`、重开后仍为`Board Pen Name`；Username始终为`evanRusk`。独立观察也记录了正确step4提交及step5同一对象的匹配值。

B target r0的[step3](../../runs/20261108T052457Z-e897e92f/screenshots/003.png)和[step5](../../runs/20261108T052457Z-e897e92f/screenshots/005.png)同样证实`Saved Pen Name`正确保留，Username为`jon-peck`。因此这六格的操作与主要业务字段审查已经正确，失分发生在保留字段输入证据的结构表示。原始pass仍未升级为最终pass，正式成绩保留blocked。

## 两个缺陷格正确检出

v3 A target r0为`20261108T052336Z-0a732c61`，r1为`20261108T052402Z-30c9b95e`。两条均在step3正确输入`Saved Pen Name`，step4触发提交，step5重开后Nickname为`Original Pen Name`。原始输出只含一条Nickname reading，引用3/5步骤完整，判fail并通过格式解析。

已查看r0的[step3](../../runs/20261108T052336Z-0a732c61/screenshots/003.png)与[step5](../../runs/20261108T052336Z-0a732c61/screenshots/005.png)，字段原文和不一致判断正确。既有独立判据将本次有效输入触发的特定业务拒绝及重开旧值判为fail，两条最终缺陷检出均保留。

## v2 唯一未计正确的格子

v2 B target r1为`20261108T052245Z-78c2bf0c`。模型报告pass，重开Nickname也为`Saved Pen Name`，但独立证据记录中`response_valid=false`且无有效提交响应事件，最终为`inconclusive / trigger_not_verified`。现有记录不能闭合独立提交证据，因此仍不计正确，没有按重开值或模型结论追改为pass。

本case表明，v3已完成正常业务操作，但在保留字段的value/step配对上丢掉六个本可正确确认的正常格。此处属于输出合同失分；与040中空Mobile被混读为Phone的内容错误应分别解释。

本文件只读分析原始输出、截图及独立观察。未修改代码、权重、任务或运行记录，未重跑，也未将这些评估轨迹导出训练。
