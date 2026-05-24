# Cycle 1 失败模式分析

## 唯一边界 case: ME01（〔2024〕35号 不存在的文号）

### Skill 行为
- 搜索找不到"国发〔2024〕35号"
- 搜索结果里出现了真实的 AI 政策文件：**国发〔2025〕11号《国务院关于深入实施"人工智能+"行动的意见》**
- 当前 fact_extractor prompt 让 LLM 判断 support_level，但**没有明确指引**当搜索"没找到 claim 提到的具体文号"时如何处理。
  - 选项 A：support_level=contradicted（找到了真实文号，反证 claim 的文号 fabricated）
  - 选项 B：support_level=neutral（搜不到当前文号，evidence 不可直接支持也不直接反驳）
  - 当前 prompt 倾向选项 B，会导致 verdict=unverifiable，但更准确的是 contradicted

### 类似的潜在失败模式
1. **fabricated document number**（claim 假造的文号）→ 现有 prompt 不主动反证
2. **wrong specific identifier**（人名/日期/版本号错位）→ 同样
3. **plausible-but-wrong claim structure**（专业措辞 + 错误细节）→ 同样

## 其它 7 个 Skill 增量价值 case（A 对 B 错）

| ID | 类别 | Skill 抓到了什么 raw 漏掉 |
|---|---|---|
| MA01 | post-cutoff | 民营经济促进法 2025-05-20 施行（训练里有但日期不准）|
| MA02 | post-cutoff | 2025 NEV 1649 万辆（训练后数据）|
| MC01 | revised data | GDP 修订公告（stats.gov.cn 2024-12-27 修订 126.06→129.4）|
| MC03 | revised data | R&D 强度 2.65% 已修订为 2.58% |
| MD02 | superseded | 公司法 2023-12 新修订（训练里仍以 2018 为现行）|
| MD03 | superseded | 行政复议法 2023-09 新修订 |
| MH02 | doc_number | 国办发〔2022〕9号 ≠ claim 的 14号 |

## 同时被 raw 错过的失败类别

不存在（A 错 B 对 = 0）。

## 共同失败（A 错 B 错）

不存在。

## 调优需求

### 必须调（影响 verdict 正确性）
**Prompt 增强 1**：FACT_EXTRACTOR 加入规则：当 claim 包含具体识别码（文号、文件名、人名 + 职位、版本号、日期）时，必须做"identifier 直接比对"：
- 在权威源中找到了**真实存在但与 claim 不同**的 identifier → support_level=contradicted
- 在权威源中找不到 claim 的 identifier，且**没找到任何同位置 identifier** → support_level=neutral / claims_about 中标记 identifier_not_found
- 在权威源中找到了 claim 的 identifier 且匹配 → support_level=strong/weak

**Prompt 增强 2**：CROSS_VALIDATOR 加入规则：当多个权威源出现了"与 claim 同主题但 identifier 不同"的文件时，标记 conflict_level=high，verdict 强制 contradicted（不是 conflicting）。

### 建议调（影响 confidence 准确性）
**Prompt 增强 3**：FACT_EXTRACTOR 加入"数据修订识别"——如果页面中含"修订"、"修正"、"更新"、"以新口径调整"等关键词，且涉及 claim 中的数字，必须 explicit 抽取出"原数据 → 新数据"，并标记 support_level=contradicted（如果 claim 用的是旧数据）。

**Prompt 增强 4**：SKILL.md 明确"宁可错杀"的边界——只在"完全没有相关 evidence"时倾向 unverifiable，"找到了反证 evidence"时必须 contradicted。

### 不必调
score.py 的 gating rules 全部按预期工作（policy expired/superseded 都正确封顶）。
source_classify 准确度高（210+ 源全部命中或合理 fallback）。

## 预期 cycle 2 改进

调优后预期：
- ME01 → contradicted（之前 unverifiable_or_contradicted）
- 类似的 fabricated identifier case 都能 contradicted
- 总体 Skill 准确率从 94% 提升至 ≥97%
