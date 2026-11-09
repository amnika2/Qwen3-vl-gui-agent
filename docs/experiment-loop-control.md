# 开发实验：循环打断与受控终局判定

预先观察：正式对照（reports/summary.md）中 qwen-base 与 qwen-improved 的主要终止原因是 action_budget（40 次中 28 与 32 次）。轨迹显示模型在第 3–5 步完成或放弃业务操作后，在同一坐标反复点击（如 `runs/20261106T170513Z-1a7aac4f` step 3–30 连击同一坐标 26 次），或在两个页面控制之间 A-B-A-B 周期循环（`runs/20261107T042845Z-d3daf7c2`）。留出上另有 6+9 次 blocked 时独立判据已持有效证据。进度记录文本对这些退化行为没有约束力，其重复点击计数对交替循环不触发（ledger.py 只统计坐标完全相同的连续点击）。

假设：在代码层检测原地重复与动作周期循环并打断，计入预算执行一次刷新，然后进入只允许输出最终判定的截图审查阶段，可以把耗尽预算的退化运行转化为终局判定，提高有效缺陷检出。判定仍由模型读取截图完成，控制器不读取 DOM、数据库、oracle 或版本标签。

唯一方法开关是 `--method qwen-controlled`。与 qwen-base 使用相同模型、截图、用例、8 条动作历史、30 次动作预算和 600 秒执行预算。机制（`src/guiqa/control.py`、`src/guiqa/runner.py` 的 `controlled_review`、`src/guiqa/model.py` 的 `assess`）：

- 连续 3 次点击且画面无可见变化（64×45 灰度帧差 < 0.5），或长度 1/2/3 的动作序列重复 3 个周期，即触发打断；
- 触发时（或到达第 29 步保留位、模型主动 finish 时）由控制器执行一次 Control+r 刷新，计入动作预算；
- 审查阶段输入"最后一次输入前、刷新前、刷新后"三张截图与动作日志，模型只被允许输出 finish 判定；输出非法记 review_invalid_output。

试点：`runs/20261107T051152Z-5831e51b`（sweeper-041 A target）在 step 5 触发 action_cycle_period_1，审查报 fail，但 oracle 为 trigger_not_verified（保存未真正提交），属无证据报告。

批量结果（2026-11-07，server402 GPU 0/1，各一次完整排程）：

| 批次 | 分组 | 缺陷检出 | 正常误报 | 执行未完成 | 成对正确 | 中位秒 | 中位动作 |
|---|---|---|---|---|---|---|---|
| evaluation-cd34ca90 | reuse-development（041, 005）16 次 | 0/4 | 0/12 | 16/16 | 0/4 | 20.43 | 5.5 |
| evaluation-1f3f9be9 | reuse-former-holdout（040, 007, 008）24 次 | 2/6 | 4/18 | 17/24 | 1/6 | 20.76 | 7.5 |

对照正式轮次同条件 qwen-base：检出 0/10、正常误报 0/30、中位动作 30。

逐项记录：

- sweeper-040 A target 2/2 取得 oracle 确认的 fail（Qwen 各方法首次有效检出），B target r0 pass/pass，构成 1 对成对正确。
- sweeper-040 preservation 4/4 被审查阶段报 fail 而 oracle 无证据（trigger_not_verified），按指标定义计入正常误报，使误报从基线 0 升为 4/18。040 的 preservation 用例本身触发不了循环打断，跑到第 29 步保留位才进审查（reserved_review_step）。
- sweeper-007 preservation 3/4 为 pass/pass（含 2 次 model_proposed_finish 路径），target 仍 0 检出。
- sweeper-041 preservation 4/4 与 sweeper-008 3 次以 invalid_output_twice 终止：模型在第 2–3 步连续输出非法 JSON，循环打断对此无效。
- 041 A target 2/2 与试点相同：审查报 fail，oracle 无证据（保存未提交），不计入检出。

结论：循环打断把终止原因从 action_budget 转为 controlled_review，中位动作从 30 降到 6–8，并在 sweeper-040 上产生首次 oracle 确认的检出（合计 2/10）。但审查阶段会在触发未证实时下 fail 结论，正常用例误报升至 4/30，整体不满足"无证据不报错"的可信度要求。循环打断本身保留；审查阶段的判定约束（触发未证实时必须 blocked）是下一轮开发候选，尚未实现。reuse-former-holdout 结果只作开发参考，不作留出成绩。

# 第二轮：审查证据门槛与输出格式恢复

预先观察：第一轮的 6 次 fail/inconclusive 全部属于"编辑值错误或未有效提交，却被当作已正确提交后未持久化"。041 A target 两次保存被 HTTP 400 与用户名只读提示拒绝；040 preservation 三次最终编辑未保存、一次校验报错，审查都报 fail。早期终止另有两类解析失败：041 preservation 4 次输出 `{"action":"press","key":"F5"}`（F5 不在按键白名单）；008 3 次一次输出多个顶层 JSON 且末尾截断。

对第一轮的三个具体错误，第二轮保持循环打断机制不变，只改审查与解析：

1. 审查输出增加三项可见证据字段：`requested_value_entered`、`submission_succeeded`、`final_record_visible`。pass/fail 需三项全真，否则由控制器降为 blocked（`review_guard`），模型原始判定保留在 `review_model_verdict` 与 model.jsonl，不回传 DOM/数据库/oracle 信息。审查证据截图在"最后输入、干预前、刷新后"之外补入"输入后首个动作"（`LoopControl.post_type_step`），用于观察保存后的校验结果。
2. 最后一次 `type` 后没有任何操作就进入审查时（`last_edit_not_submitted`），禁止 pass/fail。
3. 受控操作阶段将 `F5` 归一化为 `Control+r`（基线解析不变，仅 `qwen-controlled` 开启）；解析失败重试时附加"只返回一个 JSON 的下一动作"纠错提示。

中间迭代（批次均保留）：e9e05dfa/9e0ac127 证实误报与 invalid_output_twice 全部消除，但证据字段语义被误读，040 真实检出被压为 0；3472dc93 澄清字段语义后 040 检出恢复 2/2，但 041 出现 4 次"拼接值保存成功后被报 fail"；180c2625 加入未提交门槛与列表字段对照后 040 达到 2/2 检出、0/6 误报；最终再补充"成功提示不等于目标值正确提交"的交叉检查（8db9d71b）。

最终 40 次结果（同一版代码，2026-11-07，server402 双卡各一次完整排程）：

| 批次 | 分组 | 缺陷检出 | 正常误报 | 执行未完成 | 环境故障 | 中位秒 | 中位动作 | 中位模型调用 |
|---|---|---|---|---|---|---|---|---|
| evaluation-8db9d71b | reuse-development（041, 005）16 次 | 0/4 | 0/12 | 16/16 | 0/16 | 23.16 | 6.0 | 7.0 |
| evaluation-d70aab0e | reuse-former-holdout（040, 007, 008）24 次 | 2/6 | 0/18 | 18/24 | 0/24 | 17.91 | 6.0 | 7.0 |

合计：缺陷检出 2/10，正常误报 0/30，执行未完成 34/40；终止原因全部为 controlled_review（33）或 controlled_review_guarded（7），action_budget 与 invalid_output_twice 均为 0。审查的 7 次降级全部为 `requested_value_entered` 未建立。

逐项记录：

- sweeper-040 A target 2/2 保持 oracle 确认的 fail；B target 2/2 agent 报 pass，其中 r0 oracle 因本轮未捕获重开事件记 inconclusive（此前 180c2625 同格 oracle 曾确认 pass），成对正确因此记 0/6，属判据观察波动，不改模型记录。
- sweeper-007 preservation（B 版）2/2 pass/pass；A 版与 target 全部 blocked。
- sweeper-041、sweeper-005 全部 blocked，无一次无证据 fail；041 的 4 次 F5 解析失败与 008 的 3 次多 JSON 解析失败均未再出现。

结论：第二轮后 `qwen-controlled` 相对 qwen-base：缺陷检出 0/10 → 2/10，正常误报 0/30 → 0/30，中位动作 30 → 6，全部运行都能给出结构化终局（含降级 blocked）而不是耗尽预算。代价是每格平均增加约 1 次审查模型调用，且 34/40 仍是执行未完成，主要剩余瓶颈是操作阶段本身走不到有效提交（尤其 005 的建表单流程），后续优化应回到操作阶段而非继续收紧审查。
