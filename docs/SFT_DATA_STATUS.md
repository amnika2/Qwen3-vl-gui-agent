# SFT 数据生成进度

更新：2026-11-08，Asia/Shanghai。十个训练应用的数据生成、合并与核验已完成，尚未开始训练。

## 统一成品

数据目录：`datasets/sft-main-10apps-v1`。

- **10 个应用，102 条完整轨迹，1398 条样本**：1296 operation、102 review。
- `train.jsonl`：1164 条，来自 86 条训练轨迹。
- `validation.jsonl`：234 条，来自 16 条预留验证轨迹。
- 审查标签：**27 fail、75 pass**，均来自本次实际执行的独立判定。
- 919 张按内容哈希去重的 PNG，已打包到 `images/`；消息图片路径相对数据目录。
- 来源、样本及图片哈希见 `manifest.json`、`media_manifest.json`；完整验证结果见 `verification.json`。
- 分应用统计见 `collection_summary.json`，项目摘要见 [生成完成记录](sft-generation-complete.json)。

## 十个应用

下表为统一成品中保留的数据量。PluXml、Typesetter 的原始数据分别仍为 136、80 条；成品按完整轨迹去重后分别保留 50、33 条。

| 应用 | 轨迹 | 样本 | train | validation | fail / pass |
|---|---:|---:|---:|---:|---:|
| PluXml | 3 | 50 | 50 | 0 | 1 / 2 |
| Typesetter | 3 | 33 | 33 | 0 | 1 / 2 |
| DokuWiki | 12 | 115 | 89 | 26 | 0 / 12 |
| FlatPress | 12 | 168 | 138 | 30 | 0 / 12 |
| LiteCart | 12 | 198 | 163 | 35 | 3 / 9 |
| Wolf CMS | 12 | 154 | 128 | 26 | 3 / 9 |
| ImpressCMS | 12 | 162 | 136 | 26 | 6 / 6 |
| OpenCart | 12 | 192 | 160 | 32 | 5 / 7 |
| MantisBT | 12 | 205 | 170 | 35 | 4 / 8 |
| phpIPAM | 12 | 121 | 97 | 24 | 4 / 8 |
| 合计 | 102 | 1398 | 1164 | 234 | 27 / 75 |

DokuWiki、FlatPress 使用自建表单编辑流程，来源标记为 `supplementary`；其官方 ACL、PrettyURLs 任务仍另行分类。其余八个应用使用官方回归流程。WonderCMS 的 48 条排序数据保留为辅助数据，未加入本成品。冻结的五个评测应用未进入训练或验证数据。

## 多样性与证据质量

新采集的八个应用共 96 个不同场景，每应用 12 个，变化包括页面起点、内容长度和语言、替换与追加、局部修正、多字段及字段顺序、预览后保存、连续两次保存、数值与空值、正常保存和真实业务拒绝。每个场景只采一次有效轨迹，A/B 均衡分配不同任务。

DokuWiki、FlatPress 的 24 个场景使用 `configs/sft-supplementary-scenarios.yaml`。观察器核对同一页面 ID、完整重开值和每次真实提交；FlatPress 同时核对 Subject 与 Content。连续保存任务必须先观察到第一次保存结果，才能完成第二次判定。原生表单请求的 CRLF 在入口归一为 LF，保留完整文本比较。中文场景补齐 Noto CJK 后重采，并已目视确认可读。

训练与验证按完整场景划分，场景 ID、语义指纹、完整轨迹及完整样本跨划分交集均为零。这是训练应用内部的验证集；原五个应用继续作为独立评测留出。

合并前共 112 条有效来源轨迹、1531 条样本；按任务、实际结局与语义动作序列去除 10 条重复轨迹，得到当前 102 条、1398 条。A fail/B pass 的不同实际结局均保留。保留轨迹中有 15 条相同操作前缀，来自旧 PluXml、Typesetter 的不同结局轨迹，全部在 train；新采集的八个应用没有精确重复样本。原始数据及被排除的失败、调试记录均保留。

## 提示词与标签

操作与审查均使用推理侧同源提示词、动作历史和截图约定，所有输出动作通过协议解析。

- `persistence`：66 条轨迹，要求正确输入、提交成功、同一记录重开后的值可见。
- `business_outcome_v1`：36 条轨迹，用于 Wolf CMS、ImpressCMS、MantisBT。支持合法输入被明确业务错误拒绝，以及任务要求的重复值拒绝；提交失败或记录不存在时，相应布尔字段保持 false。操作与审查两阶段都使用这一 profile。
- 已隔离旧提示词冲突批次，统一成品只取最终重采批次。原五个评测应用继续使用原有提示词与默认审查行为。

finish 标签与每条运行的独立 oracle 一致，不根据 build 直接生成标签。19 条真实业务拒绝记录的证据布尔值在导出前后保持一致。两名子智能体另做只读复核，核对了跨分区隔离、去重、源码来源和成功/失败的截图证据链。

## 来源与环境

正式来源固定在 [sft-release.yaml](../configs/sft-release.yaml)，共九个批次；合并器不扫描全部 `runs/`。成品引用的原始样本与截图逐字节核对通过。

原始 benchmark JSONL 的 SHA 未变，十个案例的 `private.json` 内容仍对应官方原始行，应用业务源码保持固定提交。OpenCart base 有五个 Composer 生成元数据文件差异，均位于 `upload/system/storage/vendor/composer/`，涉及开发自动加载、版本引用、dev 标记和平台检查生成方式；业务与依赖实现文件未改。

Wolf 两版一致启用应用原生 `allow_html_title=on`，排除旧 Kses 在 PHP 7.4 下的无关过滤问题，仍核对真正的 slug 保存与拒绝。LiteCart 安装器的在线替换路径在实例环境中关闭，避免固定源码被更新。ImpressCMS 的 GD、phpIPAM 的 GMP/PEAR 均以用户目录依赖加载。详见各应用的校准、运行时与源码溯源记录。

并发采集实际出现的 MariaDB 临时表故障已通过实例级 `TMPDIR/--tmpdir` 隔离解决，并用真实实例查询验证，见 [tmpdir 核验](native-instance-tmpdir-verification.json)。整页导航动作明确等待导航完成再截图。字体包与字体文件哈希见 [字体记录](sft-font-manifest.json)。

## 核验与复现

公共验证覆盖源样本哈希、图片解码与字节、prompt/history、完整轨迹、场景划分、审查规则和实测标签。相关流水线测试 39 项通过；补齐业务操作 profile 后相关测试另有 18 项通过。日志为 `docs/sft-final-pipeline-tests.log`、`docs/sft-business-profile-tests.log`。

核验成品：

```bash
.venv/bin/python scripts/verify_sft_dataset.py --dataset datasets/sft-main-10apps-v1
```

从固定来源重建到新目录：

```bash
.venv/bin/python scripts/build_sft_release.py --output datasets/sft-main-10apps-rebuild
```

后续增加任务时扩展场景内容与行为，并用新批次、新输出目录采集。现有 `configs/sft-supplementary.yaml` 保留作基础固定流程校准；丰富场景采集入口为 `scripts/collect_supplementary.py`。全部 200 个官方用例、400 个 workflow 的分类仍见 [分类报告](../reports/sft-classification.txt)。
