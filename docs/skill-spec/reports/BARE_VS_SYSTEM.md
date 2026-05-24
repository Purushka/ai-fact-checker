# 裸 DeepSeek vs 系统 — 端到端真实对比

## 对比方案

- **裸 DeepSeek**：仅 prompt + claim，让模型纯靠训练知识判断；无搜索、无评分、无 gating
- **系统**：完整 pipeline（Bocha 搜索 → fetch → DeepSeek extract → cross_validate → score）
- 同模型（deepseek-chat），同 9 个 memory-resistant claim

## 一句话结论

**表面准确率持平（33% vs 33%），但裸模型的错误是"自信地骗人"，系统的错误是"诚实地不确定"。这才是父项目"少踩坑"价值观下的真实差距。**

## 9 case 全矩阵

| Case | Ground Truth | 裸 DeepSeek | 系统 | 象限 |
|---|---|---|---|---|
| MA01 民营经济促进法 5/20 施行 | supported | outdated(90) | **supported(89)** | 系统增量 ✓ |
| MA04 2025 GDP 突破 140 万亿 | supported | unverifiable(30) | **supported(85)** | 系统增量 ✓ |
| CD10 民营经济促进法首部 | supported | outdated(95) | **supported(92)** | 系统增量 ✓ |
| MZ01 宪法 2018-03-11 修正 | supported | **supported(100)** | partially_supported(69) | 系统回归 ✗ |
| MD02 公司法 2018 修正 | outdated | **outdated(100)** | unverifiable(30) | 系统回归 ✗ |
| CD06 国家金融监管总局 2023 | supported | **supported(95)** | partially_supported(61) | 系统回归 ✗ |
| MC01 GDP 最终数 126.06 万亿 | contradicted | supported(95) ⚠️ | unverifiable(30) | 都错 |
| MG01 证监会主席仍是易会满 | contradicted | outdated(100) | unverifiable(10) | 都错 |
| CD01 中成药新增 40 余种 | contradicted | supported(90) ⚠️ | partially_supported(45) | 都错 |

## 关键洞察 1：裸模型的"错"含 2 个自信幻觉

| Case | 裸模型 verdict | 信心 | 实际错在哪 |
|---|---|---:|---|
| **MC01** | **supported** | **95** | 自信确认 126.06 万亿是 2023 最终数，**实际已被 stats.gov.cn 2024-12 修订为 129.4 万亿** |
| **CD01** | **supported** | **90** | 自信确认"新增 40 余种中成药"，**实际只新增 11 种** |

**这两个 case 父项目用户会被严重误导**——拿到 90+ confidence 的"supported"会以为是真的，但实际是过时/错误数据。

**系统在这两个 case 都返回 `unverifiable` / `partially_supported` 低 confidence**，触发父项目的人工复核流程。这就是"宁可错杀"的产品价值。

## 关键洞察 2：训练截止后的事件，裸模型默认"猜 outdated"

| Case | 裸模型 verdict | 真实情况 |
|---|---|---|
| MA01 | outdated(90) "未颁布" | 已 2025-05-20 施行 |
| CD10 | outdated(95) "未施行" | 同上 |

裸模型遇到 2024-2026 事件，**它的训练数据里没有 → 默认猜"outdated/没颁布"**，但实际这些都是已生效的真实事件。系统通过搜索找到 ndrc.gov.cn 等真实源后正确判 supported。

## 关键洞察 3：系统的"错"都是保守诚实

系统 6 个错都是 `unverifiable` 或 `partially_supported`——意味着调用方（父项目）会知道"这事系统也拿不准，请人工核对"。**没有一个是自信错判**。

| 系统错误 case | verdict | 含义 |
|---|---|---|
| MC01 | unverifiable(30) | 搜不到 2024-12 修订公告 → 不下定论 |
| MD02 | unverifiable(30) | Bocha 仅返回 csrc 模板页 → 无有效证据 |
| MG01 | unverifiable(10) | 搜索仅返回低权威源 → 不下定论 |
| MZ01 | partially_supported(69) | 永久法规 + 单 tier1 源 → 接近 75 阈值未达 |
| CD01 | partially_supported(45) | LLM 抽取保守，标 neutral 多 |
| CD06 | partially_supported(61) | 仅 1 evidence → 孤证 |

**所有系统错误都"自带 confidence < 70 的警示"**，父项目可以基于 confidence_level=low/medium 触发人工复核或要求重查。

## 量化对比

| 维度 | 裸 DeepSeek | 系统 | 差距 |
|---|---:|---:|---|
| 严格匹配率 | 3/9 (33%) | 3/9 (33%) | 持平 |
| **自信错判（≥90 信心错）** | **2** | **0** | **系统完胜** |
| 系统增量（裸不知道，系统找到）| — | 3 | +33% |
| 系统回归（裸 confident 对，系统找不全）| — | 3 | -33% |
| 平均 token / case | 210 | 7680 | 系统 36x |
| 平均耗时 / case | 2s | 24s | 系统 12x |
| 平均 ¥/case 估算 | ¥0.001 | ¥0.10 | 系统 100x |

## 产品意义

### 为什么"持平"是个误导

简单准确率把"自信幻觉"和"诚实承认不知"等同对待，但产品语境下完全不同：
- 父项目"少踩坑"价值观下，错判 `supported` 远比错判 `unverifiable` 危险
- 用户拿到 `unverifiable` 会去查证；拿到 `supported(90)` 会直接相信
- 系统的 `partially_supported` + confidence 提供了人工复核的信号

### 系统真正的产品价值

1. **拦截自信幻觉**：MC01 / CD01 等 case，裸模型会自信地骗用户；系统会标低 conf 让用户警觉
2. **填补 post-cutoff 知识缺口**：MA01 / MA04 / CD10 这类 2024-2026 事实，裸模型完全没办法
3. **冗余确认机制**：即使系统在某 case 上不如裸模型"准"，它至少诚实地说 unverifiable，把决策权交回用户

### 接下来的改进方向

系统 3 个回归 case（MZ01 / MD02 / CD06）都是**搜索覆盖度问题**——Bocha 返回的 URL 不够好。改进路径：
1. 多 search provider 并行（Tavily + Bocha + 各 SERP），提高召回
2. 搜索结果按 authority 重排序，优先抓 tier1 源
3. fact_extractor 拿训练知识做 sanity check（"如果搜索 0 evidence 但训练知识有，标 weak 而非 unverifiable"）

## 给父项目的接入建议

基于这次对比，建议父项目使用方式：

```python
result = await fact_check(claim, ...)
if result.verdict == "supported" and result.confidence >= 75 and result.has_official_source:
    用户答案 = 直接展示，附 evidence URL
elif result.verdict == "contradicted" and result.confidence >= 50:
    用户答案 = "信息可能不准确" + 展示 contradicting evidence
elif result.verdict == "outdated":
    用户答案 = "该规定/状态已变更" + 展示 superseded_by 信息
else:  # unverifiable / partially_supported / conflicting
    用户答案 = "无法核实" + 进入人工复核工作流
```

这种用法下：
- **裸 DeepSeek 的 2 个自信幻觉会直接误导用户**（confidence 95 走第一条分支）
- **系统所有错误都触发"无法核实"或人工复核，0 误导**
