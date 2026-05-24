# 评分规则

## 评分公式

```
raw_confidence = Σ(score_i × weight_i)    其中 i ∈ {authority, consistency, freshness, completeness, clarity}
final_confidence = apply_gating(raw_confidence, gating_rules)
```

## 默认权重

```python
DEFAULT_WEIGHTS = {
    "source_authority":  0.40,
    "source_consistency": 0.30,
    "freshness":         0.20,
    "completeness":      0.10,
    "claim_clarity":     0.00,
}
```

调用方可在 `scoring_weights` 中覆盖。校验规则：
- 必须求和等于 1.0（误差 ≤ 0.001）
- 不能为负
- 不能含未知维度

校验失败抛 `INVALID_SCORING_WEIGHTS` 错误。

## 5 维计算规则

### 1. source_authority（来源权威性，0-100）

```python
top_authorities = [e.authority_weight for e in evidence if e.support_level != 'contradicted']
if not top_authorities:
    score = 0
else:
    # 取最高权威性源加权，加上多个权威源的加成
    primary = max(top_authorities) * 100               # 0-100
    bonus = min(20, (len([a for a in top_authorities if a >= 0.9]) - 1) * 5)
    score = min(100, primary + bonus)
```

权威分映射表（在 `source_authority.json` 中）：
- 国务院、中央部委门户：1.0
- 省级政府、省级部门：0.95
- 市级政府、市级人社/税务/市监：0.90
- 区县级政府：0.85
- 行业主管协会：0.80
- 央媒（新华社、人民日报、央视）：0.75
- 主流财经媒体（财新、第一财经、21 经济）：0.65
- 其它备案媒体：0.55
- 自媒体、知乎、微信公众号：0.30
- 内容农场（黑名单）：0.0（直接丢弃）

### 2. source_consistency（多源一致性，0-100）

```python
# 已经过同源去重（内容指纹合并）后的独立证据数
independent_evidence_count = N
support_count   = 支持 claim 的独立证据数
neutral_count   = 中立证据数
contradict_count = 反驳 claim 的独立证据数

if N == 0:
    score = 0
elif N == 1:
    score = 50  # 孤证不立但不算冲突
else:
    agreement_ratio = support_count / N
    if agreement_ratio >= 0.8:
        score = 90 + min(10, N - 2)   # 越多独立源一致越高
    elif agreement_ratio >= 0.6:
        score = 70
    elif agreement_ratio >= 0.4:
        score = 50
    else:
        score = max(20, 50 - contradict_count * 10)
```

### 3. freshness（时效性，0-100）

按 claim 类型分别处理：

#### 历史事实（claim 带明确历史时间锚点，如"2023 年销量"）
```python
score = 95  # 历史事实不过期，只要 evidence 来源本身可信
if 任一 evidence 标注"数据已更正/修订" then score -= 30
```

#### 政策类
```python
publish = 最权威 evidence 的 publish_date
expire  = 政策标注的 expire_date

if expire and expire < today:
    score = 10   # 已过期
    policy_status = "expired"
elif 检测到 superseded by newer document:
    score = 20
    policy_status = "superseded"
elif publish is None:
    score = 30   # 来源没有发布时间是严重问题
    warning += "权威源未标注发布时间"
else:
    age_months = (today - publish).days / 30
    if age_months <= 12:
        score = 90
    elif age_months <= 24:
        score = 75
    elif age_months <= 48:
        score = 55
    else:
        score = 35
```

#### 状态类（"某公司 CEO 是谁"）
```python
最新 evidence 发布时间到今天的天数：
- ≤ 90 天：score = 90
- ≤ 180 天：score = 75
- ≤ 365 天：score = 55
- > 365 天：score = 35
```

#### 事件类（"X 公司 Y 月发布了 Z 产品"）
```python
score = 90 if evidence 提到该具体事件 else 30
```

### 4. completeness（要素完整度，0-100）

```python
template_fields = 按 mode 取模板的全部字段
filled_count = evidence 中能填出的字段数
required_fields = 模板中标记 required=true 的字段
missing_required = required_fields - filled_fields

base = (filled_count / len(template_fields)) * 100
penalty = min(40, len(missing_required) * 15)
score = max(0, base - penalty)
```

### 5. claim_clarity（事实表达清晰度，0-100）

```python
score = 100
if claim 含模糊词（"近年来"、"大幅"、"显著"、"较多"）：score -= 20
if claim 时间未锚定（无年份、季度、日期）：score -= 30
if claim 地域未锚定且原本是地域相关事实：score -= 20
if claim 是主观评价、价值判断：score = 0（直接判 out_of_scope）
if claim 包含多个独立命题但未拆分：score -= 15
score = max(0, score)
```

## Gating 规则

加权和算完后按下列规则封顶/降级（按顺序应用，取最严约束）：

```python
if no_evidence:
    verdict = "unverifiable"
    confidence = max(0, raw // 10)  # 几乎归零
    return

if require_official_source and not has_official_source:
    confidence = min(confidence, 40)
    gating_applied.append("no_official_source_capped_40")

if unresolved_conflict:
    verdict = "conflicting"
    confidence = min(confidence, 35)
    gating_applied.append("conflict_capped_35")

if policy_status == "expired":
    verdict = "outdated"
    confidence = min(confidence, 25)
    gating_applied.append("policy_expired_capped_25")

if policy_status == "superseded":
    verdict = "outdated"
    confidence = min(confidence, 30)
    gating_applied.append("policy_superseded_capped_30")

if mode == "policy" and not has_official_source:
    # 政策类没有官方源，无论其它维度多高，最高 supported 不可达
    confidence = min(confidence, 35)
    if verdict == "supported":
        verdict = "partially_supported"
    gating_applied.append("policy_no_official_capped_35")

if contradict_count >= max(support_count, 1) and any evidence.authority_weight >= 0.9 is contradicting:
    verdict = "contradicted"
    confidence = max(20, 50 - contradict_count * 5)
    gating_applied.append("authority_contradicted")
```

## Verdict 决策树

按以下顺序判定（gating 已应用后），第一个 match 即返回：

```
1. claim_clarity == 0 (主观/价值判断) → out_of_scope
2. evidence 数量 == 0 → unverifiable
3. unresolved_conflict → conflicting
4. policy_status in {expired, superseded} → outdated
5. authority 反驳 ≥ 支持 → contradicted
6. raw_confidence >= 75 and has_official_source → supported
7. raw_confidence >= 60 → partially_supported
8. raw_confidence >= 40 → partially_supported
9. raw_confidence < 40 → unverifiable
```

## confidence_level

```
high:   confidence >= 75
medium: 40 <= confidence < 75
low:    confidence < 40
```

## 校验

`scripts/score.py` 必须返回 deterministic 结果：相同输入永远返回相同输出。这是 Skill 阶段唯一可信赖的"客观环节"，不能用 LLM 做评分。
