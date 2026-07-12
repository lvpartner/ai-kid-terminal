# 开发交接

当前公开版是单家庭、中文、儿童 Kiosk 架构。默认运行路径为单轮内存音频缓存、Qwen ASR、
结构化/网页证据检索、DeepSeek JSON 答案、Claim Validator 和 CosyVoice TTS。
`qwen_realtime` 仅作为显式实验 Provider 保留，不是默认路径。

开发前执行：

```bash
git status -sb
git log -5 --oneline
make lint
make test
make android-test
```

必须保留用户工作区改动，不得读取或输出 `.env`。不得提交 API Key、设备令牌、真实儿童对话、
数据库、配对二维码、签名密钥或生产配置。发布前运行完整测试和历史密钥扫描。

当前提示词版本为 `family-zh-v1`。运行时只保留当前策略；历史设计变化记录在 Git 提交和
`CHANGELOG.md`，不进入运行时规则表。
