# 自建与手机安装

## 需要准备

- 一台长期联网的电脑、NAS 或 Linux 小主机。
- Docker Engine 或 Docker Desktop，支持 `docker compose`。
- DashScope API Key：中文语音识别、联网搜索和语音合成。
- DeepSeek API Key：基于证据生成最终中文答案。
- Android 8.0 或更高版本旧手机，以及可上网的 Wi-Fi。手机不需要 SIM 卡。

## 启动家庭服务器

```bash
git clone https://github.com/lvpartner/ai-kid-terminal.git
cd ai-kid-terminal
./install.sh
```

安装器只在终端中隐藏读取一次 API Key，并写入权限为 `0600` 的 `.env`。它不会上传或打印 Key。
完成后会显示局域网地址并生成 `pairing.svg`。不要把 `.env` 或配对二维码发给别人。

检查服务：

```bash
curl http://127.0.0.1:8000/health/ready
docker compose -f compose.family.yml ps
```

停止或更新：

```bash
docker compose -f compose.family.yml down
git pull --ff-only
docker compose -f compose.family.yml up -d --build
```

## 安装手机

1. 从 GitHub Releases 下载 APK，并核对发布页 SHA-256。
2. 允许系统安装这个 APK。
3. 手机与家庭服务器连接同一个 Wi-Fi。
4. 首次启动时输入安装器显示的 `http://局域网IP:8000` 和一次性绑定码；也可以用二维码扫描器
   打开 `pairing.svg` 中的配置链接。
5. 授予麦克风权限，按住屏幕中央按钮提问。

HTTP 只允许 `localhost`、`.local` 和 RFC1918 局域网地址。公网域名必须使用 HTTPS。

## 完全锁定为儿童手机

普通 Lock Task 会由 Android 显示一次确认。若要获得完整 Kiosk 效果，手机必须是刚恢复出厂、
尚未添加账号的专用设备，然后通过 ADB 设置 Device Owner：

```bash
adb shell dpm set-device-owner com.aikid.terminal/.KioskAdminReceiver
```

不同厂商可能还需允许开机自启并关闭针对本应用的省电限制。执行 Device Owner 前请确认这是一台
专供孩子使用的旧手机；恢复普通用途最稳妥的方法是先四击退出，再卸载或恢复出厂设置。

## 家长退出

在 3 秒内连续点击左上角电量图标 4 次。应用会停止锁定、恢复状态栏和锁屏、清除默认桌面绑定，
然后退出。此版本按项目要求不设置 PIN，因此孩子如果学会该手势也能退出。

## 数据与备份

原始录音不落盘。设备资料、短期对话和运行状态位于 Docker `family_data` 卷。删除数据前请先
停止服务并自行备份；家长也可调用管理接口删除指定设备数据。默认不要把数据库同步到公共云盘。

## 常见问题

- 手机无法连接：确认两台设备在同一 Wi-Fi，路由器没有开启客户端隔离，电脑防火墙允许 TCP 8000。
- 地址变化：给后端设备设置 DHCP 固定租约，然后在手机清除应用数据并重新绑定。
- 回答不了最新事实：检查两个 API 账户额度和服务器网络；系统会在证据不足时主动拒绝猜测。
- 不需要域名：家庭 Wi-Fi 模式直接使用局域网 IP。只有跨互联网连接时才需要 HTTPS 域名或 VPN。
