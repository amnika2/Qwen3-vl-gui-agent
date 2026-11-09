# 审查 RL 首轮试验

日期：2026-11-08。已完成 64 题 × 8 候选诊断、54 份针对性真实 UI 补采，以及一次真实 Dr. GRPO 参数更新。原 v3/v4、原始数据和 v4 选用配置保留；试验 adapter 另存。本轮验证了训练闭环，质量增益尚未评测。

## 固定证据采样

模型使用 `configs/agent-review-evidence-v4-selected.yaml`，adapter SHA256 为 `d1c1c403d2fed4a5a788fbdfab3f55ae6d70a2a6af98d806ae6dc5422fac72ee`。

从 v4 的 105 条训练审查中冻结选择 64 题：全部 21 条 fail、23 条 pass、20 条 blocked，覆盖十个训练应用。验证场景、验证来源运行和五个留出应用均不参与选择。每题的公开任务、动作历史、截图完全固定，参考答案仅供评分。

采样参数为 seed 42、temperature 0.8、top_p 0.95、top_k 20、repetition_penalty 1、最多 1024 新 token，沿用 `evidence_v2` schema。批次上限为 4，根据输入长度和 16000 个批次提示 token 预算确定实际批次。每个候选记录实际批次种子、完整原文、token 数、用时、来源哈希及评分。

初始五图 batch4 试采显存不足，记录保存在 `reports/rl-review-diagnostic-batch4-probe`。正式运行的 GPU2 worker 还发生过一次预填充显存分配失败；失败日志保留为 `runs/rl-review-pilot-v1/worker-0-attempt0.log`，随后以 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` 按同一 manifest 续跑。

该分片后来在 Mantis 的约束解码出现 CPU 热点。定时 Python 栈定位到 LM Format Enforcer 0.11.3 的 trie 求交：大量只有一两个子节点的节点反复扫描 17805 个字符。独立 `lmfe_traversal.py` 对稀疏节点采用等价 membership 遍历，保留 parser、schema 与允许 token 集合。16 项边界测试及真实 Qwen 全词表重放通过；两个结构前缀的局部 CPU 时间约从 0.45 秒降到 0.015 秒。原卡住批次随后实际在 68.67 秒内完成；该批判断仍错，恢复执行与判断提升分开记录。

用户授权 401/402 后，将已有 440 个候选原 JSON 行逐字节转移到五分片，仅补剩余 72 个候选。401 GPU1、402 GPU0/1、404 GPU2/3 完成全部补充，五进程均退出码 0。生成参数、选样、图片、逐题种子算法及模型权重保持，原库和冻结源码没有修改。转移证明及额外性能补丁的源码快照位于 `runs/rl-review-pilot-v1-distributed`。

运行产物：

- `reports/rl-review-diagnostic-v1-distributed`：最终 512 候选、原始统计、复评分和性能验证。
- `manifest.json`、`selected.jsonl`：与原运行逐字节相同的冻结选样及来源身份。
- `shard-0.jsonl` 至 `shard-4.jsonl`、`predictions.jsonl`：完整候选原文及原始评分。
- `adjudications.json`、`adjudicated_predictions.jsonl`、`adjudicated_summary.json`：独立核图记录与复评分，原始回答保持不变。
- `runs/rl-review-pilot-v1-distributed`：多机命令、PID、退出码、转移证明、源码快照和完成记录。
- 原 `reports/rl-review-diagnostic-v1` 与 `runs/rl-review-pilot-v1`：初始 440 候选及原始失败/恢复证据。

```bash
.venv/bin/python scripts/diagnose_rl_reviews.py merge \
  --output reports/rl-review-diagnostic-v1-distributed --shards 5

.venv/bin/python scripts/adjudicate_rl_review_diagnostic.py \
  --output reports/rl-review-diagnostic-v1-distributed
```

如需从同一记录续跑，先确认对应 worker 已退出，使用实际分片编号与空闲卡；以下命令在本次完成后不会重跑已有候选：

```bash
CUDA_VISIBLE_DEVICES=2 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false \
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
.model-venv/bin/python -u scripts/diagnose_rl_reviews_fast.py worker \
  --output reports/rl-review-diagnostic-v1-distributed --shard-index 0 --shards 5
```

## 奖励和复审口径

主奖励要求终判与结构化关键事实同时正确。主分为 +1 或 -1，已核验的局部字段事实最多补充 0.2；格式错误为 -1。真实证据不足时，正确 blocked 可以获得正奖。

评分分别保存终判匹配、结构化事实匹配、严格引用匹配和可信奖励。新的截图引用、未知字段同义先保留为待复审，可信 reward 为 null；人工核图后才能批准等价引用。同值的重开截图不能替代保留字段的原始基准。自然语言说明不做字符串匹配，其事实矛盾另记人工语义审计，因此结构化严格正奖与整份解释正确分开报告。

全部 21 道 fail 题已完成 168 个候选。原始终判中 91 个为正确 fail，39 个误 pass，32 个 blocked，6 个解析失败。原始严格正奖为 4 条、覆盖 3 题；核图批准 Typesetter 的 `Default` 在本题中对应 `Header` 后，严格正奖为 5 条，仍覆盖 3/21 题。其中说明文字也明确成立的严格正例为 2 条、覆盖 2 题。另有两个 phpIPAM 题的关键结构事实与额外 Hostname 均正确，但固定金标对额外字段尚未解锁，不能计作现成正奖励。

这次选择性复审覆盖 51 个候选、12 道题，范围与理由保存在 `adjudications.json`；精确的 21 题信号汇总为 `fail_signal_summary.json`。结构化严格正奖、完整说明正确和更宽的语义候选分别记录。

完整复评分结果如下。本表统计训练题上的温度 0.8 多候选采样；原 40 条留出轨迹评测使用不同题目与确定性解码。

| 金标 | 题数 | 候选终判一致 | 严格字段与引用一致 | 至少一次严格正确的题 |
|---|---:|---:|---:|---:|
| fail | 21 | 91 / 168 | 5 / 168 | 3 / 21 |
| pass | 23 | 167 / 184 | 25 / 184 | 4 / 23 |
| blocked | 20 | 49 / 160 | 1 / 160 | 1 / 20 |
| 合计 | 64 | 307 / 512 | 31 / 512 | 8 / 64 |

176/512 个候选获得可信数值奖励；12/64 题的全部八个奖励均可信，其中 11 题有奖励差异、1 题同奖。331 个候选仍存在待核验字段或引用，不能直接当作已知错误。格式拒绝共 18 次：13 次重复字段、5 次达到长度上限未闭合；5 个截断候选均排除可信训练奖励。

实际错误集中在：`expected_rejection` 把正常保存任务误作预期拒绝；提交状态标签与错误响应混淆；输入空框与重开旧值混淆；重开后的值被当作原始保留基准。3/21 是当前可直接核验严格正奖励的题目下界，后续训练还需要扩展可信奖励覆盖和正确示范。

## 针对性补采

新增 54 份真实 UI 证据包，分组和分区在采集前固定；同组缺陷版、修复版留在同一分区。校准及失败校准保留在独立目录，没有计入正式数据。首批 46 份完成后，根据 blocked 诊断再补 8 份输入未建立的对照，首批产物和数据集 v1 保留。

| 应用与机制 | fail | pass | blocked | train / validation |
|---|---:|---:|---:|---:|
| Wolf：合法编辑被拒、重复 slug 应被拒 | 4 | 10 | 0 | 10 / 4 |
| phpIPAM：清空字段、取消与未建立输入 | 4 | 4 | 12 | 15 / 5 |
| OpenCart：小数权重与取消对照 | 4 | 4 | 4 | 9 / 3 |
| Typesetter：连续两次保存 | 4 | 4 | 0 | 6 / 2 |
| 合计 | 16 | 22 | 16 | 40 / 14 |

Wolf 用相同重复 slug 错误构造不同公开任务：编辑已有页并保留自身 slug 应成功，遭拒为 fail；创建重复 slug 并验证拒绝时，遭拒为 pass。持久化样本分别保存输入、可见提交响应、同对象重开和独立 oracle；8 个取消对照均确认没有真实提交事件。

新增的 8 个 phpIPAM 对照保持相同公开任务，把清空按键替换为实际的 ArrowRight，保留旧输入后照常保存并重开。独立记录均为请求输入未建立、保存接受、同对象重开仍旧值，oracle 为 inconclusive，审查为 blocked。四个缺陷版配对与之前 fail 具有相同最终旧值；区别在输入帧是否已正确清空。

phpIPAM 的首次校准截图存在 Loading 和弹窗淡入重叠。补采器在该应用截图前等待实际浮层隐藏和动画结束，新的 A/B 校准及正式截图均记录稳定状态。

正式来源为 `runs/rl-targeted-wolf-v1`、`runs/rl-targeted-persistence-v1` 和 `runs/rl-targeted-input-not-established-v1`，各自保留采集器快照、实际部署 commit、trace、原图、截图时可见 DOM/bbox、独立 oracle 和 `verification.json`。原 v4 全部 1031 个文件 SHA256 保持一致，追加采集还核对了旧 46 份与数据集 v1 的全部文件。DOM/网络与来源绑定逐条核验，人工像素复审覆盖代表性样本；没有将这表述为逐条全人工复审。

## 独立审查数据集

`datasets/rl-review-pilot-v2` 包含原 v4 的 130 条审查和新增 54 条，共 184 条：训练 145、验证 39，标签为 pass 93、fail 42、blocked 49。原有分区和金标保持，449 张去重图片按原字节拷贝，最多五图。首批 `datasets/rl-review-pilot-v1` 的 176 条、训练 139/验证 37 及其 443 张图片保留。

`verification.json` 已确认场景 ID、场景指纹、来源运行均无跨分区交集，五个留出应用排除，部署 commit 和原图哈希正确。构建命令为：

```bash
.venv/bin/python scripts/build_rl_review_dataset.py \
  --source datasets/sft-main-10apps-v4 \
  --curriculum runs/rl-targeted-wolf-v1/reviews.jsonl \
  --curriculum runs/rl-targeted-persistence-v1/reviews.jsonl \
  --curriculum runs/rl-targeted-input-not-established-v1/reviews.jsonl \
  --output datasets/rl-review-pilot-v2
```

输出目录必须是新目录。只需要新增样本时，可使用 `--new-only` 和另一个输出目录。此次数据集只含审查任务，原 v4 的操作监督和模型文件保留。

最终采样、评分、复审、补采、数据构建、GRPO 奖励及 LMFE 等价遍历相关 67 项定向测试通过；真实 v2 数据构建与验证通过。

## 最小 GRPO 更新结果

独立 `.rl-venv` 锁定 `trl==0.27.2`，复用原有 Torch 2.14.1、Transformers 4.57.6、PEFT 0.21.2，原环境保持。该版本把传入的 v4 default adapter 复制成 ref adapter，实际运行已校验初始化参数摘要相同、更新后 ref 不变。环境来源、版本与官方 wheel 哈希位于 `runs/rl-review-grpo-env`。

脚本 `scripts/smoke_review_grpo.py` 固定使用两个训练题、最多两次更新，单卡 microbatch 1、G4、梯度累积 4，Dr. GRPO、学习率 2e-6、beta 0.02。RL 小试使用完整词表温度采样，不挂自定义 schema logits processor。训练入口对已确定的终判错误、预期拒绝标记错误或逻辑矛盾给固定负奖，不读取暂定分；真正待核验的候选所在同题组仍整体任务奖励置零。

只有同一次更新同时满足可信组奖励存在差异、梯度有限且非零、actor 参数变化，且 ref 与原磁盘 v4 保持，才认定更新闭环成功。单次烟测不用于评价质量提升。

CPU 多图预检通过：Impress 两图 2995 tokens、Typesetter 三图 4263 tokens，与诊断预处理一致，CUDA 未初始化。首个 GPU 尝试 `runs/rl-review-grpo-smoke-v1` 第一组因未核验候选整组零任务奖励，梯度为零；第二组同时生成 G4 时显存不足，原失败产物保留。

修正版在同题内逐候选执行标准 HF 生成，收齐 G4 后仍按原奖励与 microbatch 进行反向传播。401 GPU0 上的 `runs/rl-review-grpo-smoke-v2` 只做一次更新即全部验收通过，退出码 0：

- 可信 G4 奖励为 `[1.2, -1.0, -0.81666667, -0.83333333]`，范围 2.2；第一个回答严格正确。
- 成功组没有待核验候选，也没有使用确定负奖覆盖，成功不依赖放宽未知项。
- 梯度 L2 为 0.110977977，全部有限；actor 参数摘要实际改变。
- ref 初始化等于 v4，更新后保持；视觉层冻结，原 v4 磁盘 SHA256 保持。
- 主体训练 51.10 秒，进程共 58.02 秒；峰值分配 19447.53 MiB，约 19.0 GiB。

证据为该目录的 `completion.json`、`rollouts.jsonl`、`gradient-steps.jsonl`、`update-steps.jsonl` 与 `adapter`。该 adapter 是两题预算内的闭环试验产物，当前选用配置仍为 v4。

后续应先用新增对照补强正确示范，并把已核验的字段同义与引用纳入奖励覆盖，再扩大 GRPO。正式训练按缺陷检出、正常误报和正确 blocked 分别选模。
