# AI Fact Checker API

OpenClaw 创业平台的事实核查基础设施。提供事实核查、政策核查、行业方案审计 API；对父项目按 token 报销，不做多租户，不做独立备案。

## 关键能力

- `POST /v1/check`：通用事实核查，接受 `claim` 或 `question`
- `POST /v1/policy-check`：政策专用，强制官方源、返回结构化政策字段（适用对象/金额/条件/截止日期）
- `POST /v1/solution-audit`：行业方案批量审计（适合 OpenClaw 行业方案知识库入库前审核）
- `POST /v1/check/async`：长任务异步
- `GET  /v1/source-profiles`：内置 210+ 中文权威源
- `GET  /v1/source-profiles/classify?url=...`：URL → 权威分

## 评分逻辑（透明、可解释）

5 维加权（默认对齐父项目原 brief）：

| 维度 | 默认权重 | 含义 |
|---|---:|---|
| source_authority | 0.40 | 来源权威性 |
| source_consistency | 0.30 | 多源一致性（内容指纹去重后） |
| freshness | 0.20 | 时效性，政策类按 publish_date / expire_date / superseded_by |
| completeness | 0.10 | 按 mode 模板的字段填充率 |
| claim_clarity | 0.00 | 默认 0；可由调用方启用 |

调用方可在请求中传入 `scoring_weights` 覆盖默认。权重和必须 = 1.0。

5 维加权后再过 **gating rules**：

- `require_official_source=true` 且无官方源 → confidence 封顶 40
- 未解决冲突 → confidence 封顶 35
- 政策 expired → 封顶 25
- 政策 superseded → 封顶 30
- 政策无官方源 → 封顶 35，verdict 降为 partially_supported

## 部署

### 本地快速跑

```bash
cd api
cp .env.example .env
# 填写至少一个 LLM key（推荐 DEEPSEEK_API_KEY）和一个 SEARCH key（推荐 TAVILY_API_KEY）

pip install -e ".[dev]"
uvicorn factcheck.main:app --reload
```

打开 http://localhost:8000/docs 查看 Swagger UI。

### Docker

```bash
cd api
cp .env.example .env  # 填好 keys
docker compose up -d
docker compose logs -f api
```

### 腾讯云生产部署

推荐：与父项目同 VPC，走内网调用。

| 资源 | 选型 | 备注 |
|---|---|---|
| 计算 | CVM 标准型 4C8G × 2 + Auto Scaling | api + worker 各一台起步 |
| 网络 | 与父项目 VPC Peering | 内网调用 0 公网流量费 |
| Redis | TencentDB for Redis 1G 单机版 | 缓存 + Celery broker |
| PostgreSQL | TencentDB for PostgreSQL 5.x | 4C8G 标准版 |
| LLM | 腾讯云 DeepSeek V3 serverless | 同云内网，无 egress |
| 监控 | 腾讯云监控 + CLS | CLS 满足日志 6 个月留存 |

## 最小可用配置

`.env` 最少需要填：

```
SHARED_SECRET=<长随机串>
DEEPSEEK_API_KEY=<your key>
TAVILY_API_KEY=<your key>
REDIS_URL=redis://localhost:6379/0
```

DB 可选（不填则不持久化历史记录，但 API 仍可工作）。

## 调用示例

```bash
curl -X POST http://localhost:8000/v1/policy-check \
  -H "Authorization: Bearer ${SHARED_SECRET}" \
  -H "Content-Type: application/json" \
  -d '{
    "claim": "2025年深圳市对登记失业人员实施一次性创业补贴每人1万元",
    "mode": "policy",
    "options": {"require_official_source": true, "max_sources": 5}
  }'
```

返回结构（节选）：

```json
{
  "request_id": "...",
  "verdict": "partially_supported",
  "confidence": 72,
  "confidence_level": "medium",
  "has_official_source": true,
  "policy_status": "active",
  "policy": {
    "title": "...",
    "issuing_agency": "深圳市人社局",
    "agency_level": "city",
    "benefit_amount": "1万元",
    "applicable_subjects": ["登记失业人员"]
  },
  "missing_fields": ["document_number", "deadline"],
  "warnings": ["建议查证 2025 年具体文件文号"],
  "evidence": [...],
  "score_breakdown": {...},
  "gating_applied": [],
  "reasoning_summary": "深圳人社局有 1 万元创业补贴政策；具体文号在公开页面未直接命中",
  "collected_at": "2026-05-21T...",
  "token_usage": { "provider": "deepseek", "model": "deepseek-chat",
    "prompt_tokens": 2840, "completion_tokens": 720, "total_tokens": 3560,
    "search_calls": 3, "fetch_calls": 5 }
}
```

`token_usage` 字段直接对应向父项目报销的口径。

## 项目结构

```
api/
├── pyproject.toml
├── Dockerfile / docker-compose.yml / .env.example
├── alembic.ini                    # DB 迁移配置
├── src/factcheck/
│   ├── main.py                    # FastAPI 入口
│   ├── config.py                  # 配置加载（Pydantic Settings）
│   ├── schemas.py                 # 全部 API 输入输出契约
│   ├── pipeline.py                # 7 步 pipeline 编排
│   ├── worker.py                  # Celery worker 入口
│   ├── api/                       # endpoints
│   │   ├── check.py / policy.py / audit.py / async_task.py / profiles.py
│   │   └── deps.py                # 鉴权 + DI
│   ├── llm/
│   │   ├── base.py
│   │   ├── openai_compatible.py   # DeepSeek/Qwen/Moonshot/GLM 共用
│   │   └── registry.py
│   ├── search/                    # Tavily / Bocha / Serper / Bing + orchestrator
│   ├── fetch/                     # http + Playwright + orchestrator
│   ├── extract/                   # QueryPlanner + FactExtractor (LLM 调用)
│   ├── verify/                    # CrossValidator (4 phase + 仲裁)
│   ├── score/                     # 评分引擎 + source_classify + data/
│   │   └── data/                  # 210+ 权威源 + 黑名单 + 模板
│   ├── cache/                     # Redis cache
│   ├── db/                        # SQLAlchemy models + session
│   └── utils/                     # prompts / json_parse / token_counter / logger / metrics
└── tests/
    ├── test_score_engine.py       # 评分引擎 8 个 case
    ├── test_source_classify.py    # URL 分类 8 个 case
    ├── test_schemas.py            # Pydantic 契约
    └── test_json_parse.py         # 容错 JSON
```

## 与父项目的集成场景

| 父项目业务点 | 调用方式 | 入口 |
|---|---|---|
| 8.2 地区政策入库流 | AI 抓取的政策原始页面 → 调本服务做先验核查 → 生成报告供人工审核 | `/v1/policy-check` |
| 8.3 行业方案知识库 | AI 初稿生成后，提取 N 条事实声明批量核查 | `/v1/solution-audit` |
| 5.5 / 9.2 对话流政策查询 | 用户问 "青岛创业补贴 2025"，对话流调本服务取证据后再生成答案 | `/v1/policy-check` 带 `question` |
| 数字员工 Agent 输出审核 | Agent 高风险事实陈述 → 本服务核查 → 不通过则改写 | `/v1/check` |
| 大批量入库 | 一次性 100+ claim | `/v1/check/async` + Celery worker |

## 已知限制

- Playwright 在 Docker 内启动 Chromium 约需 200MB 内存/实例，pool 默认 4
- 内容农场二级过滤（百家号、搜狐号子路径）目前只覆盖 25 个常见域名，建议运维定期补充
- 评测集仅 22 case，生产前建议补到 200+ 跑回归
- 缓存 TTL 是按 mode + policy_status 静态分级，没做"政策修订事件触发缓存失效"，少数被官方修订的数据缓存命中时不会自动刷新（在 30 天 TTL 内）

## License

内部项目。
