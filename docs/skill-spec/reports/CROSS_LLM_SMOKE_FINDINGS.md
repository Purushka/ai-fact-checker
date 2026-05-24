# 跨 LLM 一致性 — 初步发现（MZ01 smoke test）

## 起因
父项目按 token 报销 + 不绑定单一模型生态。需要量化：同一 prompt 在不同国内 LLM 上 verdict 是否一致？

## Smoke test：MZ01 「宪法最近一次修正是 2018-03-11」

| 模型 | Verdict | Conf | Evidence | Tokens | 关键观察 |
|---|---|---:|---:|---:|---|
| DeepSeek Chat | **supported** ✓ | 82 | 3 | 7298 | 2 个 0.9+ 源，正确识别永久性法规 |
| Qwen 2.5 72B | unverifiable ✗ | 30 | 1 | 3102 | 抽取保守，只得 1 evidence；可能 source extract 失败 |
| Kimi K2 | outdated ✗ | 30 | 3 | 7208 | **幻觉**：cross_validator 填了不存在的"2023-03-11 宪法修正案"作为 superseded_by |
| Qwen3 235B | **supported** ✓ | 84 | 3 | 10087 | 3 个高权威源，judgment 最稳 |

## 三种失败模式

### 1. 模型保守失败（Qwen 2.5 72B）
LLM 在 evidence extract 阶段把多数页面标 neutral 或返回少量字段，导致下游 score 算出来低。
- 表现：unverifiable / partially_supported 偏多
- 影响：对父项目"宁可错杀"价值观一致，但生产质量低

### 2. 模型幻觉失败（Kimi K2）
关键发现：**Kimi K2 在 cross_validator 阶段直接编造了不存在的事实**——把"2023年3月11日宪法修正案（不存在）"填入 `superseded_by`，触发 outdated verdict。
- 表现：自信地给出错误答案（confidence 不会反映幻觉）
- 影响：**这是生产严重风险**。父项目场景下用户可能完全相信
- 启示：`SKILL.md` / `prompts.py` 的"反幻觉硬约束"针对的就是这种情况；Kimi 没有 honor 该规则

### 3. 模型差异性（DeepSeek vs Qwen3）
DeepSeek Chat 和 Qwen3 235B 都正确判 supported，但 conf 不同（82 vs 84）。这是健康的差异。

## 产品意义

1. **不能盲目切换 LLM**：Kimi K2 的幻觉行为意味着不能仅靠 prompt 让所有模型表现一致
2. **LLM 选型即风险管理**：DeepSeek Chat / Qwen3 是当前测试集下最 safe 的选择
3. **多模型 voting 可能必要**：对高风险 case 跑 2 个模型对比，verdict 不一致 → 标记 conflicting / unverifiable

## 6 case × 4 模型 完整矩阵（运行中）

后台跑全部 24 组合，结果将更新到 `reports/cross_llm_all.json` 和本文件。
