# Pipeline 执行细则

每步明确：输入 / 输出 / 操作 / 用什么工具 / 失败处理。

---

## 步骤 1 — QueryPlan

**输入**：`claim` 或 `question`（原始字符串）+ `mode`

**操作**：
1. 提取实体：人物、机构、地点（精确到省/市/区县）、时间锚点、数字、文件名
2. 识别意图类别：政策查询 / 数据核查 / 状态确认 / 事件验证
3. 拆子核查点：复合 claim 切成单一可核查命题
4. 生成搜索词组合（每个子核查点 2-4 条搜索词，覆盖不同角度）：
   - 直接复述：原文关键词
   - 官方定位：实体 + 官方机构名 + "通知/公告/规定"
   - 文号定位：实体 + "字〔20XX〕XX号"模式
   - 时间限定：实体 + 年份/季度

**输出**：
```json
{
  "entities": ["青岛市", "高校毕业生", "创业补贴"],
  "time_anchor": "2025",
  "region": { "level": "city", "name": "青岛市" },
  "sub_claims": [
    { "id": "sc1", "text": "青岛市2025年对首次创业的高校毕业生发放一次性创业补贴" },
    { "id": "sc2", "text": "该补贴金额为5000元" }
  ],
  "search_queries": [
    "青岛市 首次创业 高校毕业生 一次性创业补贴 2025",
    "青岛市人社局 高校毕业生 创业补贴 通知",
    "site:qd.gov.cn 高校毕业生 创业补贴 5000",
    "山东省 青岛 创业补贴 2025 文件"
  ]
}
```

**工具**：纯 Claude 推理，无外部调用。

---

## 步骤 2 — Search

**输入**：QueryPlan 的 `search_queries`

**操作**：
1. 对每条搜索词调用 WebSearch，limit 设置为每条 5-8 条结果
2. 合并所有结果，按 URL 去重
3. 第一层过滤：
   - 黑名单（`content_farm_blacklist.json`）直接丢弃
   - 搜索聚合页（baidu.com/sogou.com 的结果页 URL）丢弃
   - 重复域名的同主题多结果，每域最多保留 2 条
4. 按预估权威性排序：gov.cn 顶部 > 央媒 > 行业协会 > 主流媒体 > 其它

**输出**：候选 URL 列表（最多 `max_sources × 2` 条），每条带初步分类标签

**工具**：WebSearch（若不可用，降级用 Bash 调系统的 search 实现或返回 unverifiable）

**失败处理**：所有搜索都返回 0 条 → `verdict = unverifiable`，confidence = 0，warning = "搜索引擎无相关结果"

---

## 步骤 3 — Fetch

**输入**：候选 URL 列表

**操作**：
1. 用 WebFetch 抓每个 URL 的主要文本内容
2. 失败处理：
   - HTTP 错误（4xx/5xx）→ 标记 `blocked`，丢弃
   - 空白页或反爬 → 标记 `blocked`，丢弃
   - 超时 → 重试 1 次后丢弃
3. 保存抓取时间戳

**输出**：
```json
[
  {
    "url": "http://qdhrss.qingdao.gov.cn/...",
    "title": "页面标题",
    "content": "正文文本",
    "fetched_at": "ISO8601",
    "status": "ok | blocked"
  }
]
```

**工具**：WebFetch

**失败处理**：所有 fetch 都失败 → 搜索结果摘要降级用作 evidence snippet（标注 `from_search_snippet_only`）

---

## 步骤 4 — Extract

**输入**：fetch 成功的页面 + `mode` + 原始 claim

**操作**：
1. 每个页面调用 `scripts/source_classify.py`（用 URL 查 `source_authority.json`）得到：
   - `source_type`
   - `authority_weight`
   - `agency`（如能从 URL/标题/正文中识别出具体机关）
2. 按 `mode` 选模板从 `data/completeness_templates.json` 中取字段列表，对每个字段在正文中抽取：
   - 直接字符串匹配 + 正则（如文号 `〔20\d{2}〕第?\d+号`、金额 `\d+ ?元/万元`、日期格式）
   - 正文中没有的字段标记 `null` 并加入 `missing_fields`
3. 抽取 snippet：摘出页面中**最直接支持/反驳 claim 的 1-3 句话**，每条不超过 200 字
4. 判定该证据对 claim 的支持级别：`strong | weak | neutral | contradicted`
5. 提取页面声明的发布时间、更新时间

**输出**：每个 URL 一个 evidence 对象（完整 evidence 结构见 SKILL.md）

**工具**：纯 Claude 推理 + Bash 调 `scripts/source_classify.py`

---

## 步骤 5 — CrossValidate

**输入**：所有 evidence + 原始 claim

**操作**（4 个 phase 并行执行，全部用 Claude 推理）：

### Phase A — 官方一致性
- 筛 `source_type ∈ {official, regulator}` 的 evidence
- 比对它们对同一字段（金额、日期、条件）是否描述一致
- 不一致的字段记入 `conflicts`，标记 conflict_level=high

### Phase B — 官方 vs 第三方
- 比对官方与非官方 evidence 是否一致
- 非官方与官方冲突 → 非官方降权 × 0.6
- 非官方独有的字段（官方未提及）→ 标记为"待官方核实"，加入 warnings

### Phase C — 矛盾检测
- 数值冲突：同一字段不同 evidence 给出不同数值
- 时间冲突：发布时间 vs 生效时间矛盾、过期时间已到
- 条件冲突：申请条件描述互斥
- 全部矛盾入 `conflicts`

### Phase D — 要素遗漏扫描
- 按模板字段检查 `missing_fields`
- 政策类至少应有：issuing_agency、publish_date、benefit_amount、applicable_subjects
- 关键字段全部缺失 → warning "未找到 X 字段，建议人工补充"

### 冲突仲裁规则
当多条权威 evidence 矛盾时按下列优先级：
1. **机关层级**：national > province > city > district
2. **时间新旧**：发布日期晚的胜出
3. **原始性**：原文发布站点 > 转载站点
4. 仲裁无果 → `verdict = conflicting`，confidence 封顶 35

**输出**：
```json
{
  "validated_evidence": [...],
  "conflicts": [...],
  "warnings": [...],
  "missing_fields": [...]
}
```

---

## 步骤 6 — Score

**输入**：validated_evidence + conflicts + missing_fields + scoring_weights + 原始 claim

**操作**：调用 `scripts/score.py`，传入 JSON，得到完整 score_breakdown + verdict + gating_applied。

详细规则见 `scoring.md`。

**输出**：完整 score_breakdown + verdict + confidence + gating 列表

**工具**：Bash 调 `scripts/score.py`

---

## 步骤 7 — Generate

**输入**：所有前序步骤结果

**操作**：
1. 组装最终 JSON 结构（按 SKILL.md 中输出契约）
2. 仅当输入是 `question` 时生成自然语言 `answer`：
   - 用 evidence 中支持级别 strong 的内容综合
   - 必须包含至少 1 个 source_url 引用
   - 若 `verdict ∈ {unverifiable, conflicting, contradicted}`，answer 必须明确表达不确定性
3. 写 `reasoning_summary`：中文一段话（30-80 字），说明判断主要依据
4. 设置 `collected_at` 为当前 ISO8601 时间

**输出**：完整 JSON，向调用方返回

---

## 整体超时与上限

- 单次核查总时长上限：30 秒（API 阶段强制）
- 搜索调用次数：每 claim 上限 5
- Fetch 并发：8
- 单 URL fetch 超时：8 秒
- LLM 推理（即 Claude 自己）每步上限不强制，但应力求简短

## 退出条件

任一情况立即终止 pipeline 并按对应 verdict 返回：
- 步骤 1 输入根本不是事实陈述 → `verdict = out_of_scope`
- 步骤 2 搜索 0 结果 → `verdict = unverifiable`
- 步骤 3 全部 fetch 失败且无搜索 snippet → `verdict = unverifiable`
- 任何步骤抛异常 → `verdict = unverifiable`，warning 中记录异常
