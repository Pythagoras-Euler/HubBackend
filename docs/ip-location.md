# IP 位置

此前实现只读取 `CF-IPCountry`。OCI 经 Caddy 直接访问，没有这个请求头时所有公网会话都存成 `XX`；项目原先并没有本地 IP 地址库。

现在使用本地 DB-IP City Lite MMDB 查询 IPv4/IPv6。会话列表及登录通知显示国家与首级行政区（省／州），不显示更细的城市和坐标；国家码继续用于既有会话安全检查。会话表不增加省份字段，按记录中的 IP 在展示时查询，因此已有会话也能显示省份。无法定位显示 `-*-*-`，缺省份时仅显示国家。内网地址单独显示为本地网络。

地址库数据是网络出口位置，VPN、移动网络和运营商出口可能与人的所在地不同。Lite 版覆盖及精度有限，不保证每个 IP 都有省级结果。

## 安装与更新

Docker 镜像构建时下载当月库，当月尚未可用则尝试上月；验证 IPv4/IPv6 查询后原子替换。数据库下载失败会阻止新镜像构建，现有服务不受影响。IP 查询仅访问本地文件，不将用户 IP 发到第三方。默认路径 `/app/geoip/city.mmdb`，非 Docker 安装可用 `GEOIP_DATABASE` 指定。

更新到指定月份：`docker compose -f compose.yaml -f compose.oci.yaml build --build-arg GEOIP_MONTH=YYYY-MM backend`，完成后重建 backend 容器。建议每月更新，镜像中保存的数据库在重启时不会重新下载。

默认忽略客户端传入的 `CF-IPCountry`；只有在代理会清除伪造头、且来源可信时才启用 `TRUST_CF_IPCOUNTRY=true`。

数据来源：[DB-IP City Lite](https://db-ip.com/db/download/ip-to-city-lite)，[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。网页页脚保留来源链接。数据未经修改，仅展示国家及首级行政区。
