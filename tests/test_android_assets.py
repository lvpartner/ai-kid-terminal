from pathlib import Path


def test_android_does_not_bundle_generated_voice_prompts() -> None:
    assert not list(Path("android/app/src/main/res").glob("raw/*.ulaw"))


def test_waiting_indicator_is_visual_and_semantically_neutral() -> None:
    source = Path("android/app/src/main/java/com/aikid/terminal/MainActivity.kt").read_text()
    waiting_block = source.split('"ai.response.started" -> {', 1)[1].split('"ai.response.done"', 1)[
        0
    ]
    assert "statusText.text = getString(R.string.thinking)" in waiting_block
    assert "audio.play" not in waiting_block
    assert "waitingPrompts" not in source
    strings = Path("android/app/src/main/res/values/strings.xml").read_text()
    assert '<string name="thinking">我想一想…</string>' in strings
