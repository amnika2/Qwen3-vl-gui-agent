# 开发实验：动作效果反馈

预先观察：最终 40 次（evaluation-8db9d71b、d70aab0e）的 blocked 轨迹显示，操作阶段死在输入环节：005 无焦点输入文本静默丢失（8/8）；041 输入追加到旧值（8/8）；008 target 从未输入就点保存；007 缺少 type 动作。模型历史只记录 "executed"，无法区分"输入成功"与"输入落空"。

假设：把每步动作的可见效果回传给模型历史，模型就能纠正未落地的输入。通用机制，不读取用例内容、DOM 或 oracle。

实现（`src/guiqa/control.py`、`src/guiqa/runner.py` 的 `action_effect_feedback`）：

- `LoopControl.record` 已计算每步前后截图的 64×45 灰度帧差，本次将其存为 `difference`；
- `type` 动作后帧差 < 0.03 时（真实轨迹校准：无焦点输入 = 0.0，小字段成功输入 ≈ 0.10），向模型历史追加 "The typed text is not visible on the page. Click the target input field, press Control+a, then type the value."，并在 model.jsonl 记录 effect 事件；
- **click 不做效果反馈**：开发试验（evaluation-54c5e45b）证明聚焦输入框不产生像素变化，"点击无变化"反馈误指正确的聚焦点击为未点中，导致模型反复点击；仅保留可靠的输入落空反馈，静态点击循环检测不受影响。

迭代记录：

- evaluation-54c5e45b（click+type 反馈）：指标不变；逐条轨迹确认 type 反馈送达且模型尝试点击，但 click 反馈误导其反复点击，故移除。
- evaluation-092189cf（仅 type 反馈，开发分组 16 次）：指标表不变（0/4、0/12、16/16），但失败阶段前移一步——005 的模型在收到反馈后执行"点击→重新输入"，审查确认商品名已在 step 3 落地（此前 8/8 全部落空）。新阻塞点为：从不填写 Sale Price/Purchase Price，即多字段表单行为问题。
- evaluation-40fa4011（仅 type 反馈，recheck 24 次）：040 A target 2/2 检出保持，B target r0 本轮 oracle 确认 pass，成对正确恢复 1/6；007 A preservation pass/pass 恢复。

最终 40 次合计（092189cf + 40fa4011，同版代码）：缺陷检出 2/10，正常误报 0/30，执行未完成 33/40，成对正确 1/5，中位动作 7。与反馈前（2/10、0/30、34/40、0/5、中位 6）相比，指标基本不变、成对正确 +1，且 dev 轨迹证据显示输入落空这一失败类已被消除。

结论：动作效果反馈机制有效但收益受限于下一层瓶颈——模型不会填写表单的全部必填字段（005 的三个字段、008 的 name+value），这属于需要学习的操作行为而非环境信息缺失。配合之前"循环打断+证据门槛"的结论：环境侧能修的（终止、审查、解析、输入落空）已经修完，剩余 33/40 的执行未完成应通过 SFT 等行为学习手段解决，而非继续叠加运行时规则。
