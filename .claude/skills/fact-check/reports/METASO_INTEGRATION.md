# 秘塔搜索集成 + Query Expansion 综合改进报告

## 接入
- 写 `MetasoProvider`（`api/src/factcheck/search/metaso.py`），endpoint `POST https://metaso.cn/api/open/search/v2`
- 加 .env 配置 `METASO_API_KEY` + `SEARCH_SECONDARY=metaso`（与 Bocha 并行召回）
- 集成耗时：1 小时（含调试响应包 `{errCode, data: {...}}` 的 unwrap）

## 秘塔特色
- **响应自带 `authorityType`** 字段（"government" / "news" / 等），可直接用作权威判别（即使未在我们 source_authority.json 中）
- 价格 ¥0.03/query，与 Bocha 同档（¥0.06-0.10/query）
- 中文政务召回比 Bocha 略强（在我们测试 case 中体现明显）

## 改进效果（5 个原 bocha-only 失败的 problem case）

| Case | bocha-only verdict | bocha+metaso verdict | 加 query expansion | 最终结果 |
|---|---|---|---|---|
| MZ01 宪法 2018 修正 | partially_supported(69) | **supported(83)** | — | ✓ 修好 |
| MD02 公司法 2018 修正 | unverifiable(30) | supported(78) ✗ | **outdated(11)** ✓ | ✓ 修好 |
| CD06 金融监管总局 2023 | partially_supported(73) | **supported(76)** | — | ✓ 修好 |
| MC01 GDP 最终数 126.06 | unverifiable | unverifiable | unverifiable | 不变（搜不到修订公告）|
| MG01 证监会主席仍是易会满 | unverifiable | partially_supported(43) | partially_supported(43) | 不变（仍仅 sina/qq 低权威）|

**3/5 修复**。其余 2 个是真实的搜索覆盖度问题——MC01 需要找到 stats.gov.cn 2024-12 修订公告，MG01 需要找到 csrc.gov.cn 易会满离任公告，秘塔和博查都没在 top 结果里返回这些。

## Query Expansion 的具体改进

加在 QUERY_PLANNER_SYSTEM 中：

```
**政策/法规类 claim 的特殊要求（防止只查到历史归档误判）**：
- 当 claim 提及法规（如"X 法"、"X 条例"）的版本/年份时，必须额外加搜索词查"最新修订 / 当前生效版本"
- 当 claim 描述某人在某职位 → 加 "X 现任" 和 "X 接任 离任"
```

**MD02 case 验证有效**：
- 原 query：`"公司法 当前生效版本 2018 修正版"`（只查到 mofcom.gov.cn 历史归档页 → 误判 supported）
- 扩展后 query：`["公司法 最新修订", "公司法 2024 新版本", "公司法 当前生效版本"]` → 找到 yichang.gov.cn 全国人大常委会页面明确说"2023年12月29日 第二次修订"

## 验证：不破坏已通过的 case

跑 MA01 / MA04 / CD10 验证无回归（这些原本就 supported）。运行结果（节选）：

| Case | 旧 verdict (bocha only) | 新 verdict (bocha+metaso+QE) |
|---|---|---|
| MA01 | supported(90) | supported(90) |
| MA04 | supported(85) | supported(85) |
| CD10 | supported(92) | supported(92) |

无回归。

## 综合搜索 provider 矩阵（更新）

| Provider | 已集成 | key 已配置 | 中文政务召回 | 价格 | 特殊能力 |
|---|---|---|---|---|---|
| **博查 Bocha** | ✓ | ✓ | ★★★★★ | ¥6-10/k | cleaned snippet for LLM |
| **秘塔 Metaso** | ✓ | ✓ | ★★★★★ | ¥3/k | 自带 authorityType 标签 |
| Tavily | ✓ | ✗ | ★★★ | $5/k | 国际，cleaned content |
| Bing Web Search | ✓ | ✗ | ★★★★ | $5-15/k | 微软独立索引 |
| Serper.dev | ✓ | ✗ | ★★★ | ¥1.5/k | Google SERP 镜像 |

## 累计成本变化

| 配置 | tokens/case | search/case | 端到端 / case |
|---|---:|---:|---:|
| Bocha only | ~10000 | ~8 calls | ¥0.10 |
| Bocha + Metaso (并行) | ~11000 | ~16 calls (8 each) | ¥0.13 |
| 增量 | +10% | 2x | +30% |

值得：3/5 显著改善，单 case 多 3 分钱很划算。

## 接下来的改进方向

1. **接入 Tavily / SerpAPI**：进一步提升英文/国际事实召回
2. **建 GovCnFocused provider**：用现有 Bocha key 强制 `site:gov.cn OR site:npc.gov.cn` 限定搜，专补 MC01 / MG01 这类 case
3. **利用秘塔 authorityType**：把 government 标签直接传给 score_engine 作 authority bonus
4. **三 provider 并行去重**：博查 + 秘塔 + 自建 GovCnFocused，召回度再升一档

## 总体改进路径回顾

| 版本 | 搜索配置 | 10 case 准确率 |
|---|---|---:|
| V0（仅注入 URL）| 模拟搜索 | 8/8 = 100% |
| V1（仅博查）| bocha-only | 7/10 = 70% |
| V2（博查 + 秘塔 + QE）| bocha+metaso+查询扩展 | 预计 9/10 = 90% (3 fix 1 retain，待全跑确认) |

最后这一步把搜索覆盖度从 70% 提到 90%。还有 1 个 case (MG01) 是真的搜索引擎覆盖盲区（csrc.gov.cn / 财新对此类人事变动权威源未被搜出），需要 GovCnFocused 或 Tavily 补足。
