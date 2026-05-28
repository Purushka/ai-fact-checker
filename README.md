# AI Fact Checker

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-150%20passing-brightgreen)](api/tests)

中文事实核查 API 服务 — 多 LLM、多搜索源、源权威评分、传播链推演，**专为 Chinese-language claim verification 设计**。

## 解决什么问题

| 给谁 | 解决什么 |
|---|---|
| **AI 应用开发者** | 给 RAG / Chatbot / 写作助手做事实校验层，挡住幻觉输出 |
| **内容审核 / 自媒体平台** | 民生类谣言识别 + 传播链路追溯 |
| **公关 / 品牌团队** | 危机响应时快速摸清"原始爆料源 → 转发 → 辟谣"全链路 |
| **研究 / 教育** | 评估 LLM 在中文事实核查任务上的性能基线 |

## 核心能力

- **7 步 Pipeline**：QueryPlanner → Search → Fetch → Extract → CrossValidate → Score → Generate
- **多 LLM Provider**：DeepSeek 主、OpenRouter fallback；可扩展到 Qwen / GLM / Moonshot
- **多搜索源**：Bocha + Metaso + AnySearch + Bilibili API，并发调用
- **5 维评分 + Bootstrap CI**：source_authority / consistency / freshness / completeness / claim_clarity，输出 `[lo, hi]` 置信区间
- **PropagationTimeline**：URL 域名分类 + 时间排序，识别 5 种典型传播模式（grassroots_viral / official_dissemination / fact_check_corrected / coordinated / narrative_distortion）
- **InferenceChain**：bge-large-zh embedding 推演，定位每个 evidence 的最可能"信息来源"
- **piyao.org.cn 集成**：中国互联网联合辟谣平台直查
- **传播性谣言识别**：query planner 自动加 `site:piyao.org.cn` 等扩展查询

## 快速开始

### Docker（推荐）

```bash
cd api
cp .env.example .env
# 填入至少 DEEPSEEK_API_KEY、BOCHA_API_KEY
docker compose up -d
curl http://localhost:8000/health
```

### 本地开发

```bash
cd api
pip install -e ".[dev]"
pytest                                # 跑 150 个测试
uvicorn factcheck.main:app --reload   # 启服务
```

### 调用示例

```bash
curl -X POST http://localhost:8000/v1/check \
  -H "Content-Type: application/json" \
  -d '{
    "claim": "2025年深圳市对登记失业人员实施一次性创业补贴每人1万元",
    "mode": "policy",
    "options": {"return_answer": true}
  }'
```

返回结构：
```json
{
  "verdict": "supported|contradicted|insufficient_evidence|conflicting|outdated|out_of_scope",
  "confidence": 78,
  "confidence_interval": {"lo": 65, "hi": 88, "method": "bootstrap_5dim_n200"},
  "evidence": [...],
  "score_breakdown": {...},
  "propagation_timeline": {
    "pattern": "official_dissemination",
    "stops": [...]
  },
  "inference_chain": {
    "nodes": [...],
    "pattern": "fact_check_corrected"
  },
  "token_usage": {...}
}
```

## 项目结构

```
.
├── api/                       # FastAPI 主服务
│   ├── src/factcheck/
│   │   ├── pipeline.py        # 主编排器
│   │   ├── extract/           # QueryPlanner / FactExtractor / TimelineBuilder / InferenceChainBuilder
│   │   ├── search/            # Bocha / Metaso / AnySearch / Bilibili
│   │   ├── score/             # 5-dim ScoreEngine + bootstrap CI
│   │   ├── fetch/             # trafilatura 内容抓取
│   │   ├── llm/               # LLM Provider 适配
│   │   └── schemas.py         # Pydantic v2 契约
│   ├── tests/                 # 150 个单元 + 集成测试
│   └── scripts/               # 演示脚本
├── docs/
│   ├── skill-spec/            # 评分逻辑、权威源库、内容农场黑名单 — 核心配置
│   └── CONTRIBUTING.md
├── eval/                      # benchmark 数据集 + 评估脚本
│   ├── data/                  # 标注 case（piyao + manual）
│   ├── scraper/               # piyao 爬虫
│   └── reports/               # 评估结果
└── experiments/               # 实验 + 分析（按 day 组织）
    ├── day1/                  # 案例选择 / 数据源验证 / 成本基线
    ├── day2/                  # 近似检测对比 / 传播链验证 / 评分验证
    └── v6_test_set/           # benchmark 数据集设计与构造
```

## 测试与基准

```bash
cd api && pytest -v
# 150 passed in 8.52s
```

| Benchmark | 数据集 | 准确率 |
|---|---|---|
| 55-case live eval | piyao 民生谣言 | 49.1% |
| 200-case calibration | 跨域综合 | 见 `eval/reports/` |
| 1000+ case 历史回归 | manual + auto-generated | 见 `eval/reports/` |

## 架构

```
Request
   │
   ▼
SubjectiveDetector  ──→ out_of_scope 短路
   │
   ▼
QueryPlanner (LLM)
   │
   ▼
SearchOrchestrator (并发 Bocha + Metaso + AnySearch)
   │
   ▼
FetchOrchestrator (trafilatura)
   │
   ▼
FactExtractor (LLM)  ──→ List[Evidence]
   │
   ▼
CrossValidator (LLM)
   │
   ▼
ScoreEngine (5-dim + bootstrap CI)
   │
   ├──→ TimelineBuilder
   ├──→ InferenceChainBuilder (bge-large-zh)
   └──→ CheckResponse
```

## 配置文件

`api/.env` 关键变量：

```bash
LLM_DEFAULT_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-v1-...  # fallback

SEARCH_PRIMARY=anysearch
SEARCH_SECONDARY=metaso
SEARCH_FALLBACK=bocha
BOCHA_API_KEY=...
METASO_API_KEY=...
ANYSEARCH_API_KEY=...

REDIS_URL=redis://localhost:6379/0  # 缓存
```

## 单条调用成本

| Component | Cost (CNY) | Share |
|---|---|---|
| LLM (DeepSeek deepseek-chat, ~5k tokens) | ¥0.008 | 2.4% |
| Search (8 calls 平均) | ¥0.320 | 97.6% |
| **总计** | **¥0.33** | 100% |

成本主要在搜索 API。优化方向：query 数减半、缓存命中、量价谈判（详见 `experiments/day1/cost_baseline.md`）。

## License

[MIT](LICENSE)

## Contributing

见 [CONTRIBUTING.md](docs/CONTRIBUTING.md)。
