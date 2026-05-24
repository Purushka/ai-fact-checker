"""评分引擎：纯规则、确定性。

输入（stdin JSON）：
{
  "claim": "...",
  "mode": "policy",
  "weights": {...} | null,
  "require_official_source": true,
  "evidence": [
    { "url": "...", "source_type": "official", "authority_weight": 0.95,
      "support_level": "strong", "published_at": "2025-03-12",
      "is_independent": true, "content_fingerprint": "...",
      "claims_about": { "title": "...", "issuing_agency": "...", "publish_date": "...", ... } }
  ],
  "conflicts": [ { "field": "benefit_amount", "values": [...], "level": "high" } ],
  "policy_meta": { "expire_date": null, "superseded_by": null },
  "today": "2026-05-21"
}

输出（stdout JSON）：完整 score_breakdown + verdict + confidence + gating_applied + policy_status。

CLI：
    cat input.json | python score.py
    python score.py --file input.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_PATH = ROOT / "data" / "completeness_templates.json"

DEFAULT_WEIGHTS = {
    "source_authority":  0.40,
    "source_consistency": 0.30,
    "freshness":         0.20,
    "completeness":      0.10,
    "claim_clarity":     0.00,
}


@dataclass
class DimScore:
    score: float
    weight: float
    weighted: float


@dataclass
class ScoreResult:
    verdict: str
    confidence: int
    confidence_level: str
    policy_status: str | None
    has_official_source: bool
    score_breakdown: dict
    gating_applied: list[str]
    formula: str
    notes: list[str] = field(default_factory=list)


def _validate_weights(w: dict) -> None:
    if any(v < 0 for v in w.values()):
        raise ValueError("INVALID_SCORING_WEIGHTS: 权重不能为负")
    total = sum(w.values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(f"INVALID_SCORING_WEIGHTS: 权重和必须为 1.0（实际 {total:.4f}）")
    unknown = set(w.keys()) - set(DEFAULT_WEIGHTS.keys())
    if unknown:
        raise ValueError(f"INVALID_SCORING_WEIGHTS: 未知维度 {unknown}")


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def _independent_evidence(evidence: list[dict]) -> list[dict]:
    seen_fingerprints = set()
    independent = []
    for e in evidence:
        fp = e.get("content_fingerprint")
        if fp and fp in seen_fingerprints:
            continue
        if fp:
            seen_fingerprints.add(fp)
        independent.append(e)
    return independent


def _score_authority(evidence: list[dict]) -> tuple[float, list[str]]:
    notes: list[str] = []
    non_contradict = [e for e in evidence if e.get("support_level") != "contradicted"]
    if not non_contradict:
        return 0.0, ["无支持型证据"]
    weights = [e.get("authority_weight", 0.0) for e in non_contradict]
    primary = max(weights) * 100
    high_count = sum(1 for w in weights if w >= 0.9)
    bonus = min(20, max(0, (high_count - 1) * 5))
    score = min(100, primary + bonus)
    if high_count >= 2:
        notes.append(f"{high_count} 个高权威源")
    return score, notes


def _score_consistency(evidence: list[dict]) -> tuple[float, list[str]]:
    notes: list[str] = []
    ind = _independent_evidence(evidence)
    n = len(ind)
    if n == 0:
        return 0.0, ["无独立证据"]
    if n == 1:
        return 50.0, ["仅 1 个独立源（孤证）"]
    support = sum(1 for e in ind if e.get("support_level") in ("strong", "weak"))
    contradict = sum(1 for e in ind if e.get("support_level") == "contradicted")
    ratio = support / n
    if ratio >= 0.8:
        score = 90 + min(10, n - 2)
    elif ratio >= 0.6:
        score = 70
    elif ratio >= 0.4:
        score = 50
    else:
        score = max(20, 50 - contradict * 10)
    notes.append(f"{support}/{n} 独立源支持")
    return float(score), notes


def _score_freshness(evidence: list[dict], mode: str, policy_meta: dict, today: date) -> tuple[float, str | None, list[str]]:
    notes: list[str] = []
    policy_status: str | None = None

    if mode == "policy":
        expire = _parse_iso_date(policy_meta.get("expire_date"))
        superseded = policy_meta.get("superseded_by")
        if expire and expire < today:
            policy_status = "expired"
            notes.append(f"政策已于 {expire} 过期")
            return 10.0, policy_status, notes
        if superseded:
            policy_status = "superseded"
            notes.append(f"已被新文 {superseded} 替代")
            return 20.0, policy_status, notes
        official = [e for e in evidence if e.get("source_type") in ("official", "regulator")]
        if not official:
            return 30.0, None, ["缺乏官方源时间锚定"]
        latest = max((_parse_iso_date(e.get("published_at")) for e in official if e.get("published_at")), default=None)
        if not latest:
            return 30.0, None, ["权威源未标注发布时间"]
        age_months = (today - latest).days / 30.0
        if age_months <= 12:
            score = 90.0
        elif age_months <= 24:
            score = 75.0
        elif age_months <= 48:
            score = 55.0
        else:
            score = 35.0
        notes.append(f"最新官方源 {latest}（{age_months:.0f} 月前）")
        policy_status = "active"
        return score, policy_status, notes

    dates = [_parse_iso_date(e.get("published_at")) for e in evidence if e.get("published_at")]
    dates = [d for d in dates if d]
    if not dates:
        return 50.0, None, ["证据无时间锚定"]
    latest = max(dates)
    age_days = (today - latest).days
    if mode == "numeric" or mode == "entity_status":
        if age_days <= 90:
            score = 90.0
        elif age_days <= 180:
            score = 75.0
        elif age_days <= 365:
            score = 55.0
        else:
            score = 35.0
    else:
        if age_days <= 365:
            score = 90.0
        else:
            score = 70.0
    notes.append(f"最新证据 {latest}（{age_days} 天前）")
    return score, None, notes


def _score_completeness(evidence: list[dict], mode: str) -> tuple[float, list[str], list[str]]:
    with TEMPLATES_PATH.open(encoding="utf-8") as f:
        templates = json.load(f)["templates"]
    template = templates.get(mode, templates["general"])
    fields = template["fields"]
    required_keys = {f["key"] for f in fields if f.get("required")}
    all_keys = {f["key"] for f in fields}

    filled_keys: set[str] = set()
    for e in evidence:
        claims_about = e.get("claims_about") or {}
        for k, v in claims_about.items():
            if v not in (None, "", [], {}):
                filled_keys.add(k)

    missing_required = required_keys - filled_keys
    base = (len(filled_keys & all_keys) / max(1, len(all_keys))) * 100
    penalty = min(40, len(missing_required) * 15)
    score = max(0.0, base - penalty)
    notes = [f"{len(filled_keys & all_keys)}/{len(all_keys)} 字段填出"]
    if missing_required:
        notes.append(f"缺必填: {sorted(missing_required)}")
    missing_fields_list = sorted(all_keys - filled_keys)
    return score, notes, missing_fields_list


def _score_clarity(claim: str) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 100.0
    vague = ["近年来", "近期", "大幅", "显著", "较多", "一定程度上", "通常", "据悉"]
    if any(v in claim for v in vague):
        score -= 20
        notes.append("含模糊表达")
    import re as _re
    has_time = bool(_re.search(r"20\d{2}|今年|去年|本月|本季度", claim))
    if not has_time:
        score -= 30
        notes.append("缺时间锚点")
    subjective_markers = ["好", "棒", "厉害", "优秀", "最好", "最差", "最佳"]
    if any(m in claim for m in subjective_markers) and "评" not in claim:
        score = 0
        notes.append("疑似主观评价")
    if claim.count("，") + claim.count(",") >= 3:
        score -= 15
        notes.append("复合命题，建议拆分")
    return max(0.0, score), notes


def _has_official_source(evidence: list[dict]) -> bool:
    return any(e.get("source_type") in ("official", "regulator") and e.get("support_level") != "contradicted"
               for e in evidence)


def _decide_verdict(
    raw_conf: float,
    clarity_score: float,
    evidence: list[dict],
    has_official: bool,
    conflicts: list[dict],
    policy_status: str | None,
    mode: str,
) -> tuple[str, list[str]]:
    gating: list[str] = []

    if clarity_score == 0:
        return "out_of_scope", ["clarity_zero_subjective"]

    ind = _independent_evidence(evidence)
    if not ind:
        return "unverifiable", ["no_evidence"]

    unresolved = [c for c in conflicts if c.get("resolved") is False]
    if unresolved:
        return "conflicting", ["unresolved_conflict"]

    if policy_status == "expired":
        return "outdated", ["policy_expired"]
    if policy_status == "superseded":
        return "outdated", ["policy_superseded"]

    contradict_strong = [e for e in ind
                         if e.get("support_level") == "contradicted"
                         and e.get("authority_weight", 0) >= 0.9]
    support_count = sum(1 for e in ind if e.get("support_level") in ("strong", "weak"))
    if contradict_strong and len(contradict_strong) >= max(support_count, 1):
        return "contradicted", ["authority_contradicted"]

    if raw_conf >= 75 and has_official:
        return "supported", gating
    if raw_conf >= 60:
        return "partially_supported", gating
    if raw_conf >= 40:
        return "partially_supported", gating
    return "unverifiable", ["low_confidence"]


def _apply_gating(
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

    unresolved = [c for c in conflicts if c.get("resolved") is False]
    if unresolved:
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


def score(payload: dict) -> ScoreResult:
    claim = payload.get("claim", "")
    mode = payload.get("mode", "general")
    weights = payload.get("weights") or DEFAULT_WEIGHTS
    _validate_weights(weights)
    require_official = payload.get("require_official_source", mode == "policy")
    evidence = payload.get("evidence", []) or []
    conflicts = payload.get("conflicts", []) or []
    policy_meta = payload.get("policy_meta", {}) or {}
    today_str = payload.get("today")
    today = _parse_iso_date(today_str) or date.today()

    auth_score, auth_notes = _score_authority(evidence)
    cons_score, cons_notes = _score_consistency(evidence)
    fresh_score, policy_status, fresh_notes = _score_freshness(evidence, mode, policy_meta, today)
    comp_score, comp_notes, _missing = _score_completeness(evidence, mode)
    clar_score, clar_notes = _score_clarity(claim)

    dim = {
        "source_authority":  DimScore(auth_score, weights["source_authority"],  auth_score * weights["source_authority"]),
        "source_consistency": DimScore(cons_score, weights["source_consistency"], cons_score * weights["source_consistency"]),
        "freshness":         DimScore(fresh_score, weights["freshness"],         fresh_score * weights["freshness"]),
        "completeness":      DimScore(comp_score, weights["completeness"],      comp_score * weights["completeness"]),
        "claim_clarity":     DimScore(clar_score, weights["claim_clarity"],     clar_score * weights["claim_clarity"]),
    }
    raw_conf = sum(d.weighted for d in dim.values())

    has_official = _has_official_source(evidence)
    verdict, verdict_gating = _decide_verdict(
        raw_conf, clar_score, evidence, has_official, conflicts, policy_status, mode,
    )

    final_conf, verdict, gating = _apply_gating(
        raw_conf, verdict, has_official, mode, require_official, policy_status, conflicts,
    )
    gating = verdict_gating + gating

    if verdict in ("unverifiable", "out_of_scope") and final_conf > 30:
        final_conf = min(final_conf, 30)

    if final_conf >= 75:
        level = "high"
    elif final_conf >= 40:
        level = "medium"
    else:
        level = "low"

    breakdown = {
        k: {"score": round(v.score, 1), "weight": v.weight, "weighted": round(v.weighted, 2)}
        for k, v in dim.items()
    }

    notes = []
    notes.extend([f"[authority] {n}" for n in auth_notes])
    notes.extend([f"[consistency] {n}" for n in cons_notes])
    notes.extend([f"[freshness] {n}" for n in fresh_notes])
    notes.extend([f"[completeness] {n}" for n in comp_notes])
    notes.extend([f"[clarity] {n}" for n in clar_notes])

    return ScoreResult(
        verdict=verdict,
        confidence=final_conf,
        confidence_level=level,
        policy_status=policy_status,
        has_official_source=has_official,
        score_breakdown=breakdown,
        gating_applied=gating,
        formula="confidence = Σ(score_i × weight_i)，再经 gating rules 封顶/降级",
        notes=notes,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="JSON 输入文件；不指定则从 stdin 读")
    args = ap.parse_args()
    if args.file:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    else:
        payload = json.loads(sys.stdin.read())
    try:
        result = score(payload)
    except ValueError as e:
        sys.stdout.write(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    sys.stdout.write(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
