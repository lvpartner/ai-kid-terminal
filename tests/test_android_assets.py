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


def test_update_confirmation_exits_lock_task_via_foreground_activity() -> None:
    installer = Path("android/app/src/main/java/com/aikid/terminal/UpdateInstaller.kt").read_text()
    activity = Path("android/app/src/main/java/com/aikid/terminal/MainActivity.kt").read_text()
    assert ".putExtra(UPDATE_CONFIRMATION_EXTRA, confirmation)" in installer
    assert "Kiosk.exit(this)" in activity
    assert "updateConfirmation.launch(confirmation)" in activity
    assert "!updateConfirmationActive" in activity
