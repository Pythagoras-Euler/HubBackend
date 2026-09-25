# TMP 自动关联和头像来源

Hub 根据已绑定的 Steam ID 调用 TruckersMP player API，只有返回的 `steamID64` 与用户一致才写入 TMP ID。Steam 登录、重新绑定及后台任务均会触发检查。成功结果缓存 24 小时；临时故障不会清除原关联。更换 Steam 账号时先清除旧 TMP 关联，再验证新账号。

个人设置可独立选择 TruckersMP、Steam、Discord、外部图片链接或上传图片。选择头像不会更改昵称。原头像在用户选择新来源之前保持不变。前三种来源每天更新一次，失败时保留最后一张有效头像；外链只在手动保存时读取。

所有新头像均经过服务端检查并保存到本站：静态 PNG/JPEG/WebP、不超过 2 MiB、单边不超过 4096 像素、总像素不超过 800 万。图片重新解码、移除元数据并缩放为最多 512×512 的 PNG。每个用户只保存一张，写入需要登录，每分钟最多 5 次，同一账号不能并发更新；解码并发数为 2。

外链只允许公网 HTTPS 的默认端口，不接受凭据、内网地址、重定向或 HTTP 压缩响应。DNS 解析器验证实际连接的所有地址，避免 DNS 重绑定访问内网。下载采用超时和流式大小限制。上传不使用原始文件名；图片端点固定返回 PNG，并设置 `nosniff` 与严格 CSP。

Steam 来源需要已配置 Steam API key；Discord 来源需要已绑定 Discord 以及有效的机器人 API 配置。提供方故障或图片不符合限制时，页面显示失败，原头像继续使用。

部署前运行 `tracker_schema.prepare` 的增量迁移，创建 `avatar_profile`。图片与来源保存在 MariaDB，随数据库备份。API：`GET /user/avatar`、`PUT /user/avatar/{source}`、`GET /avatar/{uid}/{digest}.png`；以上路径位于配置的 API 前缀下，上传请求体为原始图片字节，外链请求体为 `{"url":"https://..."}`。
