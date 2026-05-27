# Day 1.2 数据源可行性 — 4 类源逐个验证

## 执行摘要

| 源 | verdict | 关键指标 |
|---|---|---|
| Bilibili API | ✅ USABLE | 3/3 query OK, 1.63s, 公开无 token |
| RSS feeds | ⚠️ PARTIAL | 仅 5/10 存活 |
| Bocha | ✅ USABLE | 8 results, 7.7 dated, 1.44s |
| Metaso | ✅ USABLE | 8 results, 7.7 dated, 4.66s |
| AnySearch | ⚠️ WEAK | 8 results, **0 dated**, 6.85s |
| Tavily | ⏭️ SKIP | 无 API key — 见下方议案 |
| piyao.org.cn | ✅ USABLE | 5/5 OK, 0.48s, 64.6KB/page |

---

## (a) Bilibili 搜索 API

**端点**：`https://api.bilibili.com/x/web-interface/search/all/v2`

**调用方式**：
```python
params = {"keyword": q, "page": 1, "page_size": 10, "platform": "pc"}
headers = {"User-Agent": "Mozilla/5.0 ...", "Referer": "https://www.bilibili.com"}
```

**关键发现**：
- 无需 token，公开端点
- **必须**带 `Referer: https://www.bilibili.com` header，否则被反爬（412 错）
- 平均延迟 1.63s（3 query 测试）
- 返回字段含 `pubdate`（unix 时间戳）、`play`（播放数）、`author`、`bvid`、`title`、`description`
- 无显式频率限制；连续 3 query 间隔 0.5s 全部成功

**对系统价值**：
- B 站是中文短视频谣言重要源头之一
- pubdate 可直接用于传播时间线
- play 数可作为传播热度信号
- **建议接入**：作为 SearchProvider 注册到 orchestrator

**风险**：
- B 站可能调整反爬策略
- 视频字幕/弹幕未通过此 API 获取，需独立调 dm.bilibili.com
- 不返回长描述，需要二次抓取详情页

---

## (b) RSS feeds

**测试 10 个主流媒体**：

| 媒体 | URL | HTTP | items | verdict |
|---|---|---|---|---|
| 新华社 | xinhuanet.com/.../news_politics.xml | 200 | 5 | ✅ ok |
| 人民日报 | people.com.cn/rss/politics.xml | 200 | 1 | ✅ ok |
| 光明网 | gmw.cn/rss/news.xml | 200 | 0 | ❌ 返回 HTML |
| 中国新闻网 | chinanews.com/.../scroll-news.xml | 200 | 6 | ✅ ok |
| 财新网 | caixin.com/rss/economy.xml | 200 | 0 | ❌ 返回 HTML 落地页 |
| 第一财经 | yicai.com/rss/feed/9.xml | 200 | 0 | ❌ 返回 HTML |
| 36 氪 | 36kr.com/feed | 200 | 1 | ✅ ok |
| 少数派 | sspai.com/feed | 200 | 4 | ✅ ok |
| 界面新闻 | jiemian.com/.../95.html.rss | 200 | 0 | ❌ 返回 HTML |
| 澎湃新闻 | thepaper.cn/rss_chnId_25.jsp | 200 | 0 | ❌ 返回 HTML |

**关键发现**：
- 5/10 真正活着，5/10 返回 HTML 但 URL 看起来像 RSS（已死或被重定向）
- 存活的均为 XML 格式，可被 feedparser 直接解析
- 财新/澎湃/界面这类高质量财经/社会类 RSS 已不维护，**主流媒体 RSS 已严重退化**

**对系统价值**：
- RSS 是被动订阅，**无法做事件触发的查询**，不适合定点核查
- 适合做"主流媒体 inflow 监控"：被动收集近期发布，建索引后供查询
- 5 个存活源不足以代表全媒体生态

**结论**：
- RSS 优先级**降低**，不是核心数据源
- 仅作为 inflow 补充（如果需要做"近 24h 主流报道"，5 个还不够）
- 替代方案：直接抓官网 sitemap，或调用 Search API 限定 domain

---

## (c) Web 搜索 API（Bocha / Metaso / AnySearch）

**测试**：3 个 case 的中文 query × 3 个 provider

| Provider | avg results | dated/8 | avg latency | verdict |
|---|---|---|---|---|
| Bocha | 8.0 | **7.7** | 1.44s | ✅ USABLE |
| Metaso | 8.0 | **7.7** | 4.66s | ✅ USABLE |
| AnySearch | 8.0 | **0.0** | 6.85s | ⚠️ WEAK |

**关键发现**：
- Bocha 综合最优：快 + 几乎全部带日期 + 中文召回完整
- Metaso 慢但日期完整，作 fallback 优秀
- **AnySearch 不返回 published_at**，这对传播时间线追溯是硬伤
- 三家结果有重叠也有差异，组合能扩大召回

**对系统价值**：
- Bocha 应作为**主**搜索 provider
- Metaso 作 fallback
- AnySearch 仅当前两者失败时启用，且要忍受无日期

---

## (d) Tavily — 跳过

**未测原因**：当前 .env 无 TAVILY_API_KEY

**议案**：
- 我们的 3 家中文 provider 已经覆盖了 Bocha/Metaso/AnySearch
- Tavily 是英文为主，中文召回历史测试中弱于 Bocha
- 不建议为这个项目专门付费接入 Tavily
- 如果需要英文核查（如 reuters/bbc），考虑直接用 Bing API 或 google-cse

---

## (e) piyao.org.cn

**测试**：连续 5 次抓首页

| 指标 | 值 |
|---|---|
| 成功率 | 5/5 |
| 平均延迟 | 0.48s |
| 平均页面大小 | 64.6 KB |
| 反爬 | 无（普通 UA 即可） |
| 已有爬虫 | eval/scraper/piyao_scraper.py（含 LLM 提取） |

**对系统价值**：
- piyao 是中国官方辟谣权威源，必接
- 已有完整爬虫 + 25 case 落地数据
- 在 query_planner.py 中已加 `site:piyao.org.cn` 自动扩展

---

## 综合建议（对 Phase 1 后续）

1. **核心数据源**（必接）：
   - 商业搜索：Bocha（主）+ Metaso（fallback）
   - 短视频：Bilibili API
   - 辟谣：piyao.org.cn 爬虫

2. **次级数据源**（按需）：
   - RSS：仅新华社 + 人民日报 + 中国新闻网 + 36 氪 + 少数派（5 个有效）
   - AnySearch：第三 fallback，需忍受无日期

3. **缺失数据源**（需补）：
   - 微博/小红书原始爆料（前面 1.1 已暴露盲区）
   - Web Archive（archive.org）历史快照查询
   - 这两者是"看到谣言起源"的关键，但合规/技术复杂度高

4. **下一步**：
   - 1.3 用 Bocha + Metaso + Bilibili 跑 P019 完整 pipeline，记录每 step 成本
   - 2.x 验证近似检测 + 传播链构建
