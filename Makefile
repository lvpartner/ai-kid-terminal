SHELL := /bin/bash
DOCKER ?= docker
-include .env
export

.PHONY: setup lint test release-gate knowledge benchmark up down deploy-api migrate demo status logs backup diagnose maintain install-maintenance run qwen-check qwen-voice-e2e android-test android-release android-publish tls-check tls-up tls-down clean

setup:
	bash scripts/setup.sh

lint:
	.venv/bin/ruff format --check .
	.venv/bin/ruff check .
	.venv/bin/mypy src

test:
	.venv/bin/pytest -q

knowledge:
	.venv/bin/python scripts/build_curriculum_outlines.py
	.venv/bin/python scripts/build_knowledge_base.py

benchmark:
	.venv/bin/python scripts/run_answer_benchmark.py

release-gate:
	bash scripts/release_gate.sh

run:
	.venv/bin/uvicorn kid_terminal.app:app --host 127.0.0.1 --port 8000

up:
	$(DOCKER) compose up -d --build
	$(DOCKER) compose run --rm api alembic upgrade head

deploy-api:
	.venv/bin/python scripts/deploy_api.py

down:
	$(DOCKER) compose down

migrate:
	.venv/bin/alembic upgrade head

demo:
	.venv/bin/kid-simulator

status:
	python3 scripts/healthcheck.py

logs:
	$(DOCKER) compose logs --tail=200 api

backup:
	bash scripts/backup.sh

diagnose:
	bash scripts/diagnose.sh

maintain:
	bash scripts/maintenance.sh

install-maintenance:
	bash scripts/install_maintenance_timer.sh

qwen-check:
	.venv/bin/python scripts/qwen_check.py

qwen-voice-e2e:
	espeak-ng -v cmn -s 135 -w data/qwen-test-input.wav '你好，请用简单的话告诉我，一加一等于几。'
	sox data/qwen-test-input.wav -t raw -r 16000 -c 1 -b 16 -e signed-integer data/qwen-test-input.pcm
	.venv/bin/python scripts/qwen_voice_e2e.py

android-test:
	cd android && ANDROID_HOME=$${ANDROID_HOME:-/opt/android-sdk} ./gradlew test lint assembleDebug

android-release:
	bash scripts/build_android_release.sh $(VERSION_CODE) $(VERSION_NAME) $(BOOTSTRAP_TOKEN_FILE)

android-publish:
	.venv/bin/python scripts/publish_android_release.py \
		dist/ai-kid-terminal-$(VERSION_NAME).apk \
		--version-code $(VERSION_CODE) --version-name $(VERSION_NAME) \
		--notes "$(NOTES)" --rollout $(or $(ROLLOUT),100) --publish

tls-check:
	bash scripts/tls_preflight.sh

tls-up: tls-check
	$(DOCKER) compose --profile public up -d caddy

tls-down:
	$(DOCKER) compose --profile public stop caddy

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
