# Android 儿童锁定客户端

项目只发布 `com.aikid.terminal` 一个 Kiosk 构建。公开 APK 不嵌入服务器地址、绑定码或 API Key；
首次启动通过中文配置页或 `aikid://provision` 链接完成绑定。设备令牌使用 Android Keystore
AES-GCM 加密。

```bash
ANDROID_HOME=${ANDROID_HOME:-/opt/android-sdk} ./gradlew test lint assembleDebug
```

完全锁定需要在恢复出厂且未添加账号的专用手机上设置 Device Owner：

```bash
adb install app/build/outputs/apk/debug/app-debug.apk
adb shell dpm set-device-owner com.aikid.terminal/.KioskAdminReceiver
```

3 秒内连续点击左上角电量图标 4 次即可退出，不设 PIN。详细安装与风险说明见
[`docs/SELF_HOSTING.md`](../docs/SELF_HOSTING.md)。
