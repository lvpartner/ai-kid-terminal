import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Evidence:
    title: str
    url: str
    authority: str
    subject: str
    page: int
    content: str
    verified: bool

    def as_dict(self) -> dict[str, str | int]:
        item = asdict(self)
        if not self.verified:
            item["content"] = ""
        return item


@dataclass(frozen=True)
class CurriculumTopic:
    subject: str
    grade_min: int
    grade_max: int
    domain: str
    topic: str
    points: tuple[str, ...]
    source_title: str
    source_url: str


SUBJECT_TERMS = {
    "数学": "数学 数 加减乘除 分数 小数 方程 几何 图形 统计 概率",
    "语文": "语文 汉字 拼音 词语 句子 阅读 文章 中心思想 作文 古诗 文言文",
    "英语": "英语 单词 语法 阅读 听说 写作",
    "科学": "科学 物理 化学 生物 植物 动物 呼吸 地球 宇宙 实验",
    "历史": "历史 朝代 古代 近代 世界史",
    "地理": "地理 地图 气候 地形 国家",
    "道德与法治": "规则 法律 道德 责任 友谊 情绪",
    "信息科技": "计算机 网络 编程 算法 数据 人工智能",
    "体育与健康": "运动 健康 营养 身体 体育",
    "艺术": "音乐 美术 艺术 舞蹈 戏剧",
    "劳动": "劳动 家务 工具 实践",
}


def classify_subject(question: str) -> str:
    scores = {
        subject: sum(1 for term in terms.split() if term in question)
        for subject, terms in SUBJECT_TERMS.items()
    }
    return max(scores, key=lambda subject: scores[subject]) if max(scores.values()) else "课程方案"


def is_time_sensitive(question: str) -> bool:
    normalized = question.lower()
    markers = (
        "今天",
        "现在",
        "最新",
        "目前",
        "还有哪",
        "什么时候",
        "比分",
        "天气",
        "下雨",
        "气温",
        "温度",
        "新闻",
        "价格",
        "谁是现任",
        "世界杯",
        "赛程",
        "谁和谁踢",
        "对阵",
        "world cup",
        "fifa",
    )
    return any(marker in normalized for marker in markers)


def resolve_grade(question: str, configured_grade: int) -> int:
    match = re.search(r"([一二三四五六1-6])年级", question)
    if not match:
        return configured_grade
    values = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
    value = match.group(1)
    return int(value) if value.isdigit() else values[value]


class CurriculumKnowledgeBase:
    def __init__(self, path: Path):
        self.path = path
        self.topic_path = path.with_name("primary-curriculum.json")
        self._topics = self._load_topics()

    def _load_topics(self) -> tuple[CurriculumTopic, ...]:
        if not self.topic_path.is_file():
            return ()
        payload = json.loads(self.topic_path.read_text())
        topics = []
        for item in payload.get("topics", []):
            topics.append(
                CurriculumTopic(
                    subject=str(item["subject"]),
                    grade_min=int(item["grade_min"]),
                    grade_max=int(item["grade_max"]),
                    domain=str(item["domain"]),
                    topic=str(item["topic"]),
                    points=tuple(str(point) for point in item["points"]),
                    source_title=str(item["source_title"]),
                    source_url=str(item["source_url"]),
                )
            )
        return tuple(topics)

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def search(self, question: str, limit: int = 4) -> list[Evidence]:
        if not self.available:
            return []
        subject = classify_subject(question)
        normalized = re.sub(r"\s+", "", question)
        terms = set(re.findall(r"[A-Za-z]+|\d+", question))
        terms.update(normalized[index : index + 2] for index in range(len(normalized) - 1))
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as db:
            rows = db.execute(
                """SELECT s.title,s.url,s.authority,s.subject,c.page,c.content,s.verified
                   FROM chunks c JOIN sources s ON s.id=c.source_id
                   WHERE s.subject IN (?, '课程方案', '数学思维')""",
                (subject,),
            ).fetchall()
        ranked = sorted(
            rows,
            key=lambda row: (
                (100 if row[3] == subject else 0)
                + sum(len(term) for term in terms if term in row[5])
            ),
            reverse=True,
        )
        return [Evidence(*row) for row in ranked[:limit] if row[5].strip()]

    def search_topics(self, question: str, grade: int, limit: int = 6) -> list[CurriculumTopic]:
        subject = classify_subject(question)
        normalized = re.sub(r"\s+", "", question).lower()
        terms = set(re.findall(r"[A-Za-z]+|\d+", normalized))
        terms.update(normalized[index : index + 2] for index in range(len(normalized) - 1))
        ranked: list[tuple[int, CurriculumTopic]] = []
        for item in self._topics:
            if not item.grade_min <= grade <= item.grade_max:
                continue
            searchable = " ".join((item.domain, item.topic, *item.points)).lower()
            score = (100 if item.subject == subject else 0) + sum(
                len(term) for term in terms if term and term in searchable
            )
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda value: value[0], reverse=True)
        return [item for _, item in ranked[:limit]]


def render_evidence(items: list[Evidence], max_chars: int = 6000) -> str:
    sections = []
    for index, item in enumerate(items, 1):
        header = f"[{index}] {item.authority}《{item.title}》\n来源：{item.url}"
        if item.verified:
            sections.append(f"{header}\n已核验原文第{item.page}页：\n{item.content}")
        else:
            sections.append(f"{header}\n未核验导航记录：不得引用摘要内容，必须打开官方来源核实。")
    rendered = "\n\n".join(sections)
    return rendered[:max_chars]


def evidence_json(items: list[Evidence]) -> str:
    return json.dumps([item.as_dict() for item in items], ensure_ascii=False)


def render_curriculum_topics(items: list[CurriculumTopic], max_chars: int = 2400) -> str:
    sections = [
        (
            f"[{item.subject} {item.grade_min}-{item.grade_max}年级] "
            f"{item.domain} / {item.topic}：{'；'.join(item.points)}\n"
            f"依据：{item.source_title}（{item.source_url}）"
        )
        for item in items
    ]
    return "\n\n".join(sections)[:max_chars]
