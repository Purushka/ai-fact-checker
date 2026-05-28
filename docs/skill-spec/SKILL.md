---
name: fact-check
description: 对单条事实声明或自然语言问题进行联网核查，返回结构化 verdict、置信度、证据链、政策要素和评分明细。适合政策核查、行业数据核查、知识库入库前验证、对话流回答前的事实校验。支持调用方传入权重覆盖默认评分公式。
---

# 事实核查 Skill

## 何时使用

满足任一条件即应使用本 skill 而非直接凭训练知识回答：

- 用户问句涉及具体政策、补贴金额、申请条件、有效期、文号、发文机关
- 涉及 2024 年之后的事件、数据、规章
- 涉及具体企业、人物、产品的当前状态
- 涉及数字、比例、排名、市场份额的精确数值
- 上游系统传入需入库的政策文本或行业方案声明

## 何时不使用

- 主观评价（"某产品好不好"）
- 通用常识（"水的沸点是多少"）
- 编程/算法问题
- 已经在用户输入中提供完整且权威来源的事实陈述（只需引用即可）

## 输入

支持两种入口：

```json
{
  "claim": "2025 年青岛市对首次创业的高校毕业生发放 5000 元一次性创业补贴",
  "mode": "policy",
  "scoring_weights": {
    "source_authority": 0.40,
    "source_consistency": 0.30,
    "freshness": 0.20,
    "completeness": 0.10,
    "claim_clarity": 0.00
  },
  "require_official_source": true,
  "max_sources": 5
}
```

或：

```json
{
  "question": "我所在的青岛，应届毕业生创业有什么补贴？",
  "mode": "policy"
}
```

`mode` 可选值：`general` / `policy` / `numeric` / `entity_status` / `event`，缺省为 `general`。

## 执行流程

严格按 `pipeline.md` 中的 7 步执行，每步完成后用一行明文向用户报告进度。

1. **QueryPlan** — 解析 claim / question，抽取实体、时间、地域，拆分子核查点，生成搜索词
2. **Search** — 用 WebSearch 工具按生成的搜索词检索，最多 `max_sources × 2` 条 URL，去重后初筛
3. **Fetch** — 用 WebFetch 工具抓取每个 URL 的主要内容
4. **Extract** — 从每个页面抽取结构化事实片段，按 `data/completeness_templates.json` 中对应模板填字段；用 `scripts/source_classify.py` 给每个源打权威分
5. **CrossValidate** — 并行做 4 个检查：官方一致性、官方 vs 非官方、矛盾检测、要素遗漏；冲突按"官方层级 > 时间新旧 > 原始性"仲裁
6. **Score** — 调 `scripts/score.py`，传入证据列表和权重，得到 5 维分数、加权 confidence、gating 应用情况、verdict
7. **Generate** — 输出结构化 JSON 结果 + 可选自然语言 answer + warnings

## 输出契约

无论输入 claim 还是 question，最终输出固定结构：

```json
{
  "input": "原始输入",
  "mode": "policy",
  "verdict": "supported | partially_supported | contradicted | outdated | unverifiable | conflicting | out_of_scope",
  "confidence": 87,
  "confidence_level": "high | medium | low",
  "has_official_source": true,
  "policy_status": "active | expired | superseded | draft | unknown",
  "policy": {
    "title": "",
    "issuing_agency": "",
    "agency_level": "national | province | city | district",
    "document_number": "",
    "publish_date": "",
    "effective_date": "",
    "expire_date": null,
    "applicable_region": [],
    "applicable_subjects": [],
    "benefit_amount": "",
    "application_conditions": [],
    "required_materials": [],
    "deadline": ""
  },
  "missing_fields": [],
  "warnings": [],
  "evidence": [
    {
      "source_url": "",
      "source_name": "",
      "source_type": "official | regulator | industry_association | authoritative_media | mainstream_media | industry_report | social_media",
      "authority_weight": 0.95,
      "agency": "",
      "published_at": "",
      "snippet": "",
      "support_level": "strong | weak | neutral | contradicted",
      "is_primary": true
    }
  ],
  "conflicts": [],
  "score_breakdown": {
    "source_authority": { "score": 96, "weight": 0.40, "weighted": 38.4 },
    "source_consistency": { "score": 90, "weight": 0.30, "weighted": 27.0 },
    "freshness": { "score": 88, "weight": 0.20, "weighted": 17.6 },
    "completeness": { "score": 84, "weight": 0.10, "weighted": 8.4 },
    "claim_clarity": { "score": 90, "weight": 0.00, "weighted": 0.0 }
  },
  "formula": "confidence = Σ(score_i × weight_i)，gating rules 后封顶/降级",
  "gating_applied": [],
  "reasoning_summary": "中文一段话解释判断依据",
  "answer": "（仅当输入是 question 时填充）自然语言答案，必须引用 evidence 中的 source_url",
  "collected_at": "ISO8601"
}
```

`mode != "policy"` 时 `policy` 和 `policy_status` 字段可为 `null`，但其它字段都必填。

## 关键行为规则

1. **宁可错杀不可错放**（边界更精确版）：
   - **完全没有相关 evidence** → `unverifiable`
   - **找到了反证 evidence**（即权威源说的与 claim 不符）→ `contradicted`，不要降级为 unverifiable
   - 错标 supported 比错标 unverifiable 代价大；但错标 unverifiable（当应当 contradicted 时）也是失误
2. **政策类必须 has_official_source = true 才能 verdict = supported**：否则 confidence 强制 ≤ 40（gating 规则）
3. **冲突未解决时 verdict = conflicting**：confidence 强制 ≤ 35
4. **二手引用必须降权**：检测到"据 XXX 报道"、"引自 XXX"时尝试找原始源，原始源缺失则证据权重 × 0.7
5. **同源去重**：相同新闻稿在多家媒体出现，按内容指纹合并，不算多条独立证据
6. **时间戳必须明确**：政策类没有发布时间或更新时间 → freshness_score ≤ 50，并加 warning
7. **Identifier 反证**：claim 含具体识别码（文号、版本、人名+职位）时，若权威源出现同主题但 identifier 不同的文件 → 强 `contradicted` 信号，不要因 claim 的 identifier 在 evidence 中"未直接出现"就升格为 unverifiable
8. **数据修订识别**：搜索结果含"修订/修正/重新核算/按新口径调整"且涉及 claim 数字 → 必须抽取"原→新"，若 claim 用的是被修订前的旧数据 → `contradicted` 或 `partially_supported`（取决于 claim 是否声明为"最终"）
9. **已超替法规识别**：法规类 claim 必须验证当前生效版本——若搜到更晚的修订/新版，原 claim 的"当前生效是 X 年版本" → `outdated`，policy_status=superseded
10. **反幻觉硬约束**：所有 claims_about 字段必须能在搜索/抓取页面的原文中找到原文支撑；禁止凭训练知识补全字段

## 配套文件

- `pipeline.md` — 每步详细执行规则
- `scoring.md` — 评分公式、gating 规则、verdict 决策树
- `data/source_authority.json` — 内置权威源库（200+ 中文政府/媒体/学术源）
- `data/content_farm_blacklist.json` — 内容农场黑名单
- `data/completeness_templates.json` — 各 mode 的字段模板
- `scripts/normalize.py` — claim 归一化、cache key 计算
- `scripts/source_classify.py` — URL → source_type + authority_weight
- `scripts/score.py` — 评分引擎（纯规则、确定性）
- `scripts/eval.py` — 跑测试集
- `scripts/compare.py` — skill 判断 vs 裸 LLM 判断的双盲对比
- `tests/test_cases.jsonl` — 测试集
- `reports/` — 测试报告输出目录

## 调用示例

```
Skill(skill="fact-check", args='{"claim":"2025年青岛市对首次创业的高校毕业生发放5000元一次性创业补贴","mode":"policy"}')
```
