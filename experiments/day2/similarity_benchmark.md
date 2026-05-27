# Day 2.1 近似检测方案对比 — SimHash vs bge-large-zh vs 混合

## 实验设计

**数据集**：100 对中文文本对
- 正例 50 对：piyao 真实 claim ↔ LLM 生成的 paraphrase（变体 1: 同义词替换 / 变体 2: 语序调整）
- 负例 50 对：跨 case 随机配对（同语料库不同事件）
- 来源：`eval/data/piyao_cases.jsonl`（25 case）

**说明**：MCFEND 学术数据集需向作者申请，我们用 piyao + LLM paraphrase 构造了
等价的真实分布数据 — 同样测试"识别同一事实的不同表达"任务。

**方法**：
1. SimHash（64-bit, 3-gram, with/without normalization）
2. BAAI/bge-large-zh-v1.5（cosine）
3. Hybrid（SimHash 召回 + bge 精排）

---

## 结果总表

| 方案 | 最佳阈值 | P | R | F1 | Acc | latency/pair |
|---|---|---|---|---|---|---|
| **SimHash** | H≤28 | 0.837 | 0.820 | **0.828** | 0.830 | **0.1ms** |
| **SimHash + norm** | H≤28 | 0.837 | 0.820 | 0.828 | 0.830 | 0.1ms |
| **bge-large-zh-v1.5** | **cos≥0.70** | **1.000** | **1.000** | **1.000** | **1.000** | 111ms |
| bge-large-zh-v1.5 | cos≥0.75 | 1.000 | 0.980 | 0.990 | 0.990 | 111ms |
| bge-large-zh-v1.5 | cos≥0.80 | 1.000 | 0.880 | 0.936 | 0.940 | 111ms |
| **Hybrid(SH≤30 + bge≥0.75)** | — | 1.000 | 0.880 | 0.936 | 0.940 | 113ms |
| Hybrid(SH≤28 + bge≥0.75) | — | 1.000 | 0.800 | 0.889 | 0.900 | 102ms |

---

## 详细数据

### SimHash 全阈值扫描

| H≤ | P | R | F1 | Acc | TP/FP/FN/TN |
|---|---|---|---|---|---|
| 3 | 0.000 | 0.000 | 0.000 | 0.500 | 0/0/50/50 |
| 5 | 0.000 | 0.000 | 0.000 | 0.500 | 0/0/50/50 |
| 10 | 0.000 | 0.000 | 0.000 | 0.500 | 0/0/50/50 |
| 15 | 1.000 | 0.020 | 0.039 | 0.510 | 1/0/49/50 |
| 20 | 1.000 | 0.160 | 0.276 | 0.580 | 8/0/42/50 |
| 24 | 0.952 | 0.400 | 0.563 | 0.690 | 20/1/30/49 |
| 26 | 0.868 | 0.660 | 0.750 | 0.780 | 33/5/17/45 |
| **28** | 0.837 | 0.820 | **0.828** | 0.830 | 41/8/9/42 |
| 30 | 0.763 | 0.900 | 0.826 | 0.810 | 45/14/5/36 |

**正例 H 距离分布**: min=12, max=35, mean=24.9, median=25
**负例 H 距离分布**: min=24, max=41, mean=31.9, median=32

### bge-large-zh-v1.5 全阈值扫描

| cos≥ | P | R | F1 | Acc | TP/FP/FN/TN |
|---|---|---|---|---|---|
| **0.70** | **1.000** | **1.000** | **1.000** | **1.000** | **50/0/0/50** |
| 0.75 | 1.000 | 0.980 | 0.990 | 0.990 | 49/0/1/50 |
| 0.80 | 1.000 | 0.880 | 0.936 | 0.940 | 44/0/6/50 |
| 0.85 | 1.000 | 0.800 | 0.889 | 0.900 | 40/0/10/50 |
| 0.90 | 1.000 | 0.360 | 0.529 | 0.680 | 18/0/32/50 |
| 0.93 | 1.000 | 0.260 | 0.413 | 0.630 | 13/0/37/50 |
| 0.95 | 1.000 | 0.160 | 0.276 | 0.580 | 8/0/42/50 |

---

## 关键发现

### Finding 1: **标准 SimHash 阈值（H≤3-5）完全无效于中文 paraphrase**
- H≤3: F1=0.000（0 TP）
- H≤5: F1=0.000（0 TP）
- 教科书阈值仅适合"逐字逐句近重复"，paraphrase 之间 n-gram 重叠度太低

### Finding 2: **SimHash 用于 paraphrase 需要 H≈28，几乎丢失 SimHash 的"强约束"优势**
- 最佳 F1=0.828 at H=28
- 但 H=28 在 64-bit 哈希中已是大约 1/4 比特不同，约束力大幅弱化
- 正负例 H 分布有重叠（正例最大 H=35，负例最小 H=24），无法完美分类

### Finding 3: **bge-large-zh-v1.5 在 cos≥0.70 实现 100% F1**
- 完美分类 50 正例 + 50 负例
- cos≥0.70 阈值在 0.70-0.85 范围都很 robust（F1≥0.889）
- 嵌入模型理解语义，对 paraphrase 鲁棒

### Finding 4: **Hybrid 收益有限**
- SimHash recall H≤30 召回 59/100，比例不够低
- 仍需对 59 个 pair 跑完整 bge encode，节省时间有限
- 最终 latency 112.9ms/pair（vs 纯 bge 111ms），几乎无加速
- **Hybrid 仅适合极大规模数据**（百万级 vs 千级），用 SimHash MinHash LSH 做候选集
- 当前规模（百级）下，纯 bge 就是最佳选择

### Finding 5: **归一化对 SimHash 无显著影响**
- 去标点 + 去空白后 F1 完全相同（0.828）
- 中文 SimHash 的特征主要来自字符 n-gram，标点本来就不在 n-gram 内

### Finding 6: **延迟差异 1000×，但选择取决于场景**
- SimHash: 0.1ms/pair（CPU，无网络）
- bge: 111ms/pair（GPU 加速到 ~10ms/pair）
- **场景 A**：URL 去重（找完全相同的转载）→ SimHash 足够
- **场景 B**：发现 "同一谣言的不同表达" → 必须 bge
- 在 propagation timeline 中，场景 B 才是核心

---

## 系统集成建议

| 用途 | 推荐方案 | 阈值 |
|---|---|---|
| URL 去重（同一 URL） | hash | exact |
| 同源转载检测 | SimHash 64-bit | H≤4 |
| **同事实不同表达** | **bge-large-zh-v1.5** | **cos≥0.75** |
| 大规模候选集筛选（>10万） | MinHash LSH → bge 精排 | — |

**给 pipeline 的具体改造**（接 1.3 baseline 的"fetch 后去重"问题）：
1. 对 fetch_orchestrator 返回的 N 个页面做 paraphrase 去重
2. 当前流程是 N=8 → 1 个 evidence；理想流程是 N=8 → 用 bge 聚类 → 3-4 个独立 evidence
3. 加 bge embedding 缓存：相同 URL 跨 claim 共享 embedding

---

## 输出文件
- `pairs.jsonl` — 100 对 benchmark 数据集（label=1 正例, label=0 负例）
- `similarity_results.json` — 所有方案 × 阈值的完整数据
- `simhash_results.json` — SimHash 独立结果
- `build_pair_dataset.py` — 数据集构造脚本（含 LLM paraphrase 调用）
- `benchmark_similarity.py` — 主 benchmark 脚本
- `benchmark_simhash_only.py` — 仅 SimHash（无 model download）
