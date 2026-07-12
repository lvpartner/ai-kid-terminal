# 架构说明

```text
Android Kiosk
  └─ 家庭 WebSocket（PCM、控制、打断）
       └─ Turn Orchestrator
            ├─ SpeechRecognizer：Qwen ASR
            ├─ 能力、安全与歧义策略
            ├─ 结构化天气 / 官方来源 / 网页证据
            ├─ AnswerGenerator：DeepSeek JSON
            ├─ Claim Validator：数字、日期、价格、source_id
            └─ SpeechSynthesizer：CosyVoice
```

## 运行边界

手机只连接家长自建的后端，不持有模型 API Key。手机到后端使用一个可打断的 WebSocket；
后端不再为每台手机维持模型厂商 Realtime 长连接。每轮 PCM 只在内存中存在，转写完成后由
`BufferedAudioProvider` 清除。

`TurnOrchestrator` 决定是否需要最新证据。天气优先结构化接口；已登记主题使用受域名白名单、
大小、时效和内容标记约束的官方来源；其他外部事实通过搜索发现 URL，再由服务器独立抓取和
校验。网页文本不能直接控制模型工具或系统提示词。

答案模型返回 `answer`、`needs_clarification` 和 `claims`。每个精确外部声明必须引用本轮真实
存在的 `source_id`；未知来源、缺少证据、过长答案和不合格澄清会在 TTS 前失败关闭。

## 单家庭部署

`compose.family.yml` 只有一个 API 容器和 SQLite 数据卷，适合家庭电脑或 NAS。局域网地址允许
使用 RFC1918 HTTP；非局域网服务器必须使用 HTTPS。公网 TLS、反向代理和备份属于可选运维层，
不影响家庭版核心代码。

## Android Kiosk

只有一个 Android 构建。首次绑定前显示中文配置页；绑定后进入 Lock Task，可选设置为 Device
Owner 和默认桌面。服务器地址保存在应用私有配置中，设备访问令牌由 Android Keystore 加密。
3 秒内点击电量图标 4 次会停止 Lock Task、清除默认桌面绑定并退出应用。
