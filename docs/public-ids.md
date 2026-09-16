# 运单 Public ID 与 delivery 页面更新

## 调查与范围

部署使用 `HubDeploy/sources/HubBackend`、`HubDeploy/sources/HubFrontend`。
保留这两份工作区原有的 Trucky 时间修复、外部司机和历史同步改动；没有覆盖较旧的 `HubDev` 副本。

运单表是 `dlog`，主键 `logid INT AUTO_INCREMENT`；负 ID 是人工里程调整。
行政待办插件 `task` 不是运输任务，本次不改其编号。
数据库为 MariaDB 10.11，PyMySQL/aiomysql 手写 SQL，无 ORM/Alembic。
原有数据库初始化及升级入口是 `db.init`、`src/upgrades/manager.py`。
内部关联、统计游标、遥测、分部、挑战和旧 API 都继续使用整数 `logid`。

`tracker_type + trackerid` 已保存来源和原始 ID，因此不重复建立 source_system/legacy_id 字段。
没有对这个组合添加新的唯一约束，也没有假定人工调整的重复 `trackerid=-1` 唯一。
没有现存 CSV/Excel 通用写入器；今后的此类导入应调用 `insert_delivery`，显式提供真实业务日期，保留来源映射和去重规则。

用户确认最早记录是 **2025-09**。因此选择固定配置 **2020-01** 作为 Epoch，覆盖到 **2105-04**。
这不是从本地生产数据库查询得出的结论；迁移仍会逐条验证实际日期，不接受超出范围的数据。

## 算法与稳定性

- 格式：`TTXXXXXC`，8 位连续大写 Crockford Base32：`0123456789ABCDEFGHJKMNPQRSTVWXYZ`。
- TT：UTC 自然月相对 Epoch 的索引；8 轮、5+5 bit Feistel 置换；轮函数为带 domain separation 的 HMAC-SHA256，取 5 bit。1024 个索引构成双射，不需要月份映射表。
- 主体：`secrets.randbits(25)`，5 字符 CSPRNG。选择它是为了在 INSERT 前得到完整编号，满足 NOT NULL，避免预分配自增主键或临时空编号。它不编码内部 ID。
- 校验：前七个字符数值乘以 `1,3,5,7,9,11,13` 后求和模 32。所有权重与 32 互质，因此检测所有单字符替换；不能检测所有换位或多字符错误。它不是鉴权机制，也不是 Crockford 扩展字符集的 mod-37 校验。
- 输入去除首尾空格并转大写，不接受 I/L/O/U 的模糊别名。
- `PublicIDs.encode_time_bucket()` / `decode_time_bucket()` 提供月份编解码；解码只返回 YYYY-MM。
- 已生成编号持久化，修改业务日期/车辆/货物不会重算。没有 force 重新编号入口。

同月空间为 33,554,432；出现生日碰撞是正常概率事件。数据库最终保证唯一，最多尝试 16 次。
每次重试读取新 CSPRNG 熵，不能简单重试同一截断值。

## 数据库与事务

`dlog` 和 `dlog_deleted` 同步增加：

```sql
public_id CHAR(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
imported_at BIGINT NULL
UNIQUE KEY uq_dlog_public_id (public_id)
```

原主键和所有 tracker IDs 保持不变。新导入的 imported_at 是接收/入库时间；旧数据未知的入库时间保持 NULL，不编造。
新 tracker 运单的 timestamp 使用标准化载荷中的 stop_time，代表业务完成/取消时间，列表用它排序。
历史回填直接读取保存的 `data.object.stop_time`，不会误用旧版本可能存为接收时间的 timestamp。
只有负 ID 的人工里程调整明确使用原 timestamp 作为实际调整发生时间。

`public_id_registry` 永久保留已分配编号，删除运单后也不释放，避免旧 URL 被复用。
`public_id_config` 保存 key+Epoch 的指纹，启动和迁移检测配置误换。
注册编号与插入 dlog 使用同一连接、同一事务和 SAVEPOINT；只有 `uq_dlog_public_id` 的 1062 冲突会重试，其他约束错误直接抛出。
tracker 路径在创建 dlog 与 metadata 后一起提交；在开始后续通知/奖励前保证运单已持久化。
迁移要求 InnoDB，并在修改前检查归档/主表的重复 logid。

## 配置与迁移

部署前备份数据库和 `.env`。在安全终端生成一次密钥：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

将输出保存为 `.env` 的 `VTCHUB_PUBLIC_ID_TIME_KEY`，并设置：

```dotenv
VTCHUB_PUBLIC_ID_EPOCH=2020-01
```

不要提交或每次启动重新生成密钥。所有实例使用同一固定 key/Epoch。代码没有默认 secret。
Compose 已将配置传给 backend 和 backend-init；缺失/过短 key、缺失 Epoch、配置指纹变化会明确失败。

**已有安装，所有写入者停机后迁移**（包括额外 worker、手动导入脚本）：

```bash
docker compose stop backend
docker compose build backend frontend
docker compose run --rm --no-deps backend python /app/src/public_id_migration.py --dry-run --batch-size 500
docker compose run --rm --no-deps backend python /app/src/public_id_migration.py --apply --batch-size 500
docker compose up -d --build
```

数据库服务必须已运行。`--dry-run` 默认只读，不加列、不注册 key、不保存候选 ID。
先列出可迁移日期范围、建议 Epoch、总数/已有/可生成/缺失日期/冲突/错误数量。
干运行的 collision 数量为 0，因为它不试占数据库唯一键；实际 apply 才统计发生的碰撞。
缺失/损坏业务日期的记录按行输出 JSON warning，可重定向日志保存 unresolved 清单。
应从来源修复这些日期后重跑，不要填写当前日期。

apply 分阶段执行：添加可空列及唯一索引 → 每批 500 条、以 logid 游标分页补齐 → 全部解决后设为 NOT NULL。
MariaDB DDL 隐式提交，所以 schema 阶段不承诺整体事务回滚；各阶段可重跑。
每批提交，崩溃后已提交的编号不会变化；未提交候选值可能变化，但尚未被公开。
已有非空编号不重算，已有非法格式会报错并阻止完成约束收紧。
有 unresolved/errors 时退出码为 1，backend 拒绝在尚可空的 schema 上启动。
成功后退出码为 0，再恢复服务。没有在此次开发中运行生产迁移。

新安装设置固定配置后照常运行 backend-init，空表直接建立最终约束。
需要回滚应用时，应同步回滚数据库备份；旧写入器不知道 NOT NULL 新列，不能直接混用。
SQL 导入和手工 INSERT 也必须提供有效编号、完成 registry 保留；优先使用统一写入函数。

## API、页面与兼容性

- 旧 `GET/DELETE /dlog/{整数}`、分部/挑战等接口保持不变。
- 新 `GET /dlog/public/{public_id}`；坏 checksum 返回 400，合法但不存在返回原有 404。
- 新路由解析后进入原 get_dlog，同样的认证、privacy、限流和详情行为；Public ID 不授予访问权限。
- 独立 public 路径避免合法的全数字 Public ID 与旧数字 ID 歧义。
- 列表/详情返回额外 `public_id`，保留 numeric `logid`；CSV 导出末尾追加 public_id，保留原列位置和 legacy ID。
- 前端使用 `/delivery/public/{public_id}`；旧 `/delivery/{整数}` 仍可用。列表、用户卡运单列表、详情标题和新 Discord 运单消息显示 Public ID。
- 删除、轨迹刷新和分部操作仍使用详情解析出的内部 ID。
- 列表提供 Public ID 查找；“筛选与排序”支持出发城市/公司、目的城市/公司和货物名称的字面子串筛选，可组合。
- 司机保留注册用户选择器和 Trucky Steam ID 选择器，包含未注册 Hub 的司机。两种选择互斥，地点与货物筛选可与其组合。
- 默认 `timestamp DESC, logid DESC`，支持利润列点击排序和设置中利润/时间排序，翻页总数使用相同筛选条件。旧缓存默认排序会一次性迁移为时间倒序。
- 利润按已有数值排序，不做欧元/美元汇率换算；比较同币种时使用游戏筛选。
- 详情主页面显示卡车、全部挂车的品牌/型号和车牌；缺失信息显示 unknown/破折号，不构造不存在的车辆。

## 验证命令

```bash
python -m unittest discover -s tests
ruff check src/public_ids.py src/public_id_store.py src/public_id_migration.py src/delivery_filters.py tests/test_public_ids.py tests/test_delivery_api.py
npm ci
npm test -- --run
npx vite build
```

本次验证：37 项后端测试、2 项前端组件测试通过；104 个 Python 模块编译通过；修改后 Python 文件的 Ruff 检查、前端针对未定义变量/重复键/不可达代码的 ESLint 检查、完整 Vite 构建通过。前端使用独立 C 盘验证副本，项目源码保存在原路径。

Python 测试包括真实 SQLite 唯一约束下的回填、MariaDB 1062 重试行为的注入测试，以及 API 查询/兼容路径测试。
SQLite 适配层只验证回填 DML；它不代替 MariaDB DDL/真实多连接并发验证。
本机 Docker daemon 不可用、未连接生产数据库，因此仍需要部署环境执行 dry-run 和迁移，并在测试库验证 MariaDB DDL。
项目无已配置的 TypeScript 检查；本次使用 Python lint/编译、前端组件测试和完整 Vite 构建。

## 修改文件

Backend 新增 `src/public_ids.py`、`src/public_id_store.py`、`src/public_id_migration.py`、`src/delivery_filters.py`、`tests/test_public_ids.py`、`tests/test_delivery_api.py` 和本文档。
修改 `src/db.py`、`src/api.py`、`src/functions/tracker.py`、`src/apis/member/manage.py`、`src/apis/dlog/{info,export,__init__}.py`、`tests/test_trucky_timestamp.py`、`openapi.json`。
Frontend 修改 `src/routes/delivery-list.js`、`src/routes/delivery.js`、`src/App.js`、`src/context.js`、`src/components/usercard.js`、`src/languages/{en,zh}.json`、`package.json`、`package-lock.json`；新增车辆组件及其测试。
Deploy 修改 `compose.yaml`、`.env.cloud.example`、`.env.oci.example`。

## 20 个测试生成的示例

以下使用独立测试 key；不是生产编号，不会写入数据库。实际 TT 由部署 key 决定。

2025-09：

```text
H5K8N5NW H50NRMKY H5SAHPK5 H547J4YS H5TG7C0N
H5C3J2GS H5MJTE9V H51CP8YX H5C148BE H5P1CEAX
```

2026-09：

```text
G4E4FDRC G4TPQERS G4J537XT G4GQCN1D G4C3CQZ9
G494CZDF G4WD6X3Z G48CEPMC G4SJQ3TS G40Q63HH
```
