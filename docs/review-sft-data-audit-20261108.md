# 审查 SFT 数据核对与 v5 构建

2026-11-08，按现有训练应用和场景分区完成审计。数据来源为 `datasets/sft-main-10apps-v4` 和 `datasets/rl-review-pilot-v2`。

## 已确认的数量

| 数据 | 训练审查 | pass / fail / blocked | 开发审查 |
| --- | ---: | --- | ---: |
| v4 实际 SFT | 105 | 59 / 21 / 25 | 25 |
| 已整理的 RL 审查池 | 145 | 75 / 33 / 37 | 39 |
| 本次 v5 审查 SFT | 145 | 75 / 33 / 37 | 39 |

v4 的 1642 条总样本包含 130 条审查；实际训练分区为 1354 条，其中 105 条审查，1249 条操作。新增的 40 条训练审查来自 `runs/rl-targeted-wolf-v1`、`runs/rl-targeted-persistence-v1`、`runs/rl-targeted-input-not-established-v1` 三批真实运行。

145 条训练审查按提交状态和结论分布如下。

| 提交状态 | pass | fail | blocked |
| --- | ---: | ---: | ---: |
| accepted | 66 | 20 | 14 |
| rejected | 9 | 13 | 2 |
| unknown | 0 | 0 | 21 |

9 条 rejected/pass 的公开任务明确要求拒绝。accepted/blocked 包括正确输入没有建立、保留字段缺乏可见基准等情况；它们用于区分“结果与要求不符”和“本次测试的前提没有完成”。

## 原图核对

原 v4 的 105 条训练审查沿用已有可见读值标注。新 40 条训练审查原来都带 `human_pixel_review=pending`。本次由 Codex 使用 `view_image` 查看其全部 68 张去重关键原图，核对各字段的 before/after、接受或拒绝响应及取消步骤。PHPIPAM 的 Escape 取消与 OpenCart 的关闭弹窗动作额外对照原 trace。

未发现这 40 条的结构化金标错误，未改字段值、引用步骤、提交状态或结论。逐条清单、原图哈希、原行哈希在 `docs/sft-review-v5-pixel-audit.json`。这是 Codex 图像核对；源数据的人工作业状态保持原值，生成数据另外记录 `agent_pixel_review`。

旧说明中有两种命名不精确：PHPIPAM 实际提交按钮标为 `Edit IP`，旧说明统称 Save；OpenCart 取消样本点击弹窗的 X 后返回，旧说明统称 cancel。其结构化 status 和 verdict 正确。v5 模型答案去掉这两处自由说明，程序根据字段比较、状态和步骤生成说明。

## 成对案例覆盖

| 训练目标 | 现有真实证据 |
| --- | --- |
| 同样提交，保存正确与重开值错误 | Typesetter、PHPIPAM、OpenCart 同场景 A/B 对照，训练 birch/hazel/pine |
| 返回列表时区分完成保存与取消 | PHPIPAM、OpenCart 各三组 saved/cancelled |
| 空框、占位文字与未见字段 | PHPIPAM 清空字段的可见空框；旧 v4 保留字段与 prefix 审查的 null 读值 |
| 任务要求拒绝与本应成功却被拒绝 | Wolf 编辑现有记录与新建重复 slug；两者出现相同错误原文，但公开任务要求不同 |
| 提交前、提交响应、重开后分阶段读值 | PHPIPAM、OpenCart、Typesetter 三阶段截图；PHPIPAM 另有 6 条输入未建立但提交已接受的训练轨迹 |

“空框与完全未见字段”已有两种监督，但还不是同一字段和同一任务下的严格成对采集。程序异常与业务拒绝的应用覆盖也较窄。现有数据足以先做这轮小规模 SFT；后续若该类错误持续，再用新训练场景补采对应对照。未追加采集，也未将开发错例改成训练样本。

## v5 数据产物

`datasets/sft-review-v5/train.jsonl` 共 290 条：145 条 facts-only 审查与 145 条原操作回放。回放仅从原训练分区抽取，按应用和场景轮转，每个应用 14 或 15 条，覆盖 92 个场景；场景内以 `SHA256(42:id)` 固定顺序。回放整条记录与原记录相等，包括提示和答案字符串。

`validation.jsonl` 为同一批 39 条开发审查，标签分布为 pass 18、fail 9、blocked 12。样本 ID、公开任务、动作历史、图片说明、截图、场景 ID 和原分区保持；只替换审查系统提示及删除模型答案中的两处 observation。所有结构化金标保留。

源数据四个产物的哈希与 manifest 相符，构建后再次相同。训练所用 361 张审查原图哈希全部一致，最终打包图片由源内容哈希命名。训练与开发的 scenario_id、scenario_fingerprint 和 source_run_id 交集均为空；冻结五应用没有进入数据。

构建入口与检查：

```bash
.venv/bin/python scripts/build_sft_review_v5.py
.venv/bin/python -m pytest -q tests/test_sft_review_v5.py
```

`manifest.json` 记录两套原始数据哈希、审图清单哈希、回放 ID、数量及分区；`verification.json` 记录构建核验。已有目录不会覆盖，重建时用 `--output` 指定新路径。训练必须从 v4 adapter 初始化新的优化器；此数据构建不启动训练。
