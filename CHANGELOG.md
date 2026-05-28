# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `InferenceChainBuilder` — bge-large-zh embedding 推演 evidence 间的传播链 + `claim_alignment` 信号
- `PropagationTimeline` — URL 域名分类 + 时间排序，识别 5 种传播模式
- `ChainNode` / `InferenceChain` / `PropagationStop` Pydantic schemas
- piyao.org.cn 集成（爬虫 + query planner site-限定查询）
- 传播性谣言识别 prompt（fact_extractor Rule 7 + query_planner viral_claim heuristic）
- AnySearch search provider
- Metaso search provider
- Bilibili API integration（公开端点，需 Referer header）
- ConfidenceInterval bootstrap_5dim_n200
- 200 case calibration benchmark with reliability diagrams
- 55 case piyao live eval benchmark + 失败模式分析
- Cross-LLM consistency tests (DeepSeek / Qwen / Kimi / GLM)
- 150 unit + integration tests

### Changed
- Pattern detection 顺序调整：`official_dissemination` 优先于 `coordinated`
- `coordinated` 模式 tighten 为 `first_prio <= 4`（需起点非权威）

### Removed
- Tavily search provider（替换为 Bocha + Metaso + AnySearch 组合）

## [0.1.0] - 2026-05-21

### Added
- Initial release
- 7-step Pipeline: QueryPlanner → Search → Fetch → Extract → CrossValidate → Score → Generate
- 5-dimensional ScoreEngine with gating rules
- FastAPI service with REST endpoints (`/v1/check`, `/v1/policy-check`, `/v1/solution-audit`)
- DeepSeek LLM provider + OpenRouter fallback
- 210+ Chinese authoritative source database
- Redis cache + Celery async worker support
- PostgreSQL + Alembic migrations
- Docker compose deployment
