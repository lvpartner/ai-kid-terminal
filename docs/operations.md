# 运维

家庭版使用 `compose.family.yml`、单 API 容器和 SQLite 数据卷。安装、更新、健康检查与手机绑定见
[SELF_HOSTING.md](SELF_HOSTING.md)。不要把 TCP 8000 转发到公网。

开发或高级部署可使用根目录 `docker-compose.yml` 的 PostgreSQL 组合。公网部署必须设置自己的
`TLS_HOST`，再使用可选 Caddy profile；仓库不包含任何生产域名、邮箱或云厂商元数据依赖。

部署前运行 `make release-gate`。数据库迁移前先备份，并定期在临时实例做恢复演练。日志只保留
脱敏的请求和故障元数据，不记录录音、完整问题、密钥或访问令牌。
