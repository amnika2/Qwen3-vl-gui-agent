# 真实应用与实例生命周期

五例均取自固定 revision 的 SWEeper-Bench，base/reference 提交见 [index.csv](index.csv)。`public.json` 是模型输入；官方 workflow、版本映射和合成测试账户保留在本地 `private.json`、`fixtures.private.json`，不会传给模型。

| Case | 应用 | target | preservation | 运行时 |
|---|---|---|---|---|
| sweeper-005 | Akaunting | 创建商品，输入本地化金额，检查已保存的同一行价格 | 普通金额创建商品 | PHP 8.3 / MariaDB |
| sweeper-040 | Easy!Appointments | 保存 Jane Doe 手机号，重开同一人员 | 保存电话号码 | PHP 8.3 / MariaDB |
| sweeper-041 | emlog | 作者账户仅改昵称，重开核对 | 管理员账户修改昵称 | PHP 8.3 / MariaDB |
| sweeper-007 | Ampache | 重命名私有 Smart Playlist ID 20，重开列表 | 搜索保存为新 Smart Playlist | PHP 8.3 / MariaDB |
| sweeper-008 | Anchor CMS | 修改已有 greeting 值，重开同一变量 | 创建 hoursnote 变量 | PHP 7.4 / MariaDB |

每次 `run` 自动完成 up → health → 登录 fixture → 业务测试 → 导出证据 → down。新实例就是 reset：新的应用副本、数据库、socket、会话和缓存。服务只绑定 `127.0.0.1`；数据库只开放实例专属 Unix socket。应用临时目录位于执行主机 `/tmp/guiqa-*`，关闭后清理，证据保存在 `runs/<id>`。

```bash
.venv/bin/python -m guiqa calibrate --case sweeper-007 --port 13207
.venv/bin/python -m guiqa run --case sweeper-008 --build A --workflow target --method script --port 13208
.venv/bin/python -m guiqa run --case sweeper-008 --build B --workflow target --method script --port 13208
```

校准每例执行两版本 × 两 workflow × 两次。预期仅 A/target 为 fail，其余为 pass。上述两个独立 `run` 返回码依次为 1、0；`calibrate` 的四格全部符合预期时返回 0。

业务初值和目标值依据官方 workflow 构造，使用合成内容。登录作为中性起点准备；模型只操作已认证页面。保存必须有本次真实提交，并在重开后看见同一对象的值，才能取得有效终局证据。

Ampache 使用管理员、私有智能播放列表与空规则；实例配置 `access_control=false` 允许测试回环访问，应用登录保持启用。前端产物包含 Vite 的 `public/dist`，两版本使用相同依赖和构建输入。

Anchor CMS 的旧错误处理器会把 PHP 7.4 启动时 `get_magic_quotes_gpc` 的废弃提示当作致命错误。实例通过 `auto_prepend_file` 安装启动阶段的废弃提示处理器；应用随后注册自身错误处理器。只在本次 PHP 进程关闭 `allow_url_fopen`，跳过已返回 404 的在线更新接口；测试变量编辑路由保持原样。Composer 在独立依赖目录把包类型 `CMS` 归一化为 `project`，安装原锁文件。环境配置对 A、B 一致。

五例通过的完整矩阵、逐次截图和真实值见 [M0 报告](../reports/milestone-M0.md)。构建、初始化、登录失败等历史记录均保留在各自运行目录。
