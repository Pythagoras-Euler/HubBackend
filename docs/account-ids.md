# 可修改的个人公开 ID（本地第一版）

## 规则

- 4–6 位 ASCII 字母或数字，可纯字母或纯数字；不区分大小写，统一大写，保留前导零。
- 老账号迁移、新账号注册默认分配安全随机的 4 位字符串；数据库唯一索引和命名空间事务锁保证唯一性。
- 默认分配不计入改号次数。首次可立即自定义；每次成功修改后冷却 6 个自然月，按 UTC 日历计算，月末截断到目标月最后一天。
- 管理员可以授予一次冷却豁免。失败提交不消耗豁免；成功修改后消耗并重新计算冷却，不能累计多次豁免。
- 修改成功后旧公开 ID 立即停止解析，保留占用连续 72 小时；到期后可由任何账号重新申请。复用后相同 `/u/ID` 地址会属于新持有者。
- `/member/123` 前端个人主页路由已删除，不跳转；部署用 Nginx 模板也对旧数字主页直接返回 HTTP 404。个人主页仅使用 `/u/AB12`，只解析当前 ID，不做历史别名查找。
- 使用记录永久保存在私有后台；公开查询、页面、搜索和 API 不提供历史映射。
- 禁用词支持完全匹配和包含匹配、启用/停用、编辑、删除和内部备注。随机生成和用户修改使用同一词表。保存前可预览已有命中账号；新规则不自动改号。

## 数据与接口边界

`user.uid`、数字 `user.userid` 及运单/积分/资产/权限关联均保持稳定。新增六张 InnoDB 表：`account_id_lock`、`account_id`、`account_id_claim`、`account_id_history`、`account_id_audit`、`account_id_rule`。

现有业务接口保留 `userid` 字段名称，但外部值改为当前公开 ID 字符串。`AccountIDRoute` 在 FastAPI 参数验证前将路径、查询和已声明的 JSON 用户引用转换成内部数字编号，返回时转换回当前公开 ID。角色编号、运单编号及第三方平台原始编号不变。负数仍为系统/经济实体，不代表个人账号。旧正整数会员编号不作为兼容入口接受。

用户对象另外返回 `public_user_id`，包括尚未成为会员的账号。技术账号主键 `uid` 沿用现有接口含义，不作为个人 ID 展示。普通响应不会增加 ID 历史字段。

Tracker webhook 入口保留上游协议；Tracker 管理接口、运单导出、交易导出、个人横幅、模板公开 ID 和新运单通知链接已适配。旧的已发送外部消息和已下载文件不回溯改写。个人资料/身份响应与横幅不缓存，避免改号后继续使用旧映射；更新前已经缓存的旧资源须等待原缓存过期或清理。

新增接口（均使用现有 Bearer 登录和限流）：

| 路径 | 方法 | 用途 |
| --- | --- | --- |
| `/user/account-id` | GET / PATCH | 查询自己的状态；提交 `{public_id}` 修改自己 |
| `/user/account-id/availability?public_id=AB12` | GET | 格式、禁用词和占用检查；最终仍以事务提交结果为准 |
| `/user/profile?public_user_id=AB12` | GET | 按当前公开 ID 查询，沿用原资料隐私规则 |
| `/admin/account-ids/status?uid=1` | GET | 查询指定账号冷却状态 |
| `/admin/account-ids/history?uid=1&public_id=AB12&page=1` | GET | 账号/历史 ID 过滤，分页查询使用记录与当前持有者；筛选均可省略 |
| `/admin/account-ids/audit?uid=1&page=1` | GET | 查询管理员操作与历史查询审计 |
| `/admin/account-ids/credit` | POST | `{uid,reason}`，授予一次修改豁免 |
| `/admin/account-ids/rules` | GET / PUT | 查询/新增/编辑规则；PUT 字段为 `{id?,word,mode,enabled,note}` |
| `/admin/account-ids/rules?id=1` | DELETE | 删除规则 |
| `/admin/account-ids/preview` | POST | `{word,mode}`，返回命中总数及最多 100 个已有账号 |

后台三个独立权限：`view_account_id_history`（记录和审计）、`manage_account_ids`（状态和豁免）、`manage_account_id_rules`（词表和预览）。`administrator` 均可使用。额外权限默认不授予任何角色，按原配置 `perms` 机制填写角色编号。

查询历史/审计、改号、豁免、词表变更和账号删除均记录在私有 ID 审计表中。迁移时原数字会员编号也以 `legacy` 来源保存到后台历史，可按原数字搜索，公开接口不解析它们。无法确认的旧编号启用时间留空。账号删除会结束 ID 使用并保留占用 72 小时，历史不随账号删除。

## 页面入口

- 设置 → 个人资料 → 折叠的“账号标识”：查看状态、检查可用性、确认修改。
- 设置 → 个人资料 → 折叠的“更多管理 · ID 管理”：后台记录、词表、审计；冷却操作还需展开“冷却管理”。
- 有权限的用户也可从用户卡片原有“编辑资料”弹窗进入折叠的 ID 管理。
- 主页、成员列表不增加 ID 操作按钮。修改成功后重新载入个人资料设置，清除浏览器内旧用户映射。

## 迁移与回滚

本次只进行了本地测试，未迁移业务库、未部署、未推送。

1. 备份数据库及当前前后端版本；停止所有写入进程，包括后台同步。
2. 通过环境变量配置目标数据库 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`，不要把密码写入命令历史或提交到仓库。
3. 在后端目录执行 `python src/account_id_migration.py --dry-run` 查看账号数量。
4. 执行 `python src/account_id_migration.py --apply`。创建表并逐账号事务回填，可中断后重跑，不改数字关联字段。
5. 启动前后端匹配版本；后端启动检查未分配 ID 的账号，不完整时阻止启动。
6. 检查登录、资料查询、改号、成员筛选、运单/积分/经济操作和 Tracker 管理。自定义前端链接模板改为 `/u/{userid}`；默认及两种常见旧模板已适配。

回滚时停止写入并恢复上一版前后端；原数字业务数据未重编号，新表可保留。再次启用本版应继续使用现有新表与完整历史，不能重新随机编号。回滚旧前端会重新显示旧编号，仅作为人工运维回滚手段，不能与新版并行对外服务。

## 本地验证

2026-10-31 审查修复：修正逗号分隔用户引用的异步转换；CLI `create-user` 同事务分配公开 ID，失败回滚。独立 MariaDB 回归 200 项通过（含新增 CLI 成功/回滚和列表转换验证）。前端复验 20 项通过、生产构建通过；本机 Node 18 有 Vite 版本要求警告，发布应使用部署镜像指定的 Node 24。

核心测试使用独立 MariaDB 10.11.14，连接仅限 `127.0.0.1:13316`。每个集成测试建立并删除随机前缀为 `hub_account_id_test_` 的测试库，不读取应用数据库配置。

```powershell
$env:HUB_ACCOUNT_ID_TEST_DB = '1'
python -m unittest discover -s tests -p test_account_ids.py -v
```

默认不开启数据库集成测试时，只执行格式和日历/词表策略测试。可通过 `HUB_ACCOUNT_ID_TEST_PORT`、`HUB_ACCOUNT_ID_TEST_PASSWORD` 指定独立测试实例。

前端在其目录执行 `node node_modules/vitest/vitest.mjs run` 和 `node node_modules/vite/bin/vite.js build`。开发服务器的 `/scripts/account-id-preview.html` 为独立组件交互测试夹具，使用模拟 API，不是生产构建入口。后端集成测试使用真实 MariaDB，但认证及旧业务接口外壳采用受控夹具；不代表已验证真实 OAuth、Discord 或线上 Tracker。

2026-10-31 最终本地验证：后端完整回归 **199 项通过**（含真实 MariaDB 10.11.14 的迁移、并发抢号、冷却/豁免、72 小时边界、私有记录权限和删除保留测试）；前端 **20 项通过**，Vite 生产构建通过。浏览器已验证折叠入口、可用性、改号确认及刷新、后台记录、豁免、禁用词 CRUD、审计，以及 1180px / 390px 布局，浏览器交互使用模拟 API。Nginx 模板已修改，尚未通过容器启动验证。
