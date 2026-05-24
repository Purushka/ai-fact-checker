# AI Fact Checker

OpenClaw 创业平台的事实核查基础设施。本仓库包含两份等价实现：

| 形态 | 位置 | 用途 |
|---|---|---|
| **Claude Skill** | `docs/skill-spec/` | Claude 直接调用，用于验证流程、跑测试集 |
| **FastAPI 服务** | `api/` | 生产部署，按 token 向父项目报销 |

两者共用同一份数据：权威源库、内容农场黑名单、字段模板、评分逻辑、测试集。

## 快速跑

### 试 Skill（Claude 自调用）

```
Skill(skill="fact-check", args='{"claim":"2025年深圳市对登记失业人员实施一次性创业补贴每人1万元","mode":"policy"}')
```

### 试 API

```bash
cd api
cp .env.example .env
# 至少填 DEEPSEEK_API_KEY 和 TAVILY_API_KEY
docker compose up -d
curl http://localhost:8000/health
```

## 项目脉络

- `docs/skill-spec/SKILL.md`：skill 入口（产品定义）
- `docs/skill-spec/pipeline.md`：7 步流程
- `docs/skill-spec/scoring.md`：评分公式 + gating rules
- `docs/skill-spec/reports/run_initial_executed.json`：10 个 case 实测结果
- `api/README.md`：API 详细文档
- `PROCESS_LOG.md`：完整建设日志（中文 UTF-8）

## 与父项目集成

详见 `api/README.md` 的"与父项目的集成场景"和 `PROCESS_LOG.md` 阶段三部分。

简言之：

```
父项目 (OpenClaw) → POST /v1/policy-check → 返回 verdict + evidence + policy_meta
父项目按 token_usage.total_tokens 报销给本服务
```
