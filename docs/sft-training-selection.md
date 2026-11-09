# 十个表单类训练应用

更新：2026-11-08，Asia/Shanghai。十应用均已完成采集与核验；统一成品为 `datasets/sft-main-10apps-v1`，102 条完整轨迹、1398 条样本。当前数据和质量指标见 [SFT 数据进度](SFT_DATA_STATUS.md)。

主训练名单为 **10 个应用：8 个官方表单用例 + 2 个自建表单补充用例**。选择顺序优先已有适配，其次复用 PHP 8.3/7.4 与 MariaDB，再比较前端构建、依赖安装和业务 fixture 的复杂度。冻结的 Akaunting、Ampache、Anchor CMS、Easy!Appointments、emlog 五个评测应用保持不变。

## 名单与采集顺序

| 顺序 | 应用 / case | 训练流程 | 部署准备 | 当前状态 |
|---:|---|---|---|---|
| 1 | PluXml / 150 | 修改密码、显示名，保存后验证 | PHP，XML 文件存储，已有适配 | 已导出 136 条 |
| 2 | Typesetter / 185 | 两次编辑主题文本、保存、回首页核对 | PHP 7.4，文件存储，已有适配 | 已导出 80 条 |
| 3 | DokuWiki / 034 | **自建**页面正文编辑、保存、重开 | PHP，文件存储 | 12 场景已采集、核验 |
| 4 | FlatPress / 053 | **自建**静态页正文编辑、保存、重开 | PHP，文件存储 | 12 场景已采集、核验 |
| 5 | LiteCart / 105 | 填写 Code、Name，选择 Country，保存后重开 Geo Zone | PHP/MariaDB，Web 安装 | 12 场景已采集、核验 |
| 6 | Wolf CMS / 195 | 修改页面 Title/Breadcrumb；校验重复 slug | PHP 7.4/PDO/MariaDB | 12 场景已采集、核验 |
| 7 | ImpressCMS / 084 | 修改 Site Name 等设置、保存、重开 | PHP 7.4/PDO/MariaDB，独立 GD | 12 场景已采集、核验 |
| 8 | OpenCart / 125 | 修改 option 重量和库存、保存、重开 | PHP/MariaDB，Composer | 12 场景已采集、核验 |
| 9 | MantisBT / 107 | 多字段填写并提交 Report Issue | PHP/MariaDB，Composer | 12 场景已采集、核验 |
| 10 | phpIPAM / 142 | 清空 Description、修改 Hostname，重开核对 | PHP/MariaDB，固定 submodule、GMP/PEAR | 12 场景已采集、核验 |

十个应用均已有实例、登录、动作流和独立观察器。后六个通过三个子智能体并行完成部署、校准和采集；固定官方元数据保存在 `cases/<case_id>/private.json`。各应用原始数据路径与核验记录在 `configs/sft-app-readiness.yaml`，统一成品按完整轨迹去重，原始分应用数据保留。

ImpressCMS 的官方 preservation 只打开表单，主训练仅采 target；preservation 保留用于官方环境校准。Wolf CMS 的 preservation 是真实重复 slug 拒绝，作为表单校验样本保留。其余应用按 `configs/sft-app-readiness.yaml` 的 `collect_workflows` 采集，共 19 个训练 workflow。

DokuWiki 与 FlatPress 的官方 workflow 分别是媒体 ACL 和 PrettyURLs，官方分类仍为 N。它们在主训练中使用仓库现有的自建文本编辑流程，导出标记 `case_source: supplementary`，两个版本均预期 pass。此选择直接复用已接入的四格正常保存流程。

## 部署证据与取舍

这次按 benchmark **固定提交**核对依赖，未用最新版安装说明代替历史版本要求。固定源码检查记录在 [依赖证据](sft-selection-dependency-evidence.json)，每项准备状态与来源链接在 [readiness 配置](../configs/sft-app-readiness.yaml)。

- [LiteCart 的固定源码](https://github.com/litecart/litecart/tree/612d0e6b12253b52dc8c91f544d01153202095cd/public_html) 包含安装目录与 PHP 应用。仓库也有 Grunt 开发构建配置，实际部署时先核对随源码提供的主题资源。Web 安装方式可参考 [官方安装文档](https://www.litecart.net/wiki/how_to_install)。
- [Wolf CMS 的固定 README](https://github.com/wolfcms/wolfcms/blob/bb30b245e3c38afbdf9b03cf0c5d0613db94bd0f/readme.md) 声明 PDO 与 MySQL/SQLite 支持；实际采用已有的 PHP 7.4/PDO/MariaDB，两版一致使用原生 `allow_html_title=on` 排除旧 Kses 兼容问题，业务源码未改，保存后重开值已验证。
- [ImpressCMS 的固定 libraries 目录](https://github.com/ImpressCMS/impresscms/tree/4b8128ed5ede9b2dea929828dd87a6b98ddcc3e8/htdocs/libraries) 包含 Smarty、HTMLPurifier、jQuery 等库，不按其最新版的安装要求推定本提交必须完整重建依赖。
- [OpenCart 的固定 Composer 文件](https://github.com/opencart/opencart/blob/0dea7746a52a8e627e392283f4df0ad36e24023c/composer.json) 要求 PHP >=8.1，主要运行依赖为 Twig、scssphp；管理界面源码已有 Bootstrap/jQuery 等资源，优先复用现有 PHP 8.3。
- [MantisBT 的固定 Composer 文件](https://github.com/mantisbt/mantisbt/blob/148cc34a4ae57848eabd103638642e203f79c72a/composer.json) 要求 PHP ^8.1，需按 lock 安装依赖。源码 `vendor/` 只有访问控制配置，不将这个目录误判为完整 vendor 缓存。
- [phpIPAM 的固定 .gitmodules](https://github.com/phpipam/phpipam/blob/81bf8abc4bae32261cf471f3e72f94f1e4a854bb/.gitmodules) 表明依赖来自多个 submodule，需按 gitlink 固定版本准备；之前将它描述成“需 Composer”不准确。它排在后面是因为需要更多依赖与 IP fixture 准备。

Wolf CMS、ImpressCMS、MantisBT 已实现独立业务 oracle，以及操作和审查同源的 `business_outcome_v1` profile。合法输入、真实提交、特定业务错误或预期拒绝均有本次截图证据；被拒绝时相应证据字段保持 false。其余应用沿用 persistence profile。

本轮未优先选择 SMF、phpList、Crater、Fork CMS、Kimai：主训练先复用已有适配和更短的初始化链路。Typemill 的官方流程含 Vue 内部状态操作，接入截图协议前还需另验证鼠标键盘路径；AdminerEvo 的官方流程终止于读取编辑表单默认选项，没有实际 Save，因此归 O。备选保留在 split 中，后续替换名单时有明确依据。

WonderCMS 已生成的 48 条菜单排序数据保留在 `datasets/sft-wondercms`，移入 `auxiliary_pool` 单列，不计这十个表单类应用。Flyspray、Cockpit、Zen Cart 的相邻操作任务也单列。

## 全量分类

已按 target 与 preservation 分开记录 **200 个官方用例、400 个官方 workflow**。人工分类源为 [sft-case-types.yaml](../configs/sft-case-types.yaml)，报告通过脚本生成，替换原先基于全文关键词的 A/B 分类。

| 官方 target 类型 | 数量 |
|---|---:|
| F：表单填写/编辑、提交、验证结果 | 68 |
| V：表单校验与无效提交拒绝 | 14 |
| O：只打开表单、回填、候选项或输入联动 | 12 |
| L：删除、移动、复制、重排、状态切换 | 35 |
| N：导航、搜索筛选、展示、权限观察 | 31 |
| X：上传、导入、导出、下载 | 23 |
| A：登录、邀请、邮件/令牌密码重置 | 4 |
| B：复合业务与外部服务联动 | 13 |
| 合计 | 200 |

这些类型描述 workflow 的主要触发动作与终局检查；准备阶段可能同时含其他动作。F 包含保存后在列表、首页或重新登录中验证结果，并非要求每条流程最终停在同一种编辑表单上。

完整结果：[CSV（400 行）](../reports/sft-classification.csv)、[JSON](../reports/sft-classification.json)、[逐应用对照表](../reports/sft-classification.txt)。重新生成：

```bash
.venv/bin/python scripts/classify_sft_cases.py
```

## 完成结果与复现

DokuWiki、FlatPress 的 script 校准首批为 `evaluation-53663a3a`：4 次 pass、4 次 Chromium `Page.captureScreenshot` 故障。仅对失败四格重跑，4/4 取得 pass，记录为 [recheck JSON](sft-selection-calibration-recheck.json)，日志 `docs/sft-selection-recheck.log`。两版、两条自建流程均有真实 pass 轨迹，原故障保留，未修改浏览器公共代码。

现已完成新八应用的 **96 个不同场景**。加原 PluXml/Typesetter 数据，并去除 10 条重复完整轨迹后，统一成品为 **10 应用、102 轨迹、1398 样本，train1164/validation234，919 张图片**；审查 27 fail/75 pass。两名子智能体另做了只读交叉复核。

```bash
.venv/bin/python scripts/verify_sft_dataset.py --dataset datasets/sft-main-10apps-v1
.venv/bin/python scripts/build_sft_release.py --output datasets/sft-main-10apps-rebuild
```

正式批次源固定在 `configs/sft-release.yaml`，重建使用新输出目录。DokuWiki/FlatPress 的丰富场景入口为 `scripts/collect_supplementary.py`；其他六个应用分别由 `collect_litecart_wolf.py`、`collect_impress_opencart.py`、`collect_mantis_phpipam.py` 采集。

选择阶段校验保留在 [selection verification](sft-selection-verification.json)；最终数据核验与逐应用统计见 [生成完成记录](sft-generation-complete.json)。5 个评测留出、原始 benchmark 及既有数据保持原样。
