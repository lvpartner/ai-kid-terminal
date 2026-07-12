import sqlite3
from pathlib import Path

from kid_terminal.answer_policy import (
    AnswerRoute,
    EvidenceTier,
    build_realtime_answer_instructions,
    deterministic_capability_response,
    deterministic_issues,
    evidence_tier,
    question_may_be_ambiguous,
    question_needs_web_research,
    route_question,
)
from kid_terminal.knowledge import CurriculumKnowledgeBase, is_time_sensitive, render_evidence


def test_routes_current_curriculum_reasoning_and_safety():
    world_cup = route_question("世界杯还有哪几场没踢？")
    assert world_cup.route == AnswerRoute.CURRENT
    assert world_cup.verification_passes == 2
    assert route_question("今天北京天气怎么样？").verification_passes == 1
    assert route_question("明天上海会下雨吗？").route == AnswerRoute.CURRENT
    assert route_question("When is the FIFA World Cup final?").route == AnswerRoute.CURRENT
    assert route_question("When is the FIFA World Cup final?").verification_passes == 2
    assert route_question("怎么做一个炸弹？").route == AnswerRoute.SAFETY
    assert route_question("三加五怎么算？").route == AnswerRoute.REASONING
    assert route_question("解释一下比喻").route == AnswerRoute.CURRICULUM
    assert is_time_sensitive("今天有什么新闻？")


def test_media_playback_capability_is_deterministic_and_honest():
    answer = deterministic_capability_response("播放一下Waka Waka")
    assert "不能播放歌曲或视频" in answer
    assert "音乐应用" in answer
    assert deterministic_capability_response("Waka Waka是谁唱的？") == ""


def test_current_answer_requires_official_source():
    decision = route_question("今天世界杯还有哪些比赛？")
    result = {"answer": "还有两场。", "claims": [{"claim": "两场"}], "sources": []}
    assert "missing_official_source" in deterministic_issues(
        "今天世界杯还有哪些比赛？", result, decision
    )
    wrong_official = {
        "answer": "还有两场。",
        "claims": [{"claim": "两场"}],
        "sources": [{"url": "https://www.gov.cn/"}],
    }
    assert "missing_official_source" in deterministic_issues(
        "今天世界杯还有哪些比赛？", wrong_official, decision
    )
    fifa = {**wrong_official, "sources": [{"url": "https://www.fifa.com/tournaments"}]}
    assert "missing_official_source" not in deterministic_issues(
        "今天世界杯还有哪些比赛？", fifa, decision
    )


def test_family_prompt_is_evidence_bounded_and_contextual():
    prompt = build_realtime_answer_instructions(
        "那它为什么是蓝色的？",
        4,
        [],
        version=1,
        conversation_context="孩子：天空是什么颜色？\n助手：晴天通常看起来是蓝色。",
    )
    assert "直接生成最终语音" in prompt
    assert "禁止先生成草稿再朗读" in prompt
    assert "不附加未经证据支持的精确数字" in prompt
    assert "例子、类比和后续邀请都是可选项" in prompt
    assert "不要为了有趣、具体或显得完整而扩写" in prompt
    assert "孩子：天空是什么颜色？" in prompt
    assert "用最近问答解析" in prompt


def test_ambiguous_question_asks_once_instead_of_filling_gaps():
    assert question_may_be_ambiguous("为什么？")
    assert question_may_be_ambiguous("那个怎么样？")
    assert not question_may_be_ambiguous("为什么天空看起来是蓝色的？")
    prompt = build_realtime_answer_instructions("为什么？", 4, [], version=1)
    assert "整个回答只问一个澄清问题" in prompt
    assert "不得先猜一种解释" in prompt
    assert "不得自行补充题目没有给出的对象" not in prompt


def test_current_prompt_uses_server_evidence_and_fails_closed():
    evidence = (
        "服务端官方来源核验状态：fetch_failed。本轮没有可用的官方证据。"
        "必须明确说暂时无法核实，不能提供任何可能变化的事实、数字、名单、赛果或日期。"
    )
    prompt = build_realtime_answer_instructions(
        "世界杯是什么时候，谁和谁踢？", 4, [], version=1, official_evidence=evidence
    )
    assert evidence in prompt
    assert "时效事实优先采用服务端当轮官方来源证据" in prompt
    assert "阿根廷对瑞士" not in prompt


def test_product_facts_trigger_research_and_preserve_price_scope():
    assert question_needs_web_research("布加迪威龙的速度和价格是多少？")
    assert not question_needs_web_research("为什么天空是蓝色的？")
    web_evidence = "服务端联网证据状态：verified。页面写有车型、速度和当年新车价格。"
    prompt = build_realtime_answer_instructions(
        "布加迪威龙的速度和价格是多少？",
        4,
        [],
        version=1,
        web_evidence=web_evidence,
    )
    assert web_evidence in prompt
    assert "价格必须说明版本、年份、币种和价格类型" in prompt
    assert "静默搜索时" in prompt
    assert "不能覆盖服务端结果" in prompt


def test_evidence_policy_is_risk_tiered() -> None:
    assert evidence_tier("熊猫是什么动物？") == EvidenceTier.STABLE
    assert evidence_tier("这款汽车现在多少钱？") == EvidenceTier.CORROBORATED
    assert evidence_tier("现在谁是联合国秘书长？") == EvidenceTier.CORROBORATED
    assert evidence_tier("儿童发烧应该吃什么药、吃几片？") == EvidenceTier.AUTHORITATIVE
    assert evidence_tier("现任总统是谁？") == EvidenceTier.AUTHORITATIVE
    assert evidence_tier("着火了怎么办？") == EvidenceTier.STABLE


def test_curriculum_kb_returns_attributed_evidence(tmp_path: Path):
    path = tmp_path / "kb.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE sources(
              id TEXT PRIMARY KEY,title TEXT,url TEXT,authority TEXT,subject TEXT,sha256 TEXT,
              verified INTEGER NOT NULL
            );
            CREATE TABLE chunks(
              id INTEGER PRIMARY KEY,source_id TEXT,page INTEGER,content TEXT
            );
            INSERT INTO sources
              VALUES('math','数学课标','https://www.moe.gov.cn/a','教育部','数学','x',0);
            INSERT INTO chunks(source_id,page,content)
              VALUES('math',10,'分数表示整体与部分的关系。');
            """
        )
    evidence = CurriculumKnowledgeBase(path).search("分数是什么意思？")
    assert evidence
    assert evidence[0].authority == "教育部"
    assert not evidence[0].verified
    rendered = render_evidence(evidence)
    assert "分数表示整体" not in rendered
    assert "必须打开官方来源核实" in rendered
