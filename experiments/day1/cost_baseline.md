# Day 1.3 LLM 成本 Baseline — Case A (P019)

## Test setup
- **Claim**: "新标准要求电动自行车安装金属鞍座"（ground_truth: contradicted）
- **Mode**: entity_status
- **LLM**: DeepSeek (deepseek-chat) — ¥0.5/1M input, ¥8.0/1M output
- **Search providers (按 .env 配置)**: primary=anysearch, secondary=metaso, fallback=bocha
- **max_sources**: 8

## Result

| Metric | Value |
|---|---|
| **verdict** | partially_supported |
| **confidence** | 40 (medium) |
| **total latency** | **31.81 s** |
| **prompt tokens** | 4,736 |
| **completion tokens** | 693 |
| **total tokens** | **5,429** |
| **search_calls** | 8 |
| **fetch_calls** | 8 |
| **evidence used** | **1 piece** (from ce.cn / 中经网) |

## Cost breakdown (CNY)

| Component | Cost | Share |
|---|---|---|
| LLM (5,429 tokens) | ¥0.007912 | 2.4% |
| Search (8 calls × ¥0.04 avg) | ¥0.320000 | 97.6% |
| **TOTAL per claim** | **¥0.328** | 100% |
| **Per 1,000 claims** | **¥328** | — |
| **Per 100k claims/month** | **¥32,800** | — |

## Key Findings

### Finding 1: **搜索成本是 LLM 成本的 40 倍**
- 单条核查 LLM 成本 ¥0.008，搜索 ¥0.32
- DeepSeek 真的很便宜（5k tokens ≈ 1 分钱）
- 搜索 API 按 call 计费，与 token 无关
- **优化方向**：
  - 减少 query 数（当前 query_planner 出 3 query × 多 provider = 大量 call）
  - 命中缓存命中率拉高（当前测试关了缓存）
  - 商业搜索 API 谈量价（>10万次/月 应该能砍价）

### Finding 2: **Fetch 成功率 1/8 = 12.5%**，严重影响证据质量
- 8 个 URL 抓取，只有 1 个成功，最终只有 1 条 evidence 进入评分
- 原因可能：
  - 部分页面需要 JS 渲染（trafilatura 抓不到）
  - 部分被反爬（403/412）
  - 部分超时
- **优化方向**：
  - 增加抓取 fallback（playwright 渲染 JS 页面）
  - 用 trafilatura 失败时退到 readability + httpx
  - 已抓到 cache 的页面优先复用

### Finding 3: **evidence 不足导致 verdict=partially_supported**
- 只有 1 条 evidence（来自中经网 ce.cn / 中国经济网，央媒级别）
- 实际 piyao 已明确判 contradicted，但本系统因证据不足只能给 partially_supported
- **优化方向**：
  - 当 fetch 失败率 > 50% 时，应自动扩 query 重新搜索
  - 或调用第二批 URL（当前只取前 8）
  - 直接接 piyao site:piyao.org.cn（已在 prompt.py 加入，但需触发条件）

### Finding 4: **延迟 31.81s 偏高**
- 搜索 + fetch 占大头（约 18s），LLM 多 step 串行约 12s
- 单条核查 30+秒，对实时交互体验差
- **优化方向**：
  - search + fetch 并发（已实现）
  - LLM steps 之间无依赖的可并发（subjective + query_planner）
  - 已经有缓存（关测试时禁用）

## Pipeline per-step 估算（基于 token 分布）

| Step | LLM tokens | Latency | 备注 |
|---|---|---|---|
| 1. SubjectiveDetector | ~200 | ~1s | 短路检查，否决 subjective claim |
| 2. QueryPlanner | ~600 | ~3s | 生成 3 query + entities |
| 3. SearchOrchestrator | 0 | ~9s | 8 query × 2 provider 并发 |
| 4. FetchOrchestrator | 0 | ~9s | 8 URL 并发抓取，1 成功 |
| 5. FactExtractor | ~3000 | ~10s | 单页提取 evidence |
| 6. CrossValidator | ~1000 | ~5s | 交叉验证（少证据少 token） |
| 7. ScoreEngine | 0 | <0.1s | 纯规则 |
| 7b. AnswerGenerator | ~600 | ~3s | 80-200 字答案 |
| TOTAL | 5,429 | 31.8s | |

## 与"裸 LLM"对比

| 方案 | 单条成本 | latency | 准确率（估） |
|---|---|---|---|
| 裸 DeepSeek 直接问 | ¥0.001 | ~3s | ~50% on contradicted cases |
| **本系统** | **¥0.328** | 31.8s | ~70%（55-case eval） |
| 性价比 | 增 328× | 增 10× | 增 20% |

**结论**：系统当前性价比有改进空间——
- 搜索成本太重，需要在 query 数 / call 数上做减法
- fetch 成功率太低，需要工程优化
- LLM 几乎是免费的，可以多调用换准确率（但要避免搜索）

## 下一步（Day 2+）
1. **2.1 近似检测**：评估是否能去重多家媒体同源转载，减少 fetch 数量
2. **fetch 改造**：提升 fetch 成功率到 ≥ 60%（playwright + readability fallback）
3. **query plan 优化**：从 3 query 减到 1-2 query
