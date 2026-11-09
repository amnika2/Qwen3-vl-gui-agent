# SFT 应用任务类型核对（上一轮抽查）

本页保留上一轮 20 个应用的抽查记录。当前已完成 200 个用例的逐流程分类，并将主训练调整为 8 个官方表单用例 + 2 个自建表单补充用例；现行名单与依据见 [十应用训练选择](sft-training-selection.md)，完整结果见 [生成报告](../reports/sft-classification.txt)。下文中的分组数量是上一轮配置状态。

核对日期：2026-11-07，Asia/Shanghai。依据当前 `configs/sft-split.yaml` 与原始 `vendor/SWEeper-Bench/test.jsonl` 中逐项读取的 `task_specified`、target/preservation workflow。分类单位是任务流程；同一个应用可以包含不同类型的流程。

上一轮配置为 5 个评测留出、9 个 training_pilot、6 个 training_pool，当时未找到含 10 个应用的 pool。以下是当时对这 20 个应用的抽查。

## 判定口径

- 表单提交与验证：填写或清空字段、选择表单选项，提交保存后，通过重开表单、列表、首页或重新登录验证结果。包含创建表单与编辑表单。
- 列表操作与验证：排序、切换状态、删除条目，再验证变化；可以训练操作与验证能力，但与多字段填表的动作分布不同。
- 删除重建与验证：删除对象、按同名重建，再验证原数据是否清除。
- 导航观察：打开页面或列表，检查可见内容。

## 冻结的 5 个评测应用

| ID | 应用 | 目标流程 | 类型 |
|---|---|---|---|
| 005 | Akaunting | 创建商品，填写售价，保存后核对列表价格 | 表单提交与验证 |
| 007 | Ampache | 修改智能播放列表名称，保存后核对列表标题 | 表单提交与验证 |
| 008 | Anchor CMS | 修改站点变量值，保存后重开核对 | 表单提交与验证 |
| 040 | Easy!Appointments | 修改服务人员手机号，保存后重开核对 | 表单提交与验证 |
| 041 | emlog | 修改作者昵称，保存后核对 | 表单提交与验证 |

这 5 个属于相同的大类。其中 Akaunting 是创建商品表单，其余主要是编辑已有对象。

## 上一轮 training_pool 的 6 个应用

| ID | 应用 | target 原始 workflow | 目标动作 | 判定及 preservation |
|---|---|---|---|---|
| 025 | Cockpit | `singleton-delete-clears-persisted-data` | 删除 singleton，重建同名 schema，再打开表单检查旧值是否清除 | 删除重建与验证；preservation 是填写 Title、保存、重开，属于表单类 |
| 084 | ImpressCMS | `target-preferences-save` | 修改 General Settings 的 Site Name，保存后重开核对 | 表单类；preservation 只打开设置表单，属于导航观察 |
| 030 | Crater | `target-notification-email-persists-without-currency` | 修改通知邮箱，保存后整页重载核对 | 表单类；preservation 修改时区后保存重载，也是表单类 |
| 058 | Fork CMS | `pages-add-auth-required-persists` | 创建页面，勾选 Authentication required，保存后重开 | 表单类；preservation 创建普通页面并核对标题，也是表单类 |
| 093 | Kimai | `target-decimal-hours-eight-point-two-zero` | 填写工时 Duration、Project、Activity、Description，保存后核对同一记录 | 表单类；preservation 使用冒号格式 Duration，也属表单类 |
| 199 | Zen Cart | `target-ezpages-header-status-toggle` | 点击 EZ-Pages 列表中的 Header 状态方块，核对 OFF 状态 | 列表状态切换；preservation 只打开列表，属于导航观察 |

结论：6 个 pool 中，4 个 target 属于表单类，Cockpit 与 Zen Cart 不能整体标成严格的表单编辑类。如果主训练要集中解决填表、提交、重开，优先考虑 ImpressCMS、Crater、Fork CMS、Kimai；Cockpit 的 preservation 可以单独按表单流程使用。

## 上一轮 training_pilot 的 9 个应用

| ID | 应用 | 目标动作 | 类型 |
|---|---|---|---|
| 150 | PluXml | 修改密码，保存后重新登录验证；相邻流程修改显示名 | 表单提交与验证 |
| 196 | WonderCMS | 下移首个菜单项，关闭再打开设置验证顺序 | 列表排序与验证 |
| 185 | Typesetter | 主题 Area 文本编辑两次，每次保存后回首页验证 | 表单提交与验证 |
| 105 | LiteCart | 在 Geo Zone 表单选择待添加 Country，直接保存后重开 | 表单提交与验证 |
| 125 | OpenCart | 修改商品 option value 的小数重量，保存后重开 Options | 表单提交与验证 |
| 142 | phpIPAM | 清空 IP Description，保存后重开 | 表单提交与验证 |
| 055 | Flyspray | 勾选已有 reminder 并 Remove，验证条目删除 | 列表删除与验证；preservation 新增 reminder 使用表单 |
| 167 | SMF | 重开已有草稿，编辑正文，再次 Save draft | 表单提交与验证 |
| 143 | phpList | 修改订阅者密码，保存后检查更改日期 | 表单提交与验证 |

pilot 的 7 个 target 属于表单类，WonderCMS 与 Flyspray 属于相邻操作类。已生成的 WonderCMS 数据可以作为排序与验证的补充数据，其动作分布应独立统计。

上一轮仅纠正了 split 中的分类说明与注释，当时未调整分组成员。本轮按用户要求重新选择十个训练应用，已更新成员；既有数据 manifest 保留当时的 split 哈希，新导出记录新配置哈希。

旧版 `scripts/classify_sft_cases.py` 按全文关键词分类，会混合 target 与 preservation，例如将 Cockpit 的删除流程判入 A，也将 Akaunting 判为 B。本轮已替换为读取人工逐流程分类源并生成报告的脚本。
