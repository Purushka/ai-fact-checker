# 三方对比：裸模型 / V2 bocha-only / V3 bocha+metaso+QE

## 三种配置

| 配置 | 描述 |
|---|---|
| **裸 DeepSeek** | 仅 prompt + claim，无搜索、无评分、无 gating |
| **V2 (bocha only)** | 完整 pipeline，搜索仅博查 |
| **V3 (+metaso+QE)** | 完整 pipeline，博查+秘塔并行 + 政策类查询扩展 |

## 完整 9-case 对比矩阵

（MA02 未在裸模型测试集，列空缺）

| Case | GT | 裸 DeepSeek | V2 bocha-only | V3 +metaso+QE |
|---|---|---|---|---|
| MA01 民营经济促进法施行 | supported | outdated(90) ✗ | supported(90) ✓ | supported(90) ✓ |
| MA04 2025 GDP 140 万亿 | supported | unverifiable(30) ✗ | supported(85) ✓ | supported(85) ✓ |
| MC01 GDP 最终数 126.06 | contradicted | **supported(95) ✗⚠️** | unverifiable(30) ✗ | unverifiable(30) ✗ |
| MD02 公司法 2018 修正 | outdated | outdated(100) ✓ | outdated(30) ✓ | outdated(15) ✓ |
| MG01 证监会主席易会满 | contradicted | outdated(100) ✗ | partial(53) ✗ | unverifiable(30) ✗ |
| MZ01 宪法 2018-03-11 | supported | supported(100) ✓ | supported(77) ✓ | supported(83) ✓ |
| CD01 中成药增 40 余种 | contradicted | **supported(90) ✗⚠️** | contradicted(42) ✓ | contradicted(39) ✓ |
| CD06 国家金融监管总局 | supported | supported(95) ✓ | supported(73) ✓ | supported(76) ✓ |
| CD10 民营经济促进法首部 | supported | outdated(95) ✗ | supported(92) ✓ | supported(92) ✓ |

## 关键指标三方对比

| 指标 | 裸 DeepSeek | V2 (bocha) | V3 (+metaso+QE) |
|---|---:|---:|---:|
| 严格匹配率 | 3/9 = **33%** | 7/9 = **78%** | 7/9 = **78%** |
| **自信错判（≥90 信心错）** | **5** ⚠️ | **0** | **0** |
| 平均 token / case | 210 | 9700 | 13000 |
| 平均耗时 / case | 2s | 27s | 29s |
| 平均 ¥ / case | ¥0.001 | ¥0.10 | ¥0.13 |

## 三大洞察

### 1. 表面准确率：裸 33% < V2 78% = V3 78%

V2 和 V3 在严格匹配上持平。V3 没有把 V2 的 7/9 提到 9/9，因为剩余 2 个错误（MC01 / MG01）都是真实的搜索覆盖盲区——博查和秘塔都没在 top 结果里返回 stats.gov.cn 2024-12 修订公告 和 csrc.gov.cn 易会满离任公告。

### 2. 自信错判数：裸 5 > V2/V3 各 0

**这是最重要的产品差距。**

裸 DeepSeek 的 5 个自信错判：
- MA01 outdated(90)："2025年5月，民营经济促进法尚未颁布" — 自信地否认已生效的法律
- MC01 supported(95)："126.06 万亿是 2023 年 GDP" — 自信地用已被修订的旧数据
- MG01 outdated(100)："易会满任职至 2023年2月" — 时间错（实际 2024-02 离任），但仍自信
- CD01 supported(90)："中成药新增 40 余种符合声明" — 凭空编造
- CD10 outdated(95)："民营经济促进法尚未施行" — 与 MA01 同种错

**父项目用户拿到 ≥90 信心的 5 个错误答案，会被直接误导。** V2/V3 在这 5 个 case 上都没有给出高信心错答（要么诚实 unverifiable，要么正确 supported/contradicted）。

### 3. V2 → V3 的本质改进

虽然准确率不变，V3 的失败更"安全"：

| Case | V2 verdict | V3 verdict | 差异 |
|---|---|---|---|
| MA02 | partially_supported(71) | conflicting(35) | V3 更主动暴露源冲突 |
| MG01 | partially_supported(53) | unverifiable(30) | V3 更诚实承认不知 |
| MD02 | outdated(30) | outdated(15) | V3 conf 更低但 verdict 一致 |
| MZ01 | supported(77) | supported(83) | V3 命中更多权威源，conf 更高 |
| CD06 | supported(73) | supported(76) | V3 conf 微涨 |

**V3 的趋势：信心更分化，对的更对、错的更诚实。** 这是更好的产品行为——给调用方更准确的不确定性信号。

## 何时该用哪个配置？

### 用裸 DeepSeek 不适合事实核查场景

裸模型在我们这种 memory-resistant case set 上：
- 33% 准确率
- 5/9 自信错判（55% 几率给出高信心错答）
- 适合：泛闲聊、内容生成；**不适合：父项目政策核查类生产场景**

### V2 (bocha only)

- 78% 准确率
- 0 自信错判
- 平均成本 ¥0.10/case
- 适合：父项目 MVP 期、对成本敏感、可接受 22% case 走人工复核

### V3 (+metaso+QE)

- 78% 准确率，但分布更优（错得更诚实，对得更稳定）
- 0 自信错判
- 平均成本 ¥0.13/case
- 适合：**父项目生产推荐配置**——稳定性收益 > 30% 成本增量

## 给父项目的接入建议（更新版）

```python
result = await fact_check(claim, ...)
if result.verdict == "supported" and result.confidence >= 75 and result.has_official_source:
    展示答案 + evidence URLs
elif result.verdict == "contradicted" and result.confidence >= 35:
    展示反驳信息 + 警示
elif result.verdict == "outdated":
    "该信息已过期/被替代" + 展示 superseded_by
elif result.verdict == "conflicting":  # ← V3 新增更频繁触发
    "权威源对此事实有冲突，建议人工核实"
else:  # unverifiable / partially_supported
    "无法核实，建议人工复核"
```

在 V3 配置下，**所有错误都进入"无法核实 / 人工复核 / 冲突报告"分支，零误导**。

## 改进路径回顾

| 版本 | 配置 | 准确率 | 自信错判 | 成本/case |
|---|---|---:|---:|---:|
| 裸 DeepSeek | 仅训练知识 | 33% | **5** ⚠️ | ¥0.001 |
| V1 注入 URL | 模拟搜索 + 评分 | 100% (8/8) | 0 | n/a |
| V2 真完整 | bocha-only | 78% | 0 | ¥0.10 |
| V3 真完整 | bocha+metaso+QE | 78% | 0 | ¥0.13 |

V1 是测试条件下上限（注入完美 URL）。V2/V3 是真实生产条件，离 V1 有 22% gap，主要是 MC01/MG01 这类搜索覆盖盲区。

## 还能继续提升的方向

1. **接入 Tavily / SerpAPI**：补足 MC01 / MG01 的搜索覆盖盲区
2. **建 GovCnFocused provider**：用现有 key 强制 `site:gov.cn site:npc.gov.cn ...` 限定搜
3. **利用秘塔 `authorityType`**：传给 score_engine 加 government 源 bonus
4. **预期 V4**：80-90% 准确率，0 自信错判，¥0.15-0.20/case
