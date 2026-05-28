"""主 pipeline 编排器。串起 7 步：QueryPlan → Search → Fetch → Extract → CrossValidate → Score → Generate。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from .cache import RedisCache, build_cache
from .config import get_settings
from .extract import FactExtractor, InferenceChainBuilder, QueryPlanner, TimelineBuilder
from .fetch import FetchOrchestrator
from .llm import LLMMessage, get_provider
from .schemas import (
    CheckRequest,
    CheckResponse,
    Conflict,
    DimScore,
    Evidence,
    PolicyMeta,
    ScoreBreakdown,
    TokenUsage,
)
from .score import ScoreEngine, SourceClassifier
from .search import SearchOrchestrator
from .utils.logger import get_logger
from .utils.prompts import ANSWER_GENERATOR_SYSTEM
from .utils.token_counter import TokenAccumulator

logger = get_logger("pipeline")


def _cache_key(req: CheckRequest) -> str:
    h = hashlib.sha256()
    h.update((req.claim or req.question or "").encode("utf-8"))
    h.update(b"|")
    h.update(req.mode.encode("utf-8"))
    if req.scoring_weights:
        h.update(b"|")
        h.update(str(sorted(req.scoring_weights.to_dict().items())).encode("utf-8"))
    return h.hexdigest()[:40]


def _cache_ttl(mode: str, policy_status: str | None) -> int:
    s = get_settings()
    if mode == "policy":
        if policy_status in ("expired", "superseded"):
            return s.cache_ttl_historical_sec
        return s.cache_ttl_policy_active_sec
    if mode == "numeric":
        return s.cache_ttl_historical_sec
    if mode == "entity_status":
        return s.cache_ttl_state_sec
    return s.cache_ttl_state_sec


class FactCheckPipeline:
    def __init__(
        self,
        *,
        search: SearchOrchestrator | None = None,
        fetch: FetchOrchestrator | None = None,
        scorer: ScoreEngine | None = None,
        classifier: SourceClassifier | None = None,
        cache: RedisCache | None = None,
    ) -> None:
        self.search = search or SearchOrchestrator()
        self.fetch = fetch or FetchOrchestrator()
        self.scorer = scorer or ScoreEngine()
        self.classifier = classifier or SourceClassifier()
        self.cache = cache

    @classmethod
    async def create(cls) -> FactCheckPipeline:
        return cls(cache=await build_cache())

    async def run(self, req: CheckRequest) -> CheckResponse:
        s = get_settings()
        request_id = str(uuid.uuid4())
        token_acc = TokenAccumulator()
        provider = get_provider()

        input_text = req.input_text()
        if not input_text:
            return self._make_empty_response(request_id, req, "claim 或 question 不能为空", token_acc)

        cache_k = _cache_key(req)
        if req.options.use_cache and self.cache is not None:
            cached = await self.cache.get(cache_k)
            if cached is not None:
                cached["cache_hit"] = True
                cached["request_id"] = request_id
                return CheckResponse(**cached)

        weights = req.scoring_weights.to_dict() if req.scoring_weights else None

        from .extract import SubjectiveDetector

        subj_det = SubjectiveDetector(provider=None)
        is_subj, subj_reason = await subj_det.is_subjective(input_text, token_acc)
        if is_subj:
            logger.info("subjective_short_circuit", request_id=request_id, reason=subj_reason)
            return self._build_subjective_response(request_id, req, subj_reason, token_acc)

        planner = QueryPlanner(provider)
        plan = await planner.plan(input_text, mode=req.mode, token_acc=token_acc)
        logger.info(
            "query_planned", request_id=request_id, entities=plan.entities, queries=plan.search_queries[:3]
        )

        if not plan.search_queries:
            plan.search_queries = [input_text]

        search_res = await self.search.search(
            plan.search_queries[: s.max_search_calls_per_claim],
            per_query_limit=6,
            total_cap=req.options.max_sources * 2,
        )
        token_acc.add_search(n=search_res.total_calls)
        logger.info(
            "search_done",
            request_id=request_id,
            urls=len(search_res.results),
            providers=search_res.used_providers,
        )

        if not search_res.results:
            return self._build_response(
                request_id=request_id,
                req=req,
                evidence=[],
                conflicts=[],
                policy_meta={},
                warnings=["搜索引擎无相关结果"],
                missing_fields=[],
                reasoning="搜索引擎无相关结果",
                token_acc=token_acc,
            )

        urls = [r.url for r in search_res.results[: req.options.max_sources]]
        pages = await self.fetch.fetch_many(urls)
        token_acc.add_fetch(n=len(pages))
        ok_pages = [p for p in pages if p.status == "ok"]
        ok_classifications = [self.classifier.classify(p.url) for p in ok_pages]
        logger.info("fetch_done", request_id=request_id, ok=len(ok_pages), total=len(pages))

        if not ok_pages:
            return self._build_response(
                request_id=request_id,
                req=req,
                evidence=[],
                conflicts=[],
                policy_meta={},
                warnings=["所有候选 URL 抓取失败"],
                missing_fields=[],
                reasoning="抓取失败",
                token_acc=token_acc,
            )

        extractor = FactExtractor(provider)
        evidences = await extractor.extract_all(input_text, req.mode, ok_pages, ok_classifications, token_acc)
        logger.info("extracted", request_id=request_id, evidence_count=len(evidences))

        from .verify import CrossValidator

        validator = CrossValidator(provider)
        validation = await validator.validate(input_text, req.mode, evidences, token_acc)

        evidence_dicts = [
            {
                "source_url": e.source_url,
                "source_type": e.source_type,
                "authority_weight": e.authority_weight,
                "agency": e.agency,
                "support_level": e.support_level,
                "published_at": e.published_at,
                "is_independent": e.is_independent,
                "content_fingerprint": e.content_fingerprint,
                "claims_about": e.claims_about,
                "snippet": e.snippet,
            }
            for e in evidences
        ]
        score_result = self.scorer.score(
            claim=input_text,
            mode=req.mode,
            evidence=evidence_dicts,
            conflicts=validation.conflicts,
            policy_meta=validation.policy_meta,
            weights=weights,
            require_official_source=req.options.require_official_source,
        )

        answer: str | None = None
        if req.options.return_answer and req.is_question():
            answer = await self._generate_answer(
                provider,
                input_text,
                score_result.verdict,
                score_result.confidence,
                evidences[:5],
                validation.warnings,
                token_acc,
            )

        # 信息传播时间线 — 从 evidence 域名 + 时间戳反推传播路径
        timeline = None
        inference_chain = None
        if evidences:
            ev_for_timeline = [
                {
                    "source_url": e.source_url,
                    "source_name": e.source_name,
                    "source_type": e.source_type,
                    "agency": e.agency,
                    "authority_weight": e.authority_weight,
                    "published_at": e.published_at,
                    "snippet": e.snippet,
                }
                for e in evidences
            ]
            timeline = TimelineBuilder().build(ev_for_timeline)

            # InferenceChain（bge-large-zh embedding 推演）— 仅当显式启用，避免 1.3GB 模型常驻
            if getattr(req.options, "use_inference_chain", False):
                try:
                    inference_chain = InferenceChainBuilder().build(ev_for_timeline)
                except Exception as exc:
                    logger.warning(
                        "inference_chain_failed", request_id=request_id, error=str(exc)
                    )

        # 传播性谣言启发式：query planner 标记的 viral claim flag
        suspected_viral = bool(getattr(plan, "suspected_viral_claim", False))

        response = self._assemble_response(
            request_id=request_id,
            req=req,
            evidences=evidences,
            validation=validation,
            score_result=score_result,
            answer=answer,
            token_acc=token_acc,
            propagation_timeline=timeline,
            inference_chain=inference_chain,
            suspected_viral_claim=suspected_viral,
        )

        if req.options.use_cache and self.cache is not None:
            ttl = _cache_ttl(req.mode, score_result.policy_status)
            if ttl > 0:
                await self.cache.set(cache_k, response.model_dump(), ttl=ttl)

        return response

    async def _generate_answer(
        self,
        provider,
        question: str,
        verdict: str,
        confidence: int,
        evidences: list,
        warnings: list[str],
        token_acc: TokenAccumulator,
    ) -> str:
        ev_summary = [
            {
                "url": e.source_url,
                "snippet": (e.snippet or "")[:200],
                "agency": e.agency,
                "published_at": e.published_at,
            }
            for e in evidences
        ]
        user_msg = (
            f"用户问题:\n{question}\n\n"
            f"verdict: {verdict}\n置信度: {confidence}\n"
            f"warnings: {warnings}\n\n"
            f"关键证据：\n{ev_summary}\n\n"
            f"请生成 80-200 字的中文答案。"
        )
        resp = await provider.chat(
            [
                LLMMessage(role="system", content=ANSWER_GENERATOR_SYSTEM),
                LLMMessage(role="user", content=user_msg),
            ],
            temperature=0.2,
            max_tokens=400,
            timeout=20.0,
        )
        token_acc.add_llm(
            provider=resp.provider,
            model=resp.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            total_tokens=resp.total_tokens,
            label="answer_generator",
        )
        return resp.text.strip()

    def _make_empty_response(
        self, request_id: str, req: CheckRequest, msg: str, token_acc: TokenAccumulator
    ) -> CheckResponse:
        return self._build_response(
            request_id=request_id,
            req=req,
            evidence=[],
            conflicts=[],
            policy_meta={},
            warnings=[msg],
            missing_fields=[],
            reasoning=msg,
            token_acc=token_acc,
        )

    def _build_subjective_response(
        self, request_id: str, req: CheckRequest, reason: str, token_acc: TokenAccumulator
    ) -> CheckResponse:
        """主观 claim 早期短路 — 直接 out_of_scope，不调搜索。"""
        breakdown = ScoreBreakdown(
            source_authority=DimScore(score=0, weight=0.4, weighted=0),
            source_consistency=DimScore(score=0, weight=0.3, weighted=0),
            freshness=DimScore(score=0, weight=0.2, weighted=0),
            completeness=DimScore(score=0, weight=0.1, weighted=0),
            claim_clarity=DimScore(score=0, weight=0.0, weighted=0),
        )
        return CheckResponse(
            request_id=request_id,
            input=req.input_text(),
            mode=req.mode,
            verdict="out_of_scope",
            confidence=0,
            confidence_level="low",
            has_official_source=False,
            policy_status=None,
            policy=None,
            missing_fields=[],
            warnings=[f"主观/无法核查类 claim: {reason}"],
            facts=[],
            evidence=[],
            conflicts=[],
            score_breakdown=breakdown,
            gating_applied=["subjective_short_circuit"],
            reasoning_summary=f"主观评价/价值判断类，无法用事实核查（{reason}）",
            answer=None,
            collected_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            cache_hit=False,
            token_usage=TokenUsage(
                provider=token_acc.provider,
                model=token_acc.model,
                prompt_tokens=token_acc.prompt_tokens,
                completion_tokens=token_acc.completion_tokens,
                total_tokens=token_acc.total_tokens,
                search_calls=token_acc.search_calls,
                fetch_calls=token_acc.fetch_calls,
            ),
        )

    def _build_response(
        self,
        *,
        request_id: str,
        req: CheckRequest,
        evidence: list,
        conflicts: list,
        policy_meta: dict,
        warnings: list[str],
        missing_fields: list[str],
        reasoning: str,
        token_acc: TokenAccumulator,
    ) -> CheckResponse:
        weights = req.scoring_weights.to_dict() if req.scoring_weights else None
        score_result = self.scorer.score(
            claim=req.input_text(),
            mode=req.mode,
            evidence=evidence,
            conflicts=conflicts,
            policy_meta=policy_meta,
            weights=weights,
            require_official_source=req.options.require_official_source,
        )
        return self._assemble_response(
            request_id=request_id,
            req=req,
            evidences=[],
            validation=type(
                "V",
                (),
                {
                    "conflicts": conflicts,
                    "warnings": warnings,
                    "missing_fields": missing_fields,
                    "policy_meta": policy_meta,
                },
            )(),
            score_result=score_result,
            answer=None,
            token_acc=token_acc,
        )

    def _assemble_response(
        self,
        *,
        request_id: str,
        req: CheckRequest,
        evidences: list,
        validation,
        score_result,
        answer: str | None,
        token_acc: TokenAccumulator,
        propagation_timeline=None,
        inference_chain=None,
        suspected_viral_claim: bool = False,
    ) -> CheckResponse:
        ev_models = [
            Evidence(
                source_url=e.source_url,
                source_name=e.source_name,
                source_type=e.source_type,
                authority_weight=e.authority_weight,
                agency=e.agency,
                agency_level=e.agency_level,
                published_at=e.published_at,
                snippet=e.snippet,
                support_level=e.support_level,
                is_primary=e.is_primary,
                is_independent=e.is_independent,
                content_fingerprint=e.content_fingerprint,
                claims_about=e.claims_about,
            )
            for e in evidences
        ]
        conflict_models = [Conflict(**c) for c in validation.conflicts if isinstance(c, dict)]

        bd = score_result.breakdown
        breakdown = ScoreBreakdown(
            source_authority=DimScore(
                score=bd["source_authority"].score,
                weight=bd["source_authority"].weight,
                weighted=bd["source_authority"].weighted,
            ),
            source_consistency=DimScore(
                score=bd["source_consistency"].score,
                weight=bd["source_consistency"].weight,
                weighted=bd["source_consistency"].weighted,
            ),
            freshness=DimScore(
                score=bd["freshness"].score, weight=bd["freshness"].weight, weighted=bd["freshness"].weighted
            ),
            completeness=DimScore(
                score=bd["completeness"].score,
                weight=bd["completeness"].weight,
                weighted=bd["completeness"].weighted,
            ),
            claim_clarity=DimScore(
                score=bd["claim_clarity"].score,
                weight=bd["claim_clarity"].weight,
                weighted=bd["claim_clarity"].weighted,
            ),
        )

        pm = validation.policy_meta or {}
        policy = PolicyMeta(**{k: v for k, v in pm.items() if v not in (None, "")}) if pm else None

        reasoning = "； ".join(score_result.notes[:5]) if score_result.notes else "无足够证据生成详细推理"

        from .schemas import ConfidenceInterval

        ci = ConfidenceInterval(
            lo=score_result.confidence_lo,
            hi=score_result.confidence_hi,
            method="bootstrap_5dim_n200",
            note=f"宽度 {score_result.confidence_hi - score_result.confidence_lo}; 越窄越稳健",
        )
        return CheckResponse(
            request_id=request_id,
            input=req.input_text(),
            mode=req.mode,
            verdict=score_result.verdict,
            confidence=score_result.confidence,
            confidence_level=score_result.confidence_level,
            confidence_interval=ci,
            has_official_source=score_result.has_official_source,
            policy_status=score_result.policy_status,
            policy=policy,
            missing_fields=score_result.missing_fields or validation.missing_fields,
            warnings=list(validation.warnings),
            facts=[],
            evidence=ev_models,
            conflicts=conflict_models,
            score_breakdown=breakdown,
            gating_applied=score_result.gating_applied,
            reasoning_summary=reasoning,
            answer=answer,
            collected_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            cache_hit=False,
            propagation_timeline=propagation_timeline,
            inference_chain=inference_chain,
            suspected_viral_claim=suspected_viral_claim,
            token_usage=TokenUsage(
                provider=token_acc.provider,
                model=token_acc.model,
                prompt_tokens=token_acc.prompt_tokens,
                completion_tokens=token_acc.completion_tokens,
                total_tokens=token_acc.total_tokens,
                search_calls=token_acc.search_calls,
                fetch_calls=token_acc.fetch_calls,
            ),
        )
