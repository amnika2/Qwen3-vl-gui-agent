# SFT v2 数据说明

正式数据集 `datasets/sft-main-10apps-v2` 已冻结。构建内验证通过后未再改写；完整命令见 `docs/sft-v2-build-command.txt`，日志见 `docs/sft-v2-build.log`。

共 10 个训练应用、110 条真实完整轨迹、1,636 条样本，train 1,350 条、validation 286 条。来源为 v1 已核实的 102 条轨迹、新采相邻字段对照批次 `field-contrast-aeebb101` 的 8 条轨迹，以及两组单独实采的 14 条未提交 review。

| 样本类别 | 数量 | 来源 |
|---|---:|---|
| operation action | 1,402 | 原真实操作，不改动作标签 |
| operation finish | 110 | 每条专家完整轨迹的真实终态截图及历史 |
| terminal review | 110 | 真实重开或业务终态，最多四图 |
| unsubmitted prefix review | 14 | 真实执行至提交前，再实际 reload 的 blocked 审查 |

110 条 operation finish 保留源轨迹的实际结果：31 fail、79 pass。它们均位于专家真实终态，没有给尚可继续完成的前缀添加 operation finish。

124 条 review 为 18 fail、66 pass、40 blocked。blocked 包括 14 条真实未提交样本，以及 26 条在四图窗口中确实缺少必要输入或结果证据的完整轨迹审查。四图限制没有通过盲抄原多图标签来绕过：裁剪的长输入、未出现在选图中的 Country、滚出视口的 Subject 或 Category，以及未入窗的拒绝提示均保留证据缺口。

persistence review 的 before/after 是既有记录中相邻的真实重开或最终字段可见步骤；business profile 使用真实终态，不新增 controller reload。last_type/post_type 按真实历史截至 before 重放计算。每条派生样本记录源 run、源 hash、历史截止步、选图、字段可见证明分组及标签依据，所有图片均为原始截图。

14 条 prefix 严格继承各自 v1 parent_run_id 的场景和 train/validation 归属。独立截图审核发现其中 10 条 reload 后仍可见已有记录，因此仅在派生标签中将 final_record_visible 修正为 true；submission_succeeded 仍为 false，verdict 仍为 blocked。原 raw 文件保留，派生 provenance 记录原 assistant、原行及文件 hash、修正注释 hash 和可见性依据。

构建内验证通过：场景划分无交叉，五个冻结测试应用未混入；操作与审查提示词匹配 profile；历史和图片均来自已存在的步骤；review 不超过四图；v1 manifest、train、validation、media_manifest 的原字节 hash 保持不变。

必要测试命令：`.venv/bin/python -m pytest tests/test_sft_v2.py -q`。已完成结果：`5 passed in 0.07s`。覆盖真实相邻审查窗口、后续登录对 last_type 的影响、每个焦点字段的最终编辑状态、同屏字段证明合并，以及裁剪输入不能仅凭步骤号认定完整可见。正式 build 之后未重复运行测试或验证。
