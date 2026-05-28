# 盲测协议（防记忆污染）

## 为什么需要

上一轮实测的"raw LLM 判断"在我看过搜索结果后才写下来，存在以下污染风险：
1. 看完搜索结果，潜意识反向校准 raw 判断（"我本来就这么觉得"）
2. raw 判断的 confidence 被搜索后的确定性影响
3. 无法真实测量"如果不联网，单凭训练知识能做到几成准确"

## 协议（严格执行顺序）

对每个 test case：

### 第 1 步 — 锁定原始判断（在任何搜索前）

把 case 内容读一遍后，**立刻**写到 `cycle_X_raw_judgments.jsonl`，每行：

```json
{
  "case_id": "M01",
  "claim": "...",
  "raw_judgment_committed_at": "ISO timestamp",
  "raw_verdict": "supported | partially_supported | contradicted | outdated | unverifiable | conflicting | out_of_scope",
  "raw_confidence": 0-100,
  "raw_reasoning": "我训练知识告诉我...",
  "raw_uncertainty_signals": ["不确定 X", "可能记错 Y", "完全不知道 Z"]
}
```

**关键纪律**：
- `raw_uncertainty_signals` 必须诚实写出来。如果心里有"我记不太清楚 X"的念头，就写下来
- `raw_confidence` 要保守：知道就高分，模糊就低分，不知道就直接写 30 以下
- **不准在写 raw_reasoning 时打开任何搜索工具的口子**——脑子里出现"等一下我搜一下"立刻刹车

### 第 2 步 — 跑 Skill pipeline

完成第 1 步并 commit 到文件后才能开始：
- WebSearch
- WebFetch
- 用 source_classify 打权威分
- 用 score.py 评分
- 记录 skill_verdict / skill_confidence / skill_reasoning

写到 `cycle_X_skill_results.jsonl`。

### 第 3 步 — 收集 ground truth

通过权威源（gov.cn / stats.gov.cn / 部委站）确认真实情况，写 `ground_truth`。如果搜索都查不到，ground truth 也是 `unverifiable`。

### 第 4 步 — 四象限分类

| 类别 | 含义 |
|---|---|
| A_right_B_right | Skill 对，raw 对 |
| A_right_B_wrong | Skill 对，raw 错 ← Skill 的核心价值 |
| A_wrong_B_right | Skill 错，raw 对 ← Skill 的回归（需要改 prompt） |
| A_wrong_B_wrong | 都错 ← 信源覆盖不足或测试集 ambiguous |

## 防止 Skill 也被记忆污染

Skill 执行时如果搜索结果和记忆冲突，必须按以下硬规则：

1. **抽取阶段**：only 用 fetched 页面正文里实际存在的字段，禁止把训练记忆里的字段补进 claims_about
2. **比对阶段**：support_level 只看证据 snippet 是否实际包含或反驳 claim 的字段，禁止"我觉得这个对"
3. **生成 verdict**：当 evidence 数量 = 0 时，verdict 必须 unverifiable，不许根据训练知识降级为 supported/contradicted
4. **answer 生成**：只引用 evidence 中提供的 snippet 和 source_url，不许补充训练知识

## 测试集筛选标准

为了让 raw judgment 真正裸，case 应该满足：
- 至少 60% 是我训练数据可能不覆盖、覆盖不准、或包含过时信息的
- 必须包含 adversarial：plausible-but-wrong + implausible-but-right
- 必须包含 post-cutoff（2025-2026 后期）事件
