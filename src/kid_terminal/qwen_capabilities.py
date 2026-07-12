QWEN35_BUILTIN_VOICES = {
    "Tina",
    "Cindy",
    "Liora Mira",
    "Sunnybobi",
    "Raymond",
    "Ethan",
}


def qwen_voice(model: str, requested: str) -> str:
    if model.startswith("qwen3.5-") and requested not in QWEN35_BUILTIN_VOICES:
        return "Ethan"
    return requested
