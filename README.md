# 儿童 AI 终端

把一台闲置 Android 手机变成仅供孩子使用的中文语音问答终端。家长在自己的电脑、NAS
或服务器上运行单家庭后端，API Key 只保存在后端，不写入 APK，也不需要 SIM 卡或域名。

## 特点

- 仅中文、仅儿童锁定模式，支持按住说话和随时打断。
- 单轮音频只缓存在内存；默认不保存原始录音。
- 独立 ASR → 可信资料检索 → DeepSeek 结构化答案 → Claim 校验 → TTS。
- 天气使用结构化接口；时效、价格和参数问题必须取得当轮证据，否则明确不猜。
- 歌曲播放等未实现能力由确定性规则拦截，不让模型假装已经执行。
- 家庭局域网可直接使用，不需要域名；公网部署可自行增加 HTTPS 反向代理。
- 电量图标 3 秒内连续点击 4 次，退出儿童模式，不需要 PIN。

## 五分钟自建

需要 Python 3、Docker Compose、可用的 DashScope 与 DeepSeek API Key：

```bash
git clone https://github.com/lvpartner/ai-kid-terminal.git
cd ai-kid-terminal
./install.sh
```

安装器会隐藏读取两个 Key、生成服务器密钥、启动单容器 SQLite 后端、创建一次性绑定码，
并输出 `pairing.svg`。手机与后端连接同一个 Wi-Fi 后，安装 Releases 中的 APK，扫描绑定
二维码或在首次启动页手工输入服务器地址与绑定码。

详细步骤见 [自建与手机安装](docs/SELF_HOSTING.md)，系统设计见
[架构说明](docs/ARCHITECTURE.md)，量化门槛见 [回答质量与性能](docs/PERFORMANCE.md)。
六项结构与性能改造及过渡提示策略见 [优化清单](docs/OPTIMIZATIONS.md)。

## 开发

要求 Python 3.12 和 Android SDK：

```bash
make setup
make lint
make test
make android-test
```

本地测试默认使用 Mock Provider，不调用外部模型。真实 API 测试必须显式开启，且不得输出
密钥、录音或儿童问题全文。

## 隐私与安全

这是供家长自建的家庭工具，不是医疗、应急或专业建议服务。请阅读
[PRIVACY.md](PRIVACY.md) 与 [SECURITY.md](SECURITY.md)。发现漏洞请勿公开披露细节。

## 许可证

代码使用 [Apache License 2.0](LICENSE)。第三方服务、模型输出和外部资料仍受各自条款约束。
