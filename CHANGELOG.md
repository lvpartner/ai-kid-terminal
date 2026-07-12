# Changelog

## 0.4.0

- Reuse bounded provider HTTP connection pools and validate every redirect before access.
- Require exact evidence spans for externally grounded claims before speech synthesis.
- Add explicit turn states and privacy-safe stage latency logging.
- Add Beta/Stable channels that promote the exact same signed APK without rebuilding.
- Persist legacy server configuration so future public and maintainer APKs are identical.
- Add the 60-question quality/performance release budget and GitHub CI.

## 0.3.0

- 重构为中文单家庭自托管架构。
- 默认移除 Qwen Realtime 长连接，改用独立 ASR、证据检索、结构化答案校验和 TTS。
- 增加 Claim/source_id 验证和设备能力边界。
- Android 收敛为单一 Kiosk 构建，支持运行时服务器配置和电量图标四击退出。
- 增加单容器 SQLite 安装器、配对二维码和开源安全/隐私文档。
