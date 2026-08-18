# Jingcai 3.5 Alpha — Stage 3 Data Layer

## 当前治理状态

- **实际资金 / staking baseline：Jingcai 3.4**
- **3.5 Alpha Stage 2：概率核心候选通过；完整投注层未晋级**
- **Stage 3：数据层已建立，开始积累严格时间戳研究数据**
- 外部市场：仅用于验证、分歧、过热和 Risk Gate；**正向概率融合仍未激活**
- Residual ML、动态球队/xG、多比分分布：**仍为 Shadow / 诊断**
- 新 S/A1/A2 阈值：**未冻结**
- HAD Strong Confidence：**仍不能冒充 CRS 比分 S**

## Stage 3 解决的问题

Stage 2 最大缺口不是“再换一个模型”，而是没有历史同一时刻的体彩衍生玩法和全球市场价格。Stage 3 因此建立可审计的数据链：

1. 同一体彩请求保存 **HAD + HHAD + CRS + TTG + HAFU**。
2. 同一采集批次保存全球多公司 **1X2 / spreads / totals**。
3. 所有采集都保存 UTC `collected_at`、源更新时间、原始 JSON SHA256 和 raw 文件路径。
4. SQLite 使用 append-only 时间快照：**即使赔率没变，也保留新的采集时点**。
5. 统一批次采用 **外盘先抓、体彩最后抓**；体彩时间作为执行/决策锚点，研究导出禁止使用晚于体彩采集时刻的外盘。
6. 中文体彩队名与国际公司队名不做危险的模糊自动匹配。只允许：
   - 直接 match_id；
   - 精确标准化队名；
   - 明确 alias；
   - 手工 direct link。
7. 支持赛前固定锚点和 closing 导出，默认锚点为 T-24h / T-6h / T-60m / T-15m。
8. Closing 不只是“比赛前最后一条”，还要求快照新鲜度，避免把几小时前的旧价冒充临场价。
9. 输出包含 Sporttery 自身 CLV-ready 字段，以及外盘逐公司原始报价，供后续计算 EV / CLV / ROI / Max Drawdown。

## 核心文件

- `jingcai_3_5_alpha_stage3_data.py`：Stage 3 数据层主脚本。
- `jingcai_3_5_alpha.py`：3.5 Alpha 主模型代码（不因 Stage 3 自动晋级）。
- `jingcai_3_5_alpha_stage2.py`：Stage 2 治理包装层。
- `stage3_external_sample.json`：外盘标准化导入示例。
- `stage3_aliases_sample.csv`：队名 alias 示例。
- `stage3_results_sample.csv`：赛果导入示例。

## 数据库表

- `capture_run`：每次来源采集记录，带 `batch_id`、时间、raw SHA256。
- `match_dim`：体彩比赛身份维表。
- `sporttery_snapshot`：每场每次采集的五池快照。
- `external_event`：外部公司赛事维表。
- `external_event_snapshot`：外部赛事每次时间快照。
- `external_market_quote`：逐公司、逐市场、逐 outcome 报价。
- `event_link`：体彩赛事 ↔ 外部赛事映射。
- `team_alias`：显式队名映射。
- `result`：最终赛果。

## 最小运行流程

### 1. 自检

```bash
python jingcai_3_5_alpha_stage3_data.py self-test
```

必须返回：

```text
"self_test": "PASS"
```

### 2. 初始化数据库

```bash
python jingcai_3_5_alpha_stage3_data.py init \
  --db data/jingcai_stage3.db
```

### 3. 最推荐：同批采集体彩 + 全球市场

需要外部赔率 API key 时，通过环境变量提供：

```bash
export ODDS_API_KEY="YOUR_KEY"
```

然后按实际覆盖联赛重复传入 `--sport-key`：

```bash
python jingcai_3_5_alpha_stage3_data.py capture-batch \
  --db data/jingcai_stage3.db \
  --raw-dir data/raw \
  --sport-key YOUR_SPORT_KEY_1 \
  --sport-key YOUR_SPORT_KEY_2
```

重要：`capture-batch` 内部顺序固定为 **外盘 → 体彩**，以避免把未来几秒的外盘混入体彩决策时点。

### 4. 只有体彩时也可先采集

```bash
python jingcai_3_5_alpha_stage3_data.py capture-sporttery \
  --db data/jingcai_stage3.db \
  --raw-dir data/raw
```

严格 Stage 3 最终验证仍要求同期外部市场；只有体彩的记录可以保存，但不能冒充完整验证样本。

### 5. 导入已有原始体彩 JSON

```bash
python jingcai_3_5_alpha_stage3_data.py ingest-sporttery \
  --db data/jingcai_stage3.db \
  --input old_sporttery_raw.json \
  --collected-at 2026-08-17T10:00:00+00:00
```

只有在真实采集时间已知时才填写 `--collected-at`。不知道时间时不要猜。

### 6. 导入外盘 JSON

标准化格式：

```bash
python jingcai_3_5_alpha_stage3_data.py ingest-external \
  --db data/jingcai_stage3.db \
  --input stage3_external_sample.json \
  --format normalized
```

The Odds API 原始格式：

```bash
python jingcai_3_5_alpha_stage3_data.py ingest-external \
  --db data/jingcai_stage3.db \
  --input odds_api_raw.json \
  --format the_odds_api \
  --sport-key YOUR_SPORT_KEY
```

## 赛事映射

### 批量 alias

```bash
python jingcai_3_5_alpha_stage3_data.py ingest-aliases \
  --db data/jingcai_stage3.db \
  --input stage3_aliases_sample.csv
```

### 导出未解决映射候选

```bash
python jingcai_3_5_alpha_stage3_data.py export-unresolved \
  --db data/jingcai_stage3.db \
  --output unresolved_links.csv
```

### 明确手工绑定

```bash
python jingcai_3_5_alpha_stage3_data.py link-event \
  --db data/jingcai_stage3.db \
  --match-id "周一001" \
  --source "the_odds_api" \
  --source-event-id "EXTERNAL_EVENT_ID"
```

## 导入赛果

```bash
python jingcai_3_5_alpha_stage3_data.py ingest-results \
  --db data/jingcai_stage3.db \
  --input stage3_results_sample.csv
```

## 严格同步数据导出

逐快照同步层：

```bash
python jingcai_3_5_alpha_stage3_data.py export-aligned \
  --db data/jingcai_stage3.db \
  --output stage3_aligned.jsonl \
  --max-sync-gap-seconds 300
```

默认要求：

- 体彩五池齐全；
- 有已映射外部市场；
- 外盘采集不晚于体彩；
- 两者时间差不超过限制。

## OOS / CLV 锚点数据导出

```bash
python jingcai_3_5_alpha_stage3_data.py export-anchors \
  --db data/jingcai_stage3.db \
  --output stage3_anchors.jsonl \
  --anchors 1440,360,60,15 \
  --close-buffer-minutes 2 \
  --max-external-gap-seconds 600 \
  --min-external-books 2 \
  --max-anchor-staleness-minutes 30 \
  --max-close-staleness-minutes 15
```

核心无泄漏规则：

- Anchor 只能使用 `<= kickoff - lead` 的最后一次体彩快照。
- Close 只能使用 `<= kickoff - close_buffer` 的最后一次体彩快照。
- 外盘也只能使用 `<= 对应体彩快照时间` 的记录。
- Anchor/Close 超出允许 staleness 就丢弃，不能把旧价伪装成临场价。

导出中的 `clv_ready` 包含：

- `price_ratio_minus1`：体彩 anchor odds / close odds - 1；
- `devig_prob_move_pp`：体彩去水概率从 anchor 到 close 的百分点变化。

最终策略级 EV / ROI / Max Drawdown 要在模型给出真实下注选择后计算，数据层本身不替模型下注。

## 审计

```bash
python jingcai_3_5_alpha_stage3_data.py audit \
  --db data/jingcai_stage3.db \
  --sync-gap-seconds 300 \
  --output stage3_audit.json
```

重点看：

- `five_pool_complete_rate`
- `linked_match_rate`
- `synchronized_snapshot_rate`
- HAD / HHAD / CRS / TTG / HAFU 各自覆盖率
- 同步时间差 median / p95 / max

## Stage 3 晋级原则

完成数据采集 ≠ 3.5 正式晋级。

只有在积累足够历史快照后，才能重新做严格时间序列 Walk-forward / OOS，并同时验证：

1. HAD 概率 proper scores；
2. Sporttery 真实执行价 EV；
3. CLV；
4. 分玩法 ROI；
5. 最大回撤；
6. S/A1/A2 分层稳定性；
7. CRS S 与 HAD Strong Confidence 必须分开治理。

在这些条件满足前：**3.4 继续负责真实资金；3.5 Stage 3 负责数据与研究。**
