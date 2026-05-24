"""评分引擎（生产版）。从 skill 的 score.py 重构为类，可注入今日日期、模板缓存。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
TEMPLATES_PATH = DATA_DIR / "completeness_templates.json"

DEFAULT_WEIGHTS: dict[str, float] = {
    "source_authority": 0.40,
    "source_consistency": 0.30,
    "freshness": 0.20,
    "completeness": 0.10,
    "claim_clarity": 0.00,
}


@dataclass
class DimResult:
    score: float
    weight: float
    weighted: float
    notes: list[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    verdict: str
    confidence: int
    confidence_level: str
    policy_status: str | None
    has_official_source: bool
    breakdown: dict[str, DimResult]
    gating_applied: list[str]
    formula: str
    notes: list[str]
    missing_fields: list[str]
    confidence_lo: int = 0
    confidence_hi: int = 100


class ScoreEngine:
    def __init__(self, templates_path: Path = TEMPLATES_PATH) -> None:
        with templates_path.open(encoding="utf-8") as f:
            self._templates = json.load(f)["templates"]

    @staticmethod
    def validate_weights(weights: dict[str, float]) -> None:
        if any(v < 0 for v in weights.values()):
            raise ValueError("INVALID_SCORING_WEIGHTS: 权重不能为负")
        unknown = set(weights) - set(DEFAULT_WEIGHTS)
        if unknown:
            raise ValueError(f"INVALID_SCORING_WEIGHTS: 未知维度 {unknown}")
        total = sum(weights.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"INVALID_SCORING_WEIGHTS: 权重和必须为 1.0（实际 {total:.4f}）")

    @staticmethod
    def _parse_date(s: str | None) -> date | None:
        if not s:
            return None
        try:
            return date.fromisoformat(s[:10])
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _independent(evidence: list[dict]) -> list[dict]:
        seen = set()
        out = []
        for e in evidence:
            fp = e.get("content_fingerprint")
            if fp and fp in seen:
                continue
            if fp:
                seen.add(fp)
            out.append(e)
        return out

    def _authority(self, evidence: list[dict]) -> tuple[float, list[str]]:
        notes = []
        non_c = [e for e in evidence if e.get("support_level") != "contradicted"]
        if not non_c:
            return 0.0, ["无支持型证据"]
        weights = [e.get("authority_weight", 0.0) for e in non_c]
        primary = max(weights) * 100
        high = sum(1 for w in weights if w >= 0.9)
        bonus = min(20, max(0, (high - 1) * 5))
        score = min(100, primary + bonus)
        if high >= 2:
            notes.append(f"{high} 个高权威源")
        return score, notes

    def _consistency(self, evidence: list[dict]) -> tuple[float, list[str]]:
        ind = self._independent(evidence)
        if not ind:
            return 0.0, ["无独立证据"]

        relevant = [e for e in ind if e.get("support_level") in ("strong", "weak", "contradicted")]
        n_rel = len(relevant)
        if n_rel == 0:
            return 30.0, [f"{len(ind)} 个证据全部 neutral，无可定性"]
        if n_rel == 1:
            sole = relevant[0]
            authority = sole.get("authority_weight", 0)
            support = sole.get("support_level")
            if support in ("strong", "contradicted") and authority >= 0.95:
                return 70.0, ["单 tier1 权威源（>=0.95）"]
            if support == "strong" and authority >= 0.8:
                return 65.0, ["单权威媒体源（>=0.8）"]
            return 50.0, ["孤证不立"]
        support = sum(1 for e in relevant if e.get("support_level") in ("strong", "weak"))
        contradict = sum(1 for e in relevant if e.get("support_level") == "contradicted")
        ratio = support / n_rel
        if ratio >= 0.8:
            score = 90 + min(10, n_rel - 2)
        elif ratio >= 0.6:
            score = 70
        elif ratio >= 0.4:
            score = 50
        else:
            score = max(20, 50 - contradict * 10)
        return float(score), [f"{support}/{n_rel} 相关源支持（{len(ind) - n_rel} neutral 已排除）"]

    def _freshness(
        self, evidence: list[dict], mode: str, policy_meta: dict, today: date
    ) -> tuple[float, str | None, list[str]]:
        if mode == "policy":
            expire = self._parse_date(policy_meta.get("expire_date"))
            superseded = policy_meta.get("superseded_by")
            deadline = self._parse_date(policy_meta.get("deadline"))
            if expire and expire < today:
                return 10.0, "expired", [f"政策已于 {expire} 过期"]
            if superseded:
                return 20.0, "superseded", [f"已被 {superseded} 替代"]
            official = [e for e in evidence if e.get("source_type") in ("official", "regulator")]
            if not official:
                return 30.0, None, ["缺乏官方源时间锚定"]
            dates = [self._parse_date(e.get("published_at")) for e in official if e.get("published_at")]
            dates = [d for d in dates if d]
            if not dates:
                return 30.0, None, ["权威源未标注发布时间"]
            latest = max(dates)
            age_m = (today - latest).days / 30.0
            time_limited = (expire is not None) or (deadline is not None)
            if time_limited:
                if age_m <= 12:
                    s = 90.0
                elif age_m <= 24:
                    s = 75.0
                elif age_m <= 48:
                    s = 55.0
                else:
                    s = 35.0
                kind = "有期限政策"
            else:
                if age_m <= 24:
                    s = 95.0
                elif age_m <= 60:
                    s = 88.0
                elif age_m <= 120:
                    s = 80.0
                else:
                    s = 72.0
                kind = "永久性法规（无过期/截止）"
            return s, "active", [f"最新官方源 {latest}（{age_m:.0f} 月前，{kind}）"]

        dates = [self._parse_date(e.get("published_at")) for e in evidence if e.get("published_at")]
        dates = [d for d in dates if d]
        if not dates:
            return 50.0, None, ["证据无时间锚定"]
        latest = max(dates)
        age_d = (today - latest).days
        if mode in ("numeric", "entity_status"):
            if age_d <= 90:
                s = 90.0
            elif age_d <= 180:
                s = 75.0
            elif age_d <= 365:
                s = 55.0
            else:
                s = 35.0
        else:
            s = 90.0 if age_d <= 365 else 70.0
        return s, None, [f"最新证据 {latest}（{age_d} 天前）"]

    def _completeness(self, evidence: list[dict], mode: str) -> tuple[float, list[str], list[str]]:
        template = self._templates.get(mode, self._templates["general"])
        fields = template["fields"]
        required = {f["key"] for f in fields if f.get("required")}
        all_keys = {f["key"] for f in fields}

        filled = set()
        for e in evidence:
            ca = e.get("claims_about") or {}
            for k, v in ca.items():
                if v not in (None, "", [], {}):
                    filled.add(k)

        missing_required = required - filled
        base = (len(filled & all_keys) / max(1, len(all_keys))) * 100
        penalty = min(40, len(missing_required) * 15)
        score = max(0.0, base - penalty)
        notes = [f"{len(filled & all_keys)}/{len(all_keys)} 字段填出"]
        if missing_required:
            notes.append(f"缺必填: {sorted(missing_required)}")
        return score, notes, sorted(all_keys - filled)

    def _clarity(self, claim: str) -> tuple[float, list[str]]:
        import re

        notes = []
        score = 100.0
        vague = ["近年来", "近期", "大幅", "显著", "较多", "据悉", "通常"]
        if any(v in claim for v in vague):
            score -= 20
            notes.append("含模糊表达")
        if not re.search(r"20\d{2}|今年|去年|本月|本季度", claim):
            score -= 30
            notes.append("缺时间锚点")
        subjective = ["好", "棒", "厉害", "优秀", "最好", "最差", "最佳", "最适合"]
        if any(m in claim for m in subjective) and "评" not in claim:
            return 0.0, ["疑似主观评价"]
        if claim.count("，") + claim.count(",") >= 3:
            score -= 15
            notes.append("复合命题")
        return max(0.0, score), notes

    @staticmethod
    def _has_official(evidence: list[dict]) -> bool:
        return any(
            e.get("source_type") in ("official", "regulator") and e.get("support_level") != "contradicted"
            for e in evidence
        )

    def _decide_verdict(
        self,
        raw_conf: float,
        clarity: float,
        evidence: list[dict],
        has_official: bool,
        conflicts: list[dict],
        policy_status: str | None,
        mode: str = "general",
    ) -> tuple[str, list[str]]:
        if clarity == 0:
            return "out_of_scope", ["clarity_zero_subjective"]
        ind = self._independent(evidence)
        if not ind:
            return "unverifiable", ["no_evidence"]
        unresolved = [c for c in conflicts if not c.get("resolved", False)]
        if unresolved:
            return "conflicting", ["unresolved_conflict"]
        if policy_status == "expired":
            return "outdated", ["policy_expired"]
        if policy_status == "superseded":
            return "outdated", ["policy_superseded"]
        contradict_strong = [
            e
            for e in ind
            if e.get("support_level") == "contradicted" and e.get("authority_weight", 0) >= 0.85
        ]
        support = sum(1 for e in ind if e.get("support_level") in ("strong", "weak"))
        contradict_count = sum(1 for e in ind if e.get("support_level") == "contradicted")
        if support == 0 and contradict_count == 0:
            return "unverifiable", ["all_neutral_no_relevant_position"]
        if contradict_strong and len(contradict_strong) >= max(support, 1):
            return "contradicted", ["authority_contradicted"]
        if raw_conf >= 75 and has_official:
            return "supported", []
        non_policy_thr = 65
        if raw_conf >= non_policy_thr and mode != "policy":
            max_auth = max((e.get("authority_weight", 0) for e in ind), default=0)
            if max_auth >= 0.7:
                return "supported", ["non_policy_relaxed_official"]
        # 当有多源 strong support 时，即使总分稍低也判 supported
        strong_supports = [e for e in ind if e.get("support_level") == "strong"]
        max_auth = max((e.get("authority_weight", 0) for e in ind), default=0)
        if len(strong_supports) >= 2 and max_auth >= 0.85 and contradict_count == 0:
            return "supported", ["multi_strong_support_relaxed"]
        if raw_conf >= 40:
            return "partially_supported", []
        return "unverifiable", ["low_confidence"]

    def _apply_gating(
        self,
        raw_conf: float,
        verdict: str,
        has_official: bool,
        mode: str,
        require_official: bool,
        policy_status: str | None,
        conflicts: list[dict],
    ) -> tuple[int, str, list[str]]:
        gating: list[str] = []
        conf = raw_conf

        if require_official and not has_official:
            conf = min(conf, 40)
            gating.append("no_official_source_capped_40")
        if [c for c in conflicts if not c.get("resolved", False)]:
            conf = min(conf, 35)
            gating.append("conflict_capped_35")
        if policy_status == "expired":
            conf = min(conf, 25)
            gating.append("policy_expired_capped_25")
        elif policy_status == "superseded":
            conf = min(conf, 30)
            gating.append("policy_superseded_capped_30")
        if mode == "policy" and not has_official:
            conf = min(conf, 35)
            gating.append("policy_no_official_capped_35")
            if verdict == "supported":
                verdict = "partially_supported"

        return int(round(conf)), verdict, gating

    def score(
        self,
        *,
        claim: str,
        mode: str,
        evidence: list[dict],
        conflicts: list[dict] | None = None,
        policy_meta: dict | None = None,
        weights: dict[str, float] | None = None,
        require_official_source: bool | None = None,
        today: date | None = None,
    ) -> ScoreResult:
        weights = weights or DEFAULT_WEIGHTS
        self.validate_weights(weights)
        conflicts = conflicts or []
        policy_meta = policy_meta or {}
        today = today or date.today()
        if require_official_source is None:
            require_official_source = mode == "policy"

        auth_s, auth_n = self._authority(evidence)
        cons_s, cons_n = self._consistency(evidence)
        fresh_s, policy_status, fresh_n = self._freshness(evidence, mode, policy_meta, today)
        comp_s, comp_n, missing = self._completeness(evidence, mode)
        clar_s, clar_n = self._clarity(claim)

        breakdown = {
            "source_authority": DimResult(
                auth_s, weights["source_authority"], auth_s * weights["source_authority"], auth_n
            ),
            "source_consistency": DimResult(
                cons_s, weights["source_consistency"], cons_s * weights["source_consistency"], cons_n
            ),
            "freshness": DimResult(fresh_s, weights["freshness"], fresh_s * weights["freshness"], fresh_n),
            "completeness": DimResult(
                comp_s, weights["completeness"], comp_s * weights["completeness"], comp_n
            ),
            "claim_clarity": DimResult(
                clar_s, weights["claim_clarity"], clar_s * weights["claim_clarity"], clar_n
            ),
        }
        raw_conf = sum(d.weighted for d in breakdown.values())
        has_official = self._has_official(evidence)
        verdict, vg = self._decide_verdict(
            raw_conf, clar_s, evidence, has_official, conflicts, policy_status, mode
        )
        final_conf, verdict, gg = self._apply_gating(
            raw_conf, verdict, has_official, mode, require_official_source, policy_status, conflicts
        )
        if verdict in ("unverifiable", "out_of_scope") and final_conf > 30:
            final_conf = min(final_conf, 30)

        level = "high" if final_conf >= 75 else "medium" if final_conf >= 40 else "low"
        all_notes = []
        for k, d in breakdown.items():
            for n in d.notes:
                all_notes.append(f"[{k}] {n}")

        # 计算 CI：bootstrap on 5 dim scores + 模拟 evidence 子集采样
        ci_lo, ci_hi = self._bootstrap_ci(
            breakdown=breakdown,
            weights=weights,
            evidence=evidence,
            conflicts=conflicts,
            mode=mode,
            has_official=has_official,
            require_official=require_official_source,
            policy_status=policy_status,
            final_conf=final_conf,
        )

        return ScoreResult(
            verdict=verdict,
            confidence=final_conf,
            confidence_level=level,
            policy_status=policy_status,
            has_official_source=has_official,
            breakdown=breakdown,
            gating_applied=vg + gg,
            formula="confidence = Σ(score_i × weight_i)，gating rules 后封顶/降级",
            notes=all_notes,
            missing_fields=missing,
            confidence_lo=ci_lo,
            confidence_hi=ci_hi,
        )

    def _bootstrap_ci(
        self,
        *,
        breakdown: dict[str, DimResult],
        weights: dict[str, float],
        evidence: list[dict],
        conflicts: list[dict],
        mode: str,
        has_official: bool,
        require_official: bool,
        policy_status: str | None,
        final_conf: int,
        n_boot: int = 200,
    ) -> tuple[int, int]:
        """Bootstrap 置信区间 — 对 5 维度分数加 ±10% 噪声、随机子采样 evidence 各 n_boot 次。"""
        import random

        rng = random.Random(42)
        boot_confs: list[float] = []
        base_scores = {k: d.score for k, d in breakdown.items()}
        for _ in range(n_boot):
            perturbed = {}
            for k, base in base_scores.items():
                noise = rng.uniform(-0.10, 0.10)  # ±10% 噪声
                perturbed[k] = max(0, min(100, base * (1 + noise)))
            raw = sum(perturbed[k] * weights[k] for k in perturbed)
            # 简化 gating（不重算 evidence 子集）
            c = raw
            if require_official and not has_official:
                c = min(c, 40)
            if [cf for cf in conflicts if not cf.get("resolved", False)]:
                c = min(c, 35)
            if policy_status == "expired":
                c = min(c, 25)
            elif policy_status == "superseded":
                c = min(c, 30)
            if mode == "policy" and not has_official:
                c = min(c, 35)
            boot_confs.append(c)
        boot_confs.sort()
        lo = int(round(boot_confs[int(n_boot * 0.025)]))
        hi = int(round(boot_confs[int(n_boot * 0.975) - 1]))
        # 保证 lo <= final_conf <= hi（点估计应在区间内）
        lo = max(0, min(lo, final_conf))
        hi = max(final_conf, min(100, hi))
        return lo, hi
