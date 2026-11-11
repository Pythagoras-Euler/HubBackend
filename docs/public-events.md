# 公开活动与 banner

现有接口新增只读字段 `banner_url: string | null`：

- `GET /api/events/list`：读取 `list[i].banner_url`。
- `GET /api/events/{eventid}`：读取根对象 `banner_url`。

`/api` 为部署配置的 prefix。无需 Authorization；匿名列表只包含公开活动，
匿名或非成员读取私有详情返回 404，成员访问规则与列表一致。
现有描述、其他响应字段、分页、筛选及置顶优先排序均保留。

## 独立主页调用

独立主页由网页服务器读取下列接口并缓存，再向浏览器提供数据；不需要浏览器跨域直连，
因此无需调整 Hub 的 CORS。以下 fetch 示例在主页服务器执行。建议缓存 5 分钟，
上游临时不可用时保留上次成功的数据。

```js
const hub = "https://<Hub 公网域名>";
const params = new URLSearchParams({
  page: "1",
  page_size: "6",
  meetup_after: String(Math.floor(Date.now() / 1000)),
  order_by: "meetup_timestamp",
  order: "asc",
});
const response = await fetch(`${hub}/api/events/list?${params}`, {
  credentials: "omit",
});
if (!response.ok) throw new Error(`Hub API: ${response.status}`);
const { list, total_items, total_pages } = await response.json();
// 卡片：title、banner_url、meetup_timestamp、departure_timestamp、departure、destination。
// banner_url === null 时不显示 banner，不替换为默认图片。
// Hub 详情链接：`${hub}/event/${event.eventid}`；link 是活动配置的外部链接。
// 详情 API：`${hub}/api/events/${event.eventid}`。
```

近期已结束活动可改用 `meetup_before=当前Unix秒` 和 `order=desc`，或使用
`meetup_after` / `meetup_before` 限定时间段。原有 `departure_after`、
`departure_before`、`created_after`、`created_before` 等参数仍然可用。
置顶活动仍优先于所选时间排序。时间戳单位是秒。

列表响应节选（其余原有字段省略）：

```json
{
  "list": [
    {
      "eventid": 123,
      "title": "车队联运",
      "description": "[Image src=\"/images/convoy.png\"]\n活动介绍",
      "banner_url": "https://hub.example.com/images/convoy.png",
      "is_private": false,
      "meetup_timestamp": 1800000000,
      "departure_timestamp": 1800003600
    },
    {
      "eventid": 124,
      "title": "无横幅活动",
      "description": "活动介绍",
      "banner_url": null,
      "is_private": false,
      "meetup_timestamp": 1800086400,
      "departure_timestamp": 1800090000
    }
  ],
  "total_items": 2,
  "total_pages": 1
}
```

## 兼容规则

读取时解析描述开头、大小写敏感的 `[Image src="..."]`；不修改数据库或要求重存。
只返回 HTTP/HTTPS 地址，拒绝缺少主机、无效端口、控制字符、凭据等无效地址。
相对路径以 `HUB_PUBLIC_URL` 为资源根地址；未配置时为 `https://{config.domain}/`，
与项目现有公共资源 URL 约定一致，不使用请求 Host 或独立主页域名。

后端 TruckersMP 同步已将远端 banner 写入相同描述标记，因此历史同步数据直接兼容。
历史活动若未保存该标记则返回 null；本接口不会请求 TruckersMP 猜测或补造图片。
Hub 前端优先使用 API 字段，只有旧服务器不返回字段时才解析旧描述；明确的 null 不回退。
详情弹窗和列表卡片共用同一处理。

验证：`python -m unittest discover -s tests -p test_event_banner.py -v`（Backend）；
`npx vitest run src/event-banner.test.js`、`npx vite build`（Frontend）。
后端测试隔离数据库、认证和限流依赖，执行真实接口函数及解析函数；不连接生产服务。
