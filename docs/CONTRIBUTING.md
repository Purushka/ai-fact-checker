# 项目说明（给 Claude 接手用）

## 项目本质

AI 事实核查基础设施，服务父项目 OpenClaw 创业解决方案平台。父项目按 token 向本服务报销。无多租户，无独立备案（依附父项目）。

## 两份等价实现

1. **Claude Skill**（`docs/skill-spec/`）：Claude 直接调用，验证用
2. **FastAPI 服务**（`api/`）：生产部署用，部署到腾讯云与父项目同 VPC

两者**共用同一份**：权威源库 / 黑名单 / 模板 / 评分逻辑。Skill 的 `scripts/` 是 standalone 版，API 的 `src/factcheck/score/` 是类化版，逻辑一一对应。**修改评分规则时两边都要改**。

## 关键设计原则

1. **宁可错杀**：找不到官方源时倾向 `unverifiable`，不要给 `supported`
2. **5 维评分 + gating**：5 维加权和不能掩盖致命缺陷，必须过 gating（如无官方源封顶 40）
3. **返回必须可解释**：所有 confidence 都伴随 `score_breakdown` + `gating_applied` + `reasoning_summary`
4. **内容指纹去重**：N 家媒体转载同一稿件按 1 条计入 consistency
5. **二手引用降权 0.7**：通过 LLM 在 fact_extractor 阶段识别

## 重要路径

- `docs/skill-spec/SKILL.md` — skill 入口、当 product spec 看
- `docs/skill-spec/pipeline.md` — 7 步执行细则
- `docs/skill-spec/scoring.md` — 评分公式（必读）
- `api/src/factcheck/pipeline.py` — pipeline 编排主代码
- `api/src/factcheck/score/engine.py` — 评分引擎
- `api/src/factcheck/score/data/source_authority.json` — 210+ 权威源（修改时注意 tier 一致性）
- `api/src/factcheck/utils/prompts.py` — 所有 LLM prompt（这里改，逻辑就改）
- `api/tests/test_score_engine.py` — 评分引擎 8 个 case，先跑这个再改评分逻辑

## 我已知的限制（接手前请读）

详见 `PROCESS_LOG.md` 阶段三的"实测发现"和 `api/README.md` 的"已知限制"。

主要：
- Skill 阶段实测 10/22 case，准确率 80%，A 错 B 对（回归）1 个
- A02 GDP case 暴露真实业务边界：数据修订场景预设外
- H01 case 暴露：claim 措辞不精确时 partial vs unverifiable 边界过松，需要调 prompt

## 如何运行测试

```bash
cd api
pip install -e ".[dev]"
pytest                       # 所有单元测试
pytest tests/test_score_engine.py -v   # 评分引擎专项
```

## 如何加新权威源

编辑 `api/src/factcheck/score/data/source_authority.json` + `docs/skill-spec/data/source_authority.json`（两边都要改）。然后跑 `test_source_classify.py` 确认没回归。

## 不要做什么

- 不要在 main 流程里加多租户、API Key、计费——父项目按 token 报销已经覆盖
- 不要做"评分模型 ML 化"——评分必须确定性、可解释，规则即代码
- 不要把 LLM 调用埋在评分引擎里——score.py 纯规则，verify.py / extract.py 才用 LLM
