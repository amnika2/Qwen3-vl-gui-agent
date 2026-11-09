# 007 / 008 同协议评估分析

本文件分析已完成的32条结果：007 Ampache、008 AnchorCMS，各权重各8条。v2与v3使用相同新审查协议，所有失败保留。正确要求agent、独立oracle与预期三者一致；每应用缺陷分母为2，正常分母为6。

| 应用 | 权重 | 正确 | 正常正确 | 缺陷检出 | 正常误报 | blocked | 审查格式拒绝 | oracle fail | oracle inconclusive | 环境故障 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 007 | v2 | 3/8 | 2/6 | 1/2 | 1/6 | 4/8 | 3/8 | 2/8 | 1/8 | 0/8 |
| 007 | v3 | 2/8 | 2/6 | 0/2 | 0/6 | 6/8 | 0/8 | 2/8 | 0/8 | 0/8 |
| 008 | v2 | 6/8 | 6/6 | 0/2 | 0/6 | 0/8 | 0/8 | 0/8 | 2/8 | 0/8 |
| 008 | v3 | 8/8 | 6/6 | 2/2 | 0/6 | 0/8 | 0/8 | 2/8 | 0/8 | 0/8 |

两应用合计正确9/16→10/16、正常正确8/12→8/12、缺陷检出1/4→2/4、正常误报1/12→0/12、格式拒绝3/16→0/16。改善集中在008；007格式遵循改善，但提交结果审查更保守。

## 007：提交结果误读与无输入创建流程

007的v3两次A target均已完成真实正确输入、提交和结果观察，oracle均为fail，但模型将`submission_succeeded`设为false。其审查原始verdict均为fail，最终由既有证据guard降为blocked。因此这里的漏检是提交证据判断失败，既不是null步骤格式拒绝，也不是没有实际提交。

代表轨迹：[20261108T051843Z-0bd659f3](../../runs/20261108T051843Z-0bd659f3/report.md)。实际查看的step10清楚显示输入`Renamed Smart Core4426`；step11已关闭编辑弹窗并返回Smart Playlists列表，旧名称`Rename Me Smart`仍在；step13刷新后同一行仍为旧名称。模型正确抄录新旧名称，却写“Save button at step11 is visible but not confirmed to have been clicked”，与step11实际已关闭弹窗的画面不符。两次guard均为`review_evidence_not_established:submission_succeeded`。

v2也有一次相同类型漏检：[20261108T051347Z-83ec2817](../../runs/20261108T051347Z-83ec2817/report.md)，正确读取输入与旧结果，但`submission_succeeded=false`；另一次A target则正确fail。v3没有增加007的有效缺陷证据总数，两版oracle fail均为2次；变化发生在最终审查。

007的四次v3 preservation均已实际创建新播放列表，oracle均为pass，最终全部blocked。该任务点击“Save as Smart Playlist”后由系统生成以`priya-shah`开头的名字，不要求用户输入Name。没有type动作时，当前选图仅保留审查前/刷新后两张列表截图，较早创建动作和确认页没有单独进入审查图像窗口。

代表轨迹：[20261108T052255Z-c901e41f](../../runs/20261108T052255Z-c901e41f/report.md)。实际查看step6、7均清楚显示新增的`priya-shah - 11/8/26, 5:23 AM`行。模型也识别“name is automatically generated”，并报告原始pass、`submission_succeeded=true`、`final_record_visible=true`；但因没有输入截图，将`input_value/input_step`留null、`requested_value_entered=false`，被guard降为blocked。null与null步骤本身符合schema，问题是把无需手动输入的自动命名流程当作缺少输入证据。其余三次preservation由模型自行blocked，其中还包含创建确认未被选入窗口的问题。

v2三次preservation格式拒绝都引用了不存在的step7，实际审查只提供step3、4。例如[20261108T051558Z-3a7a2156](../../runs/20261108T051558Z-3a7a2156/report.md)生成`observed_step=7`和`evidence_steps=[3,4,7]`。三次均为`review_invalid_output`、oracle pass。v3在这两应用上已消除这一引用问题，但没有把007 preservation转为正确通过。

v2还在正常B target误报一次：[20261108T051627Z-a550bda5](../../runs/20261108T051627Z-a550bda5/report.md)。实际查看step4可见全页面被选中、编辑框仍是`Rename Me Smart`，说明type没有正确替换Name；step5返回列表仍为旧值。模型自身也把`input_value`抄为旧名称，却将`requested_value_entered=true`并报告fail。oracle为`trigger_not_verified`，该条计正常误报，不能视为修复版持久化缺陷。v3的两个B target均正确pass。

## 008：完成重开编辑后取得有效缺陷检出

008两版六个正常格全部共同正确，差异来自A target。v2两次均输入并提交了正确值，最后停在Site Variables列表，oracle为`observation_not_completed`；v3最后重新打开greeting编辑页，oracle两次均确认fail，模型也两次正确fail。

v2代表：[20261108T051347Z-a115deb8](../../runs/20261108T051347Z-a115deb8/report.md)。实际查看step9输入`Hello Quillmere`；step10显示“Your variable was updated”及列表旧值`Hello Anchor`；step11刷新后仍是列表。列表确有可见的值差异，模型的fail读值与这些画面相符，但公开任务要求重新打开greeting编辑页，固定独立观察器未取得该重开编辑证据，故不计有效检出。

v3代表：[20261108T051816Z-6caecdff](../../runs/20261108T051816Z-6caecdff/report.md)。实际查看step9为新输入，step10为更新成功后的列表旧值；step11额外点击并打开greeting编辑页，Value清楚为`Hello Anchor`，step12控制器刷新后仍为该旧值。该额外重开动作完成了任务所要求的观察链路。另一重复[20261108T051912Z-1d961b7e](../../runs/20261108T051912Z-1d961b7e/report.md)同样oracle/agent均fail。

因此，008的提升有实际操作证据支撑；007则说明结构化格式遵循与业务证据理解是两个不同问题。当前剩余问题包括保存后页面状态判断、创建流程的候选截图保留，以及自动生成字段不需要输入时的审查语义。本轮只分析结果，未修改协议、模型、原始记录或训练数据。
