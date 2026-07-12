# 贡献指南

提交代码前请阅读 `AGENTS.md`，使用 Python 3.12，并运行：

```bash
make lint
make test
make android-test
```

新增认证、协议、隐私、数据保留或 Provider 行为必须包含回归测试。外部 AI 默认使用 Mock；真实
服务测试必须显式开启且不得输出凭据。不要提交 `.env`、数据库、APK 签名材料、配对二维码、真实
儿童对话、录音或生产日志。

提交信息使用简洁的 Conventional Commit。Pull Request 应说明问题、安全与隐私影响、验证命令、
新增环境变量和回滚方式。
