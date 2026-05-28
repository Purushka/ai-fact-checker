# Contributing

感谢有兴趣参与这个项目。下面是开发者需要知道的核心信息。

## 项目两层结构

1. **`docs/skill-spec/`** — 评分逻辑、权威源库、内容农场黑名单、字段模板等**核心配置 + 设计文档**
2. **`api/`** — FastAPI 主服务，生产部署形态

`docs/skill-spec/scripts/` 是 standalone 版评分脚本，`api/src/factcheck/score/` 是类化版，**逻辑一一对应**。修改评分规则时**两边都要改**，保持一致。

## 关键设计原则

1. **宁可错杀**：找不到官方源时倾向 `unverifiable`，不要给 `supported`
2. **5 维评分 + gating**：5 维加权和不能掩盖致命缺陷，必须过 gating（如无官方源封顶 40）
3. **返回必须可解释**：所有 confidence 都伴随 `score_breakdown` + `gating_applied` + `reasoning_summary`
4. **内容指纹去重**：N 家媒体转载同一稿件按 1 条计入 consistency
5. **二手引用降权 0.7**：通过 LLM 在 `fact_extractor` 阶段识别
6. **LLM 调用与评分逻辑分离**：`score.py` 纯规则，`verify.py` / `extract.py` 才用 LLM

## 重要路径

| 路径 | 作用 |
|---|---|
| `docs/skill-spec/SKILL.md` | Skill 入口，等价于 product spec |
| `docs/skill-spec/pipeline.md` | 7 步执行细则 |
| `docs/skill-spec/scoring.md` | 评分公式（必读） |
| `api/src/factcheck/pipeline.py` | Pipeline 主编排 |
| `api/src/factcheck/score/engine.py` | 评分引擎 |
| `api/src/factcheck/score/data/source_authority.json` | 210+ 权威源（修改时注意 tier 一致性） |
| `api/src/factcheck/utils/prompts.py` | 所有 LLM prompt |
| `api/tests/test_score_engine.py` | 评分引擎专项测试，改逻辑前先跑 |

## 开发流程

```bash
cd api
pip install -e ".[dev]"
pre-commit install              # 装 hook
pytest                          # 跑所有单元测试（应 150 passed）
pytest tests/test_score_engine.py -v   # 评分引擎专项
```

## 加新权威源

编辑两份 JSON 保持一致：
- `api/src/factcheck/score/data/source_authority.json`
- `docs/skill-spec/data/source_authority.json`

然后跑 `pytest tests/test_source_classify.py` 确认没回归。

## 加新 search provider

参考 `api/src/factcheck/search/bocha.py` 实现 `SearchProvider` 协议，并在 `orchestrator.py` 注册。

## 加新 LLM provider

参考 `api/src/factcheck/llm/deepseek.py` 实现 `LLMProvider` 协议，并在 `get_provider()` 中注册。

## 已知限制

- 单条调用 LLM tokens ~5k，搜索 calls ~8 次，**搜索成本占总成本 97%**
- Fetch 成功率约 50-60%（trafilatura），部分 JS 渲染的政府门户抓不到
- 地方/最新政策搜索覆盖差（参考 `experiments/day1/source_feasibility.md`）
- benchmark 准确率 ~50%（55 case live eval），存在改进空间

## Pull Request 规范

- 单一职责：一个 PR 只做一件事
- 必须带测试：新功能需配套单元测试 + 集成测试
- 代码格式：`ruff format` + `ruff check`，提交前自动跑 pre-commit
- 提交信息使用 conventional commits 风格：`feat:` / `fix:` / `refactor:` / `docs:` / `test:`

## 不建议做的方向

- **不要把多租户 / API key / 计费埋进 main pipeline**：这些应该在 gateway 层
- **不要做"评分模型 ML 化"**：评分必须确定性、可解释，规则即代码
- **不要在 score.py 里调 LLM**：评分纯规则，verify.py / extract.py 才用 LLM
