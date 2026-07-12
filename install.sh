#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077

command -v docker >/dev/null || {
  echo "未找到 Docker，请先安装 Docker Desktop 或 Docker Engine。" >&2
  exit 1
}
docker compose version >/dev/null

if [[ -f .env ]]; then
  echo "检测到现有 .env，为避免覆盖密钥，安装器已停止。" >&2
  echo "如需全新安装，请先自行备份并移走 .env。" >&2
  exit 1
fi

read -r -s -p "请输入 DashScope API Key（用于中文语音识别、搜索和朗读）: " dashscope_key
echo
read -r -s -p "请输入 DeepSeek API Key（用于生成答案）: " deepseek_key
echo
[[ -n "$dashscope_key" && -n "$deepseek_key" ]] || {
  echo "两个 API Key 都不能为空。" >&2
  exit 1
}

admin_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
pepper="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
cat >.env <<EOF
ENVIRONMENT=production
DATABASE_URL=sqlite+aiosqlite:////app/data/kid-terminal.db
ADMIN_API_KEY=$admin_key
TOKEN_PEPPER=$pepper
AI_PROVIDER=hybrid
DASHSCOPE_API_KEY=$dashscope_key
QWEN_WORKSPACE_ID=
QWEN_REGION=cn-beijing
GROUNDED_MODEL=qwen3.5-plus
DEEPSEEK_API_KEY=$deepseek_key
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
COSYVOICE_MODEL=cosyvoice-v3-flash
COSYVOICE_VOICE=longanyang
STRICT_GROUNDING=true
CONTEXT_TURNS=8
ENABLE_LONG_TERM_MEMORY=false
CONVERSATION_RETENTION_HOURS=24
TELEMETRY_RETENTION_DAYS=7
LOG_LEVEL=INFO
FAMILY_BIND_HOST=0.0.0.0
FAMILY_PORT=8000
EOF
chmod 600 .env
docker compose -f compose.family.yml build
docker compose -f compose.family.yml up -d

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:8000/health/ready >/dev/null; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/health/ready >/dev/null || {
  echo "服务未能通过健康检查，请运行 docker compose -f compose.family.yml logs。" >&2
  exit 1
}

response="$(curl -fsS -X POST http://127.0.0.1:8000/v1/admin/enrollments \
  -H "X-Admin-Key: $admin_key" -H 'Content-Type: application/json' \
  --data '{"label":"family-phone","expires_minutes":1440}')"
token="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["enrollment_token"])' <<<"$response")"
lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$lan_ip" ]] || lan_ip="127.0.0.1"
server="http://$lan_ip:8000"
pairing_url="$(SERVER="$server" TOKEN="$token" python3 -c \
  'import os,urllib.parse; print("aikid://provision?"+urllib.parse.urlencode({"server":os.environ["SERVER"],"token":os.environ["TOKEN"]}))')"
printf '%s' "$pairing_url" | docker compose -f compose.family.yml run --rm -T api \
  python -m kid_terminal.pairing - >pairing.svg
chmod 600 pairing.svg

echo
echo "家庭服务器已启动：$server"
echo "绑定二维码已生成：$PWD/pairing.svg"
echo "手机和服务器应连接同一 Wi-Fi。绑定码 24 小时后失效。"
