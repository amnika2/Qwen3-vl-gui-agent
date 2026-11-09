# 005 金额任务分析

本应用两种权重各8格、合计16格均已完成。两种权重使用同一 `evidence_v1` 协议与推理核心 `55b58e0692f57f327d35f9a0b04068afe51f5c35124a0b407f1f05fc18498273`。目标流程要求输入 `1.234.567,89`、Purchase Price `100`，保存后检查列表是否显示完整金额 `$1,234,567.89`；保留流程使用正常金额 `1.234,56`。

## 完整16格结果

| 指标 | v2 | v3 |
|---|---:|---:|
| 正确判定 | 4/8 | 8/8 |
| 正常任务正确 | 4/6 | 6/6 |
| 缺陷检出 | 0/2 | 2/2 |
| 正常误报 | 0/6 | 0/6 |
| 主动finish | 5/8 | 8/8 |
| 主动且正确结束 | 4/8 | 8/8 |
| target A/B成对正确 | 0/2 | 2/2 |
| 四格全对 | 0/2 | 2/2 |
| 模型blocked | 2/8 | 0/8 |
| 审查格式错误 | 2/8 | 0/8 |
| guard降级 | 0/8 | 0/8 |
| 独立判定inconclusive / 环境故障 | 0 / 0 | 0 / 0 |
| 中位动作数 | 18 | 15.5 |

四格类别的正确数分别为：A target `0/2→2/2`、A preservation `2/2→2/2`、B target `1/2→2/2`、B preservation `1/2→2/2`。因此增加的4个正确结果由2个真实缺陷检出和2个正常任务恢复构成。

v2剩余两条错误均为结构化引用超出实际供图范围：

- B target repeat1，`20261108T051616Z-783d2e48`：只提供10、11、29、30图，输出却为Purchase Price引用26、Sale Price引用21，解析拒绝，最终blocked。原始输出还把保存值 `1234567.89` 与输入格式 `1.234.567,89` 直接视为不同而提出fail，未正确处理等值金额格式；该提议未成为最终fail，故不计正常误报。
- B preservation repeat0，`20261108T051722Z-5d7140c8`：只提供10、13、17、18图，Sale Price.input_step却引用8，解析拒绝，最终blocked。其原始观察提出pass且金额比较正确。

v3的两条B target均在12动作后完成pass，其余正常流程也全部pass；这与A target两次fail共同构成两组真正的A/B区分。

## A target 配对结果

| 权重 | repeat | run_id | Agent / oracle | 动作数 | 结束方式 | 审查选图 |
|---|---:|---|---|---:|---|---|
| v2 | 0 | `20261108T051135Z-d6ab7aaf` | pass / fail | 18 | 动作循环触发 | 10、11、17、18 |
| v2 | 1 | `20261108T051241Z-dac14776` | pass / fail | 18 | 动作循环触发 | 10、11、17、18 |
| v3 | 0 | `20261108T051859Z-f666a4c6` | fail / fail | 16 | 主动finish | 10、12、15、16 |
| v3 | 1 | `20261108T052032Z-81d22411` | fail / fail | 16 | 主动finish | 10、12、15、16 |

四条均没有环境故障、审查格式错误或 guard 降级。A target 的缺陷检出为 v2 0/2、v3 2/2。每种权重的两个重复使用相同动作序列和审查内容；对应输入、保存、重开和末态关键截图的 SHA256 逐帧一致。

## v2：输入与保存正确，最终把实际小金额报告为完整金额

两条 v2 运行的 step7 输入Sale Price `1.234.567,89`，step10 输入Purchase Price `100`。实际查看的 [step10表单](../../runs/20261108T051135Z-d6ab7aaf/screenshots/010.png) 同时显示完整商品名、输入金额和进价。

step11 点击真正Save。其 [保存结果](../../runs/20261108T051135Z-d6ab7aaf/screenshots/011.png) 显示 `Item created!`，同一商品行的售价为 `$1,234.57`、进价为 `$100.00`。因此，本次已经完成正确输入与真实提交，并产生了与期望完整金额不同的保存结果。

step12 打开商品编辑页，Sale Price显示 `1234.57`。随后模型执行Home、ArrowRight、连续三次Backspace；查看 [step17](../../runs/20261108T051135Z-d6ab7aaf/screenshots/017.png) 与 [step18刷新后](../../runs/20261108T051135Z-d6ab7aaf/screenshots/018.png)，金额均仍为 `1234.57`，没有后续type或保存。审查由 `action_cycle_period_1` 触发。

本轮后续操作是未改变字段的按键尝试，和历史旧协议中向列表body反复type的轨迹不同。新选图仍保留step10真实输入与step11保存结果，本次错误不能归因于这些关键帧被挤掉。

最终结构化输出仍与截图不符：

- `Sale Price.input_value = "1.234.567,89"` 引用step11，但step11已经是保存后的列表。
- `Sale Price.observed_value = "1.234.567,89"` 引用step18，实际编辑框显示 `1234.57`。
- observation把期望与观察都写成 `1234567.89`，给出pass。

这些步骤号均属于实际提供的图片集合，因此格式校验通过；三项证据布尔值也都由模型设为true，没有触发guard。这里暴露的是字段原文和阶段关联错误，最终造成真实缺陷的错误通过。

## v3：主动识别金额不一致，结构化原文仍有标点误差

两条 v3 运行同样在step7输入Sale Price、step10输入Purchase Price，并在 [step11真正Save](../../runs/20261108T051859Z-f666a4c6/screenshots/011.png) 后得到 `$1,234.57`。 [step10输入表单](../../runs/20261108T051859Z-f666a4c6/screenshots/010.png) 显示三项请求值均正确。

step12 打开同一商品编辑页，Sale Price为 `1234.57`；step13刷新，step14等待，step15点击Items返回已保存列表。 [step15](../../runs/20261108T051859Z-f666a4c6/screenshots/015.png) 与 [step16控制器刷新后](../../runs/20261108T051859Z-f666a4c6/screenshots/016.png) 均显示同一商品行售价 `$1,234.57`。保存后没有再输入或删除字段。

操作阶段已主动提出finish/fail，其原始观察明确比较 `$1,234.57` 与 `$1,234,567.89`。终局审查同样报告fail，与独立判定一致；两次缺陷均检出。

审查保留了step10输入、step12重开编辑、step15列表和step16刷新结果。结构化 `submission_step=12` 引用的是保存后的同记录编辑页，真正Save动作仍在step11；候选阶段标签不能当成Save动作本身。

逐字读取尚未完全正确：`Sale Price.observed_value` 写成 `"1.234.57"`，引用step15，而截图原文是 `$1,234.57`；Purchase Price写成 `"100.00"`，截图为 `$100.00`。observation中的售价与比较结论正确。因此可以确认本轮金额差异判断和主动结束改善，但不能把“结构化字段有值”计作逐字抄读完全正确。

## 已核对来源

每条结论对应各运行目录的 `trace.jsonl`、`model.jsonl`、`result.json` 和实际截图。A target两个repeat的对应关键截图相同。全16格的逐次来源如下；Agent/oracle写在各run_id之后。

| 条件 | repeat | v2 run_id · Agent/oracle | v3 run_id · Agent/oracle |
|---|---:|---|---|
| A target | 0 | `20261108T051135Z-d6ab7aaf` · pass/fail | `20261108T051859Z-f666a4c6` · fail/fail |
| A target | 1 | `20261108T051241Z-dac14776` · pass/fail | `20261108T052032Z-81d22411` · fail/fail |
| A preservation | 0 | `20261108T051334Z-e5c0e00f` · pass/pass | `20261108T052120Z-557e402d` · pass/pass |
| A preservation | 1 | `20261108T051419Z-85bb3f4f` · pass/pass | `20261108T052208Z-a1831c69` · pass/pass |
| B target | 0 | `20261108T051512Z-991b6edd` · pass/pass | `20261108T052300Z-61ab3d0c` · pass/pass |
| B target | 1 | `20261108T051616Z-783d2e48` · blocked/pass | `20261108T052340Z-73cf74ec` · pass/pass |
| B preservation | 0 | `20261108T051722Z-5d7140c8` · blocked/pass | `20261108T052417Z-ef840537` · pass/pass |
| B preservation | 1 | `20261108T051812Z-4af48ddc` · pass/pass | `20261108T052505Z-53a9e7f9` · pass/pass |

本分析未修改任何运行结果、模型或推理代码，也未把评估轨迹导入训练。
