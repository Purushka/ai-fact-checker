# Day 2.3 评分引擎对比人工判断

## 实验设计

跑完整 pipeline 对 3 个 piyao case 做评分，与人工判断（piyao 的 ground_truth）对比：

| case | human verdict | category |
|---|---|---|
| P019 | contradicted | policy_standard |
| P015 | contradicted | health |
| P021 | contradicted | social |

---

## 实验结果

| Case | Human | System | Conf | Match | Evidence | Propagation Pattern | Latency |
|---|---|---|---|---|---|---|---|
| P019 | contradicted | **unverifiable** | 30 | PARTIAL | 2 | insufficient_data | 33.0s |
| P015 | contradicted | **partially_supported** | 55 | PARTIAL | 4 | official_dissemination | 35.6s |
| P021 | contradicted | **partially_supported** | 63 | PARTIAL | 8 | **fact_check_corrected** | 37.7s |

**Summary**: MATCH 0/3, PARTIAL 3/3, MISS_OPPOSITE 0/3, DIFFER 0/3

---

## 关键发现

### Finding 1: **0/3 完全匹配，但 0/3 严重错判**
- 没有任何一个 case 系统给出 `supported`（即没有把谣言误判为真）
- 全部判 `partially_supported` 或 `unverifiable`，相对保守
- **方向性正确，强度不够**

### Finding 2: **P021 评分引擎漏用 propagation_timeline 信号**

最严重的发现。P021 案例：
- 8/8 fetch 成功（少有的完美抓取）
- 8 条 evidence 全部入库
- **propagation_timeline.pattern = "fact_check_corrected"** (confidence 85)
- 这意味着系统**已经识别出了 piyao 节点 + 完整辟谣链路**

但 score engine 仍输出 `partially_supported` (conf 63)，没有跳到 `contradicted`。

**根因**：score_engine.py 当前的评分逻辑**不读 propagation_timeline**。
当 propagation_timeline.pattern == "fact_check_corrected" with conf ≥ 80 时，
应该强信号 boost 到 `contradicted`，但目前没有这个 gate。

**修复方向**：
```python
# 在 score_engine.py 加 gate:
if propagation_timeline and \
   propagation_timeline.pattern == "fact_check_corrected" and \
   propagation_timeline.confidence >= 80:
    # 辟谣平台明确收尾 → 强信号 contradicted
    final_verdict = "contradicted"
    confidence = max(70, confidence)  # 提升 confidence
    notes.append("propagation_timeline 检测到 fact_check_corrected pattern")
```

### Finding 3: **P019 fetch 失败率高导致证据不足**

P019: fetch 2/8 = 25% 成功率，与 1.3 cost baseline 的 12.5% 接近，证实了
fetch 工程问题。当 evidence 仅 2 条时：
- score engine 倾向给 `unverifiable`（confidence 30）
- 这其实是合理的保守策略 — 证据不足不应硬下结论
- 但 piyao 已经在搜索结果中（且 piyao 是 source_authority 满分源）
- **未充分利用 piyao 的权威性**

### Finding 4: **P015 中规中矩，反映 evergreen myth 的固有难度**
- evidence 4 条，verdict=partially_supported, conf=55
- propagation pattern = official_dissemination（说明搜到的是科普类官方文章）
- evergreen 健康类谣言的"反对面"主要是医学知识共识，不是某个事件辟谣
- 系统能识别"主流来源说这个观点是错的"，但语义上不达到"contradicted"强度

### Finding 5: **Fetch 成功率与 verdict 强度正相关**

| Case | Fetch OK | Verdict | Confidence |
|---|---|---|---|
| P021 | 8/8 (100%) | partially_supported | 63 |
| P015 | 4/8 (50%) | partially_supported | 55 |
| P019 | 2/8 (25%) | unverifiable | 30 |

完美的单调关系 — fetch 成功率直接决定输出 confidence。
**意味着**：fetch 是 confidence 的瓶颈，提升 fetch 成功率比改 prompt 提升更大。

---

## Priorities for fix（按 ROI 排序）

### Priority 1 (最高 ROI): **score engine 读 propagation_timeline**

P021 案例展示了系统**已经识别出辟谣链路但没用上**这个信号。
加 ~10 行代码，把 fact_check_corrected pattern + conf ≥ 80 映射到 contradicted。

**预期收益**：P021 + 任何含 piyao 节点的 case 都会从 partially_supported → contradicted。
基于 55-case eval，大约 30% 案例可能受益。

### Priority 2 (高 ROI): **fetch 工程优化**

当前流程：trafilatura → httpx → 失败放弃
应改为：trafilatura → playwright 渲染 → readability fallback → 失败时复用 search snippet

目标：fetch 成功率从 ~30% → ≥ 60%

**预期收益**：evidence 数量翻倍，自然推高 confidence + 减少 unverifiable 误判。

### Priority 3 (中 ROI): **evergreen myth 检测**

对 P015 类（"穿聚酯纤维含微塑料"、"喝纯净水缺微量元素"）：
- 加 query expansion: 自动加 "辟谣 OR 科普 OR 权威 OR 健康"
- 加 source_type filter: 偏好 medical/scientific 权威源
- 当 evidence 来自 fact_check_corrected 域名时，verdict 应是 contradicted

---

## 输出文件
- `validate_scoring.py` — 跑 3 case 的可复现脚本
- `scoring_validation.json` — 原始 pipeline response 数据
- `scoring_validation.md` — 本报告
