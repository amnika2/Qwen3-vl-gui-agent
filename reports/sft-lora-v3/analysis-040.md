# 040：Easy!Appointments 字段编辑与审查分析

本次两版各8格，均使用相同 `evidence_v1` 协议。v3 的正确结果由5/8增至6/8，Mobile任务的实际输入、保存和重开全部完成；两次缺陷版运行最终仍因审查格式错误成为blocked。Phone与空Mobile之间的字段混读仍然存在。

| 指标 | v2 | v3 |
|---|---:|---:|
| 模型、独立判定、预期共同正确 | 5/8 | 6/8 |
| 正常任务共同正确 | 5/6 | 6/6 |
| 缺陷检出 | 0/2 | 0/2 |
| 正常误报 | 1/6 | 0/6 |
| Mobile target独立证据闭合 | 1/4 | 4/4 |
| Phone preservation共同正确 | 4/4 | 4/4 |
| 主动结束 | 7/8 | 8/8 |
| 主动且正确结束 | 4/8 | 6/8 |
| 模型blocked | 0/8 | 2/8 |
| 审查格式错误 | 0/8 | 2/8 |
| 独立判定inconclusive | 3/8 | 0/8 |
| 环境故障 | 0/8 | 0/8 |
| last_edit_not_submitted | 0/8 | 0/8 |
| 中位动作数 | 14 | 10.5 |

Mobile独立证据闭合指本次运行真实提交了目标字段、提交被接受，并重新观察到同一对象，因而oracle可以判pass或fail。它衡量执行是否完成，最终缺陷检出仍要求模型和oracle同时正确判fail。两版的target A/B成对正确及四格全对均为0/2。

## v3：有效缺陷证据已经取得，最终输出被格式校验拒绝

两次A target分别为：

- r0：`20261108T051648Z-60fb93eb`
- r1：`20261108T051728Z-aca1ffdb`

两条操作序列相同：step5正确输入Mobile Number `+15551234567`；step6实际点击顶部Save；step7重新打开Jane Doe，页面显示 `Provider saved successfully.`，Mobile字段已为空。step10再次重开同一记录后的Mobile仍为空，Phone Number则保持 `+10000000000`。独立观察记录确认step6的提交值正确且被接受，step7已读取同一对象的空Mobile，最终oracle均为fail。

实际查看的关键截图：

- r0：[输入step5](../../runs/20261108T051648Z-60fb93eb/screenshots/005.png)、[保存结果step7](../../runs/20261108T051648Z-60fb93eb/screenshots/007.png)、[重开step10](../../runs/20261108T051648Z-60fb93eb/screenshots/010.png)、[控制器刷新step11](../../runs/20261108T051648Z-60fb93eb/screenshots/011.png)。
- r1：[重开step10](../../runs/20261108T051728Z-aca1ffdb/screenshots/010.png)。

两条原始审查输出均提议fail，但把同一Mobile拆成两条reading。第一条包含正确的输入值和step5，却输出 `observed_value=null`、`observed_step=7`；这违反不可读值与步骤应同时为null的格式约定，终止原因为 `review_invalid_output`，最终agent判定保持blocked。

第二条reading又把 `+10000000000` 记录成step10的Mobile观察值。截图中该数字属于Phone Number，Mobile框实际为空。因此这里同时存在格式错误和字段读取错误。原始fail没有升级为最终fail，也未计入缺陷检出。

## v2：三条Mobile任务未完成真实提交，却报告fail

对应运行是：

- A target r0：`20261108T051135Z-34514d83`
- A target r1：`20261108T051229Z-79173a57`
- B target r0：`20261108T051422Z-74c978a9`

以A target r0为例，[step5](../../runs/20261108T051135Z-34514d83/screenshots/005.png)及[step15](../../runs/20261108T051135Z-34514d83/screenshots/015.png)均能看到正确输入的Mobile。随后滚到表单底部，step7、17点击的位置未触发实际Save；[step17](../../runs/20261108T051135Z-34514d83/screenshots/017.png)仍是未提交的编辑页。刷新后，[step20](../../runs/20261108T051135Z-34514d83/screenshots/020.png)的Mobile恢复为空。独立观察没有任何提交事件，三条oracle均为 `inconclusive / trigger_not_verified`。

审查仍报告fail，并把Phone旧值当作Mobile结果。A两条无法计缺陷检出，正常版本B target r0因此记为一次误报。B target r1（`20261108T051505Z-bd96aad9`）完成有效提交并正确通过，但由 `action_cycle_period_2` 触发审查，而非模型主动结束。

## 正常编辑和字段读取的改善范围

v3两个B target（`20261108T051853Z-a2f33192`、`20261108T051936Z-5f64a1a8`）均11动作完成。以r0为例，[step5](../../runs/20261108T051853Z-a2f33192/screenshots/005.png)和[step10](../../runs/20261108T051853Z-a2f33192/screenshots/010.png)的Mobile均为 `+15551234567`，Phone仍为 `+10000000000`；审查正确引用Mobile并报告pass。

四条Phone preservation在两版均正确，v3每条10动作，v2每条12动作。本应用的改善体现在更准确的保存操作、修复版Mobile检查完成率和更短轨迹；缺陷版空字段的识别及结构化输出仍阻止最终检出。

本文件只读分析已完成运行。没有修改代码、权重、任务或原始结果，没有重跑，也未将评估轨迹导出训练。
