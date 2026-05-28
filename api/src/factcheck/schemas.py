"""Pydantic v2 schemas — 全部 API 输入输出契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["general", "policy", "numeric", "entity_status", "event"]
Verdict = Literal[
    "supported",
    "partially_supported",
    "contradicted",
    "outdated",
    "unverifiable",
    "conflicting",
    "out_of_scope",
]
PolicyStatus = Literal["active", "expired", "superseded", "draft", "unknown"]
ConfidenceLevel = Literal["high", "medium", "low"]
SupportLevel = Literal["strong", "weak", "neutral", "contradicted"]
SourceType = Literal[
    "official",
    "regulator",
    "industry_association",
    "authoritative_media",
    "mainstream_media",
    "industry_report",
    "social_media",
    "academic",
    "encyclopedia",
    "exchange",
    "content_farm",
    "unknown",
]


class ScoringWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_authority: float = 0.40
    source_consistency: float = 0.30
    freshness: float = 0.20
    completeness: float = 0.10
    claim_clarity: float = 0.00

    @field_validator("source_authority", "source_consistency", "freshness", "completeness", "claim_clarity")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("权重不能为负")
        return v

    def total(self) -> float:
        return sum(
            [
                self.source_authority,
                self.source_consistency,
                self.freshness,
                self.completeness,
                self.claim_clarity,
            ]
        )

    def validate_sum(self) -> None:
        if abs(self.total() - 1.0) > 0.001:
            raise ValueError(f"INVALID_SCORING_WEIGHTS: 权重和必须为 1.0（实际 {self.total():.4f}）")

    def to_dict(self) -> dict[str, float]:
        return self.model_dump()


class SourceWeights(BaseModel):
    model_config = ConfigDict(extra="allow")
    official: float = 1.0
    regulator: float = 0.95
    industry_association: float = 0.85
    authoritative_media: float = 0.75
    mainstream_media: float = 0.60
    industry_report: float = 0.55
    academic: float = 0.75
    social_media: float = 0.30


class CheckOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_sources: int = Field(default=5, ge=1, le=20)
    require_official_source: bool = False
    return_evidence: bool = True
    return_conflicts: bool = True
    return_answer: bool = False
    timeout_sec: int | None = None
    use_cache: bool = True
    use_inference_chain: bool = False  # 启用 bge-large-zh embedding 推演链（首调用加载 ~1.3GB 模型）


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str | None = None
    question: str | None = None
    mode: Mode = "general"
    domain: str | None = None
    language: str = "zh-CN"
    source_profile: str | None = None
    scoring_profile: str | None = None
    scoring_weights: ScoringWeights | None = None
    source_weights: SourceWeights | None = None
    options: CheckOptions = Field(default_factory=CheckOptions)

    @field_validator("claim", "question")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("claim/question 不能为空字符串")
        return v

    def input_text(self) -> str:
        return (self.claim or self.question or "").strip()

    def is_question(self) -> bool:
        return bool(self.question and not self.claim)


class PolicyCheckRequest(CheckRequest):
    mode: Mode = "policy"


class BatchCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[CheckRequest]


class SolutionAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str | None = None
    claims: list[str]
    mode: Mode = "general"
    scoring_weights: ScoringWeights | None = None
    options: CheckOptions = Field(default_factory=CheckOptions)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    source_url: str
    source_name: str | None = None
    source_type: SourceType
    authority_weight: float = 0.0
    agency: str | None = None
    agency_level: str | None = None
    published_at: str | None = None
    snippet: str | None = None
    support_level: SupportLevel = "neutral"
    is_primary: bool = True
    is_independent: bool = True
    content_fingerprint: str | None = None
    claims_about: dict[str, Any] = Field(default_factory=dict)
    evidence_score: float | None = None


class Conflict(BaseModel):
    field: str
    values: list[Any]
    level: Literal["high", "medium", "low"] = "medium"
    resolved: bool = False
    arbitration_note: str | None = None


class VersionRef(BaseModel):
    """版本溯源——一条历史/当前版本的元信息。"""

    model_config = ConfigDict(extra="allow")
    label: str  # 如 "2018 修正" / "2023 修订"
    document_number: str | None = None
    publish_date: str | None = None
    effective_date: str | None = None
    expire_date: str | None = None
    superseded_by: str | None = None  # 该版本被哪个版本替代
    source_url: str | None = None
    note: str | None = None


class PolicyMeta(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str | None = None
    issuing_agency: str | None = None
    agency_level: Literal["national", "province", "city", "district"] | None = None
    document_number: str | None = None
    publish_date: str | None = None
    effective_date: str | None = None
    expire_date: str | None = None
    last_updated: str | None = None
    applicable_region: list[str] = Field(default_factory=list)
    applicable_subjects: list[str] = Field(default_factory=list)
    benefit_amount: str | None = None
    application_conditions: list[str] = Field(default_factory=list)
    required_materials: list[str] = Field(default_factory=list)
    application_process: str | None = None
    deadline: str | None = None
    contact_info: str | None = None
    # 溯源字段
    current_version: VersionRef | None = None
    previous_versions: list[VersionRef] = Field(default_factory=list)
    superseded_by: str | None = None

    @field_validator(
        "applicable_region",
        "applicable_subjects",
        "application_conditions",
        "required_materials",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, v):
        """LLM 偶尔返回字符串或字典而非列表，自动 coerce 防止 validation 错误。"""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, dict):
            return list(v.values()) if v else []
        if isinstance(v, list):
            return [str(x) for x in v if x not in (None, "")]
        return [str(v)]

    @field_validator("agency_level", mode="before")
    @classmethod
    def _coerce_agency_level(cls, v):
        """LLM 偶尔返回非枚举值，过滤之。"""
        if v in ("national", "province", "city", "district"):
            return v
        return None


class DimScore(BaseModel):
    score: float
    weight: float
    weighted: float


class ScoreBreakdown(BaseModel):
    source_authority: DimScore
    source_consistency: DimScore
    freshness: DimScore
    completeness: DimScore
    claim_clarity: DimScore


class TokenUsage(BaseModel):
    """计入父项目按 token 报销的核心字段。"""

    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    search_calls: int = 0
    fetch_calls: int = 0


class PropagationStop(BaseModel):
    """信息传播时间线上的一个节点。"""

    model_config = ConfigDict(extra="allow")
    source_url: str
    source_name: str | None = None
    source_category: Literal[
        "social_media",  # 微博 / 知乎 / 抖音 / 小红书
        "self_media",  # 百家号 / 头条号 / 微信公众号 / 搜狐号
        "aggregator",  # 新浪 / 网易 / 腾讯网（聚合门户）
        "mainstream_media",  # 财新 / 第一财经 / 澎湃 / 21经济
        "local_official_media",  # 地方融媒体 / 地方党媒
        "central_official_media",  # 新华社 / 人民日报 / 央视
        "encyclopedia",  # 百度百科 / 维基
        "official",  # gov.cn / 部委
        "fact_check_platform",  # piyao.org.cn / 各地辟谣
        "other",
    ] = "other"
    earliest_seen: str | None = None  # ISO8601 date
    is_likely_origin: bool = False
    is_likely_amplifier: bool = False
    snippet: str | None = None


class PropagationTimeline(BaseModel):
    """信息传播时间线 — 该 claim/事件从何处发起、如何扩散。"""

    model_config = ConfigDict(extra="allow")
    earliest_date: str | None = None
    latest_date: str | None = None
    n_stops: int = 0
    stops: list[PropagationStop] = Field(default_factory=list)  # 按时间正序
    pattern: Literal[
        "grassroots_viral",  # 社交媒体起源 → 主流扩散（典型病毒传播）
        "official_dissemination",  # 官方发布 → 媒体转载（正常信息发布）
        "narrative_distortion",  # 官方 → 自媒体扭曲转述
        "coordinated",  # 多平台同期出现（疑似有组织）
        "fact_check_corrected",  # 含 piyao 等辟谣源，传播被纠正
        "insufficient_data",  # 时间数据不足以判断
    ] = "insufficient_data"
    confidence: int = Field(default=0, ge=0, le=100)
    note: str | None = None


class ChainNode(BaseModel):
    """InferenceChain 中的单个节点 — 一份 evidence 在传播链中的位置 + embedding 相似度。"""

    model_config = ConfigDict(extra="allow")
    source_url: str
    source_name: str | None = None
    source_category: Literal[
        "social_media",
        "self_media",
        "aggregator",
        "mainstream_media",
        "local_official_media",
        "central_official_media",
        "encyclopedia",
        "official",
        "fact_check_platform",
        "other",
    ] = "other"
    published_at: str | None = None  # ISO date / datetime
    snippet_preview: str | None = None  # 截断到 200 字
    tier: float | None = None  # authority_weight 0-1
    role: Literal["origin", "propagator", "debunker", "amplifier"] = "propagator"
    likely_source: str | None = None  # 前置节点中相似度最高的 URL（可能的"来源"）
    similarity_to_source: float | None = None  # 与 likely_source 的 cosine 相似度
    all_similarities: list[dict[str, Any]] = Field(
        default_factory=list
    )  # [{"from": url, "similarity": 0.92}] 与所有前置节点的相似度


class InferenceChain(BaseModel):
    """从 evidence embedding + 时间排序推演出的信息传播链。

    与 PropagationTimeline 区别：
      - PropagationTimeline 用 URL 域名分类启发式判定 pattern，不计算 embedding
      - InferenceChain 用 bge-large-zh embedding 计算每个节点和前置节点的 cosine
        相似度，定位"最可能的信息来源"
    """

    model_config = ConfigDict(extra="allow")
    nodes: list[ChainNode] = Field(default_factory=list)  # 按时间正序
    origin: ChainNode | None = None  # 时间最早 + 优先非权威源
    chain_length: int = 0
    avg_similarity: float = 0.0  # 非 origin 节点的 similarity_to_source 均值
    pattern: Literal[
        "grassroots_viral",
        "official_dissemination",
        "narrative_distortion",
        "coordinated",
        "fact_check_corrected",
        "insufficient_data",
    ] = "insufficient_data"
    timeline_hours: float | None = None  # origin 到 latest 的小时差
    embedding_model: str = "BAAI/bge-large-zh-v1.5"
    note: str | None = None


class ConfidenceInterval(BaseModel):
    """置信度区间 — bootstrap on 5 dim scores 得出 [lo, hi]，反映点估计的不确定性。

    用法：
      - 区间宽（如 [40, 90]）表示评估不稳定，建议人工复核
      - 区间窄（如 [78, 85]）表示评估稳健
      - 用 confidence 作为点估计，[lo, hi] 反映系统对自身判断的"二阶不确定性"
    """

    lo: int = Field(ge=0, le=100)
    hi: int = Field(ge=0, le=100)
    method: str = "bootstrap_5dim"
    note: str | None = None


class CheckResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    request_id: str
    input: str
    mode: Mode
    verdict: Verdict
    confidence: int = Field(ge=0, le=100)
    confidence_level: ConfidenceLevel
    confidence_interval: ConfidenceInterval | None = None
    has_official_source: bool = False
    policy_status: PolicyStatus | None = None
    policy: PolicyMeta | None = None
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    facts: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    score_breakdown: ScoreBreakdown
    formula: str = "confidence = Σ(score_i × weight_i)，gating rules 后封顶/降级"
    gating_applied: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    answer: str | None = None
    propagation_timeline: PropagationTimeline | None = None
    inference_chain: InferenceChain | None = None
    suspected_viral_claim: bool = False
    collected_at: str
    cache_hit: bool = False
    token_usage: TokenUsage | None = None


class BatchCheckResponse(BaseModel):
    batch_id: str
    status: Literal["completed", "partial", "failed"] = "completed"
    results: list[CheckResponse | dict[str, Any]]
    total_token_usage: TokenUsage | None = None


class AsyncJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_type: Literal["check_batch", "document_audit", "solution_audit"]
    payload: dict[str, Any]
    callback_url: str | None = None


class AsyncJobResponse(BaseModel):
    job_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    created_at: str
    progress: float = 0.0
    result: dict[str, Any] | None = None
    result_url: str | None = None
    error: str | None = None


class SourceEntry(BaseModel):
    domain: str
    name: str | None = None
    url_pattern: str | None = None
    source_type: SourceType
    authority_weight: float = Field(ge=0, le=1)
    agency: str | None = None
    agency_level: str | None = None
    tier: str | None = None


class SourceProfile(BaseModel):
    profile_name: str
    domain: str | None = None
    sources: list[SourceEntry]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ErrorResponse(BaseModel):
    error: dict[str, Any]


def make_error(code: str, message: str, details: dict | None = None) -> ErrorResponse:
    err: dict[str, Any] = {"code": code, "message": message}
    if details:
        err["details"] = details
    return ErrorResponse(error=err)
