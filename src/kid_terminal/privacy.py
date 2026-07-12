import re

PATTERNS = [
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号已隐藏]"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证号已隐藏]"),
    (re.compile(r"(?i)(密码|口令|password)\s*[:：是]?\s*\S+"), "[密码已隐藏]"),
    (re.compile(r"[\u4e00-\u9fff]{2,}(省|市|区|县).{0,20}(路|街|巷)\d{0,6}号?"), "[地址已隐藏]"),
]


def redact_private_text(text: str) -> str:
    clean = text
    for pattern, replacement in PATTERNS:
        clean = pattern.sub(replacement, clean)
    return clean[:4000]


def summarize_messages(messages: list[str], max_chars: int = 500) -> str:
    if not messages:
        return ""
    joined = "；".join(redact_private_text(item) for item in messages[-8:])
    return ("对话摘要：" + joined)[:max_chars]
