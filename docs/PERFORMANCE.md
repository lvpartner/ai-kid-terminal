# 回答质量与性能门槛

发布候选必须使用 `scripts/compare_fact_routes.py` 的至少 50 道中文事实题测试。题目覆盖科学、
地理、历史、文化、技术、生物、健康、汽车参数和时效事实；两条路线使用相同的合成中文语音，
再盲评准确性、可用性、拒答和无证据精确声明。

当前默认路线是手机 PCM → Qwen HTTP ASR → 证据检索 → DeepSeek 结构化答案 → Claim 校验 →
CosyVoice。对照路线是独立的“转文字后联网问模型”流程。最近一次 60 题基线中，默认路线平均
准确度 4.30/5、首段语音 P50 1.466 秒、P95 4.784 秒、无证据精确声明 0 条；对照路线平均
准确度 4.867/5，但首段 P50 7.571 秒、P95 12.118 秒，并出现 9 条无证据精确声明。因此默认
保留可验证路线，不用更快回答换取编造风险，也不把厂商 Realtime 长连接作为默认依赖。

预算定义在 `evaluations/performance-budget.json`。发布前运行：

```bash
python scripts/check_performance_budget.py evaluations/<run>/report.json
```

任何优化相对上一个稳定版的关键延迟不得退化超过 10%；准确率、完成率和无证据声明任一项不达标，
只能留在 Beta，不能晋升 Stable。报告可保留匿名统计，原始儿童问题和录音不得提交 Git。
