from pathlib import Path


def test_android_does_not_bundle_generated_voice_prompts() -> None:
    assert not list(Path("android/app/src/main/res").glob("raw/*.ulaw"))
