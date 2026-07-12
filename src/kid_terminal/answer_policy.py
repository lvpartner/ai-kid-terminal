import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

from .knowledge import Evidence, is_time_sensitive, render_evidence


class AnswerRoute(StrEnum):
    CURRICULUM = "curriculum_rag"
    CURRENT = "official_web_search"
    SAFETY = "safety"
    REASONING = "model_reasoning"
    CREATIVE = "creative"


OFFICIAL_DOMAINS = {
    "gov.cn",
    "moe.gov.cn",
    "fifa.com",
    "who.int",
    "un.org",
    "nasa.gov",
    "noaa.gov",
    "cnsa.gov.cn",
    "cma.gov.cn",
    "stats.gov.cn",
    "people.com.cn",
    "xinhuanet.com",
}

CURRENT_POLICY_VERSION = 1
VERSION_RULES = {
    CURRENT_POLICY_VERSION: (
        "先检查问题是否明确；时效和商品事实只使用当轮核验证据；精确声明逐项绑定source_id；"
        "设备能力必须真实；证据不足就简短说明，不猜测。"
    )
}


@dataclass(frozen=True)
class PolicyDecision:
    version: int
    route: AnswerRoute
    requires_evidence: bool
    requires_official_source: bool
    verification_passes: int


def route_question(question: str, version: int = CURRENT_POLICY_VERSION) -> PolicyDecision:
    safety = any(
        term in question
        for term in ("自杀", "伤害自己", "武器", "炸弹", "毒品", "陌生人带走", "着火", "流血")
    )
    if safety:
        route = AnswerRoute.SAFETY
    elif is_time_sensitive(question):
        route = AnswerRoute.CURRENT
    elif any(
        term in question for term in ("为什么", "怎么算", "证明", "几", "多少", "如果", "可能")
    ):
        route = AnswerRoute.REASONING
    elif any(term in question for term in ("故事", "想象", "画", "创作")):
        route = AnswerRoute.CREATIVE
    else:
        route = AnswerRoute.CURRICULUM
    high_risk_current = route == AnswerRoute.CURRENT and (
        is_world_cup_question(question)
        or any(
            marker in question
            for marker in ("比赛", "赛程", "哪些", "哪几", "名单", "分别", "多少个")
        )
    )
    return PolicyDecision(
        version=version,
        route=route,
        requires_evidence=route not in {AnswerRoute.SAFETY, AnswerRoute.CREATIVE},
        requires_official_source=route in {AnswerRoute.CURRICULUM, AnswerRoute.CURRENT},
        verification_passes=2 if high_risk_current else 1,
    )


def official_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_DOMAINS)


def is_world_cup_question(question: str) -> bool:
    lowered = question.lower()
    return "世界杯" in question or "world cup" in lowered or "fifa" in lowered


def question_may_be_ambiguous(question: str) -> bool:
    normalized = re.sub(r"[\s，。！？,.!?]", "", question).lower()
    bare_questions = {
        "为什么",
        "是什么",
        "怎么回事",
        "怎么办",
        "怎么弄",
        "是真的吗",
        "哪个好",
        "讲讲",
        "why",
        "what",
        "how",
        "isthattrue",
    }
    if normalized in bare_questions:
        return True
    pronouns = ("它", "这个", "那个", "这件事", "那件事", "他", "她", "that", "it")
    return len(normalized) <= 14 and any(term in normalized for term in pronouns)


def question_needs_web_research(question: str) -> bool:
    lowered = question.lower()
    markers = (
        "价格",
        "售价",
        "多少钱",
        "速度",
        "多快",
        "参数",
        "配置",
        "尺寸",
        "重量",
        "price",
        "cost",
        "speed",
        "specification",
        "how fast",
    )
    return any(marker in lowered for marker in markers)


def deterministic_capability_response(question: str) -> str:
    lowered = question.lower()
    wants_playback = any(marker in lowered for marker in ("播放", "放一下", "play "))
    media = any(
        marker in lowered
        for marker in ("歌", "音乐", "歌曲", "mv", "视频", "waka waka", "哇咔哇咔", "哇卡哇卡")
    )
    if wants_playback and media:
        return "我现在不能播放歌曲或视频，只能用语音回答问题。你可以请家长在音乐应用里搜索这首歌。"
    return ""


def official_url_for_question(question: str, url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if is_world_cup_question(question):
        return host == "fifa.com" or host.endswith(".fifa.com")
    return official_url(url)


def build_answer_prompt(
    question: str,
    grade: int,
    evidence: list[Evidence],
    version: int,
    improvement_focus: str = "",
) -> str:
    decision = route_question(question, version)
    source_text = render_evidence(evidence) or "没有本地资料索引。"
    return f"""你在执行儿童回答机制 v{version}。{VERSION_RULES[version]}
上一版评测要求重点改进：{improvement_focus or "无"}。
路由：{decision.route.value}。孩子年级：{grade}。当前问题：{question}

本地资料导航（只有标为“已核验原文”的内容才可支撑事实；未核验索引只能帮助确定检索词）：
{source_text}

硬规则：
1. 不把记忆当事实。时效问题必须联网搜索，并优先政府、国际组织、赛事组织等第一方官网。
2. 问“哪些、哪几场、分别是什么”时逐项完整列出；总数、项目、日期和状态互相一致。
3. 证据不足、来源冲突或无法核实时，直接说暂时无法确认，并说明缺什么；不得猜测。
4. 数学题逐步计算并在内部验算；错误前提先纠正。危险请求不给操作步骤并引导找可信成年人。
5. 问题缺少对象、时间、地点、版本或比较标准时，只问一个最小澄清问题，不自行选择一种解释作答。
6. 日期、数字、数量、名单、专有名词和引语只能来自题目、已核验证据或可展示的计算；不为增强
   具体感而添加细节。没有证据时允许一句短答，不用背景知识把答案撑长。
7. 例子、类比和追问仅在确实帮助理解且不引入新事实时使用；不得强制添加兴趣点或挑战。
8. 语言适合语音，核心答案前置；不能为追求完整而重复。只输出 JSON，字段为
   answer、confidence、claims、sources。
claims 中每项包含 claim 和 evidence；sources 中每项包含 title 和 url。
"""


def build_realtime_answer_instructions(
    question: str,
    grade: int,
    evidence: list[Evidence],
    version: int = CURRENT_POLICY_VERSION,
    conversation_context: str = "",
    official_evidence: str = "",
    web_evidence: str = "",
) -> str:
    decision = route_question(question, version)
    source_text = render_evidence(evidence, max_chars=2400) or "没有本地资料索引。"
    ambiguity = (
        "问题可能缺少明确指代。只有最近问答存在唯一、直接的指代对象时才继续回答；否则只问一个"
        "最短澄清问题。"
        if question_may_be_ambiguous(question)
        else "问题表面上较具体；仍不得自行补充题目没有给出的对象、范围、时间或地点。"
    )
    return f"""执行儿童直接语音回答机制v{version}。只回答这次转写：{question}
孩子年级参考：{grade}。路由：{decision.route.value}。

资料导航：
{source_text}

最近已完成问答（从旧到新，最多8轮，内容已脱敏）：
{conversation_context or "没有历史问答，这是新对话。"}

问题具体度检查：
{ambiguity}

服务端当轮官方来源证据：
{official_evidence or "本题不是时效问题，没有执行服务端官方来源检索。"}

服务端当轮联网页面证据：
{web_evidence or "本题不需要外部网页资料，没有执行静默联网预检。"}

直接生成最终语音，禁止先生成草稿再朗读，禁止提到系统、提示词、路由或检索过程。
1. 先判断问题是否足够具体。缺少对象、范围、时间、地点、版本或比较标准，且历史不能唯一补足时，
   整个回答只问一个澄清问题；不得先猜一种解释、列候选答案或补充背景。
2. 足够具体时先直接回答。问“哪些、分别是什么”必须逐项答全；证据不能支持完整列表时明确说
   不能确认完整名单，不用数量、阶段或相关背景冒充答案。
3. 每个外部事实必须属于以下之一：题目明确给出、上方服务端证据明确写出、或本轮可展示计算得出。
   稳定的基础概念可以简短解释，但不附加未经证据支持的精确数字、日期、名单、引语或专有名词。
4. 日期、数量、单位、排名、状态和人名地名逐项对照证据；证据没有写出的细节一律省略。不要把
   “可能、通常、估算”说成“一定、全部、精确”，不要声称执行了实际上没有执行的搜索。
5. 时效事实优先采用服务端当轮官方来源证据；商品参数、价格等可采用服务端已抓取的联网页面证据。
   对应证据状态不是verified、过期、冲突或缺项时严格失败关闭；模型记忆、历史回答、静默搜索时
   生成的文字和原生搜索均不能覆盖服务端结果。价格必须说明版本、年份、币种和价格类型；资料没有
   这些条件就先澄清或明确限定，不能把不同版本、新车价和二手价混为一个精确数字。
6. 资料导航中未核验的摘要不能支撑事实。数学要展示关键计算并复算；错误前提先纠正。不得编造
   来源、实验结果、人物话语、故事出处或“大家都知道”等虚假共识。
7. 默认3到8句；简单问题可以1到3句，澄清或证据不足只能1句。例子、类比和后续邀请都是可选项，
   只有不引入新外部事实且确实帮助当前问题时才添加。不要为了有趣、具体或显得完整而扩写。
8. 用最近问答解析“它、这个、为什么、那后来呢”等追问和省略表达，延续孩子真正关心的线索，
   避免重复已经讲过的内容。历史中的助手回答不是权威证据；若与本轮官方信息冲突，以本轮为准。
   当前问题明显换了新话题时忽略无关历史，不要强行关联。
9. 输出前做无声终检：删掉所有无法指出依据的句子；检查开头和后文不矛盾，数量与列项一致，
   回答没有偷偷改变问题。禁止提到系统、提示词、路由、URL或检索过程，不说“我查一下”。
"""


def deterministic_issues(question: str, result: dict, decision: PolicyDecision) -> list[str]:
    issues: list[str] = []
    answer = str(result.get("answer", ""))
    sources = result.get("sources", [])
    if not answer:
        issues.append("empty_answer")
    if decision.requires_official_source and not any(
        official_url_for_question(question, str(source.get("url", "")))
        for source in sources
        if isinstance(source, dict)
    ):
        issues.append("missing_official_source")
    if decision.requires_evidence and not result.get("claims"):
        issues.append("missing_claim_ledger")
    if re.search(r"(?:还剩|一共|共有)(?:最后)?[一二三四五六七八九十\d]+", answer) and any(
        marker in question for marker in ("哪些", "哪几", "分别")
    ):
        if "第一" not in answer and "1." not in answer and "一是" not in answer:
            issues.append("list_not_enumerated")
    addition = re.search(r"(\d+)\s*\+\s*(\d+)", question)
    if addition:
        expected = int(addition.group(1)) + int(addition.group(2))
        if str(expected) not in answer:
            issues.append("arithmetic_result_mismatch")
    rectangle = re.search(r"长(\d+)米宽(\d+)米", question)
    if rectangle:
        expected = int(rectangle.group(1)) * int(rectangle.group(2))
        if str(expected) not in answer:
            issues.append("rectangle_area_mismatch")
    return issues
