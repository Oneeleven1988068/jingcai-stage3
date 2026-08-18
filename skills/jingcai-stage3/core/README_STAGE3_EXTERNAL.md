# Jingcai 3.5 Alpha Stage 3 — External Market Bridge v1

## 定位

本层只负责**全球多公司赔率的标准化、时点对齐、分歧/过热审计、Risk Gate 候选信号与 CLV 参照**。

治理状态保持不变：

- 实际资金 / staking baseline：**Jingcai 3.4**
- Jingcai 3.5：**Alpha Stage 3 数据研究层**
- 外部市场正向概率融合：**关闭**
- 外部市场允许用途：联网验证、分歧检测、过热检测、Risk Gate、CLV/EV/ROI/最大回撤研究
- 本脚本**不能自动改变 S/A1/A2 评级**
- Closing 市场数据仅用于事后 CLV 标签/评估，禁止作为更早时点的赛前 feature

## 已实现

1. 读取 The Odds API 风格的 JSON（当前赔率数组、历史 wrapper、推荐的 Stage3 envelope）。
2. 读取统一 CSV 长表。
3. 支持 1X2 / Spread / Total，并保留可用的 BTTS / Correct Score / HAFU 原始市场。
4. 每家公司先独立去水：Multiplicative + Power；两者均保存。
5. 外部 1X2 共识：每家公司多算法去水平均后，再跨公司取 median；同时计算 MAD 离散度。
6. 严格时点规则：Risk Gate 只能使用 `external_capture_time <= sporttery_capture_time` 的最近外盘快照。
7. 外部 closing：仅取 `kickoff - close_buffer` 之前的最后一个外盘快照。
8. 输出 closing 字段时强制 `label_only_for_clv=1`、`feature_use_forbidden=1`。
9. Sporttery HHAD 与外部 two-way spread 不强行等价；spread 只保留为 margin 诊断证据。
10. 支持球队别名表；另支持 `provider + sport_key -> 体彩联赛` 映射，解决中英文队名无法直接 fuzzy-match 的问题。
11. 当联赛已映射且开赛时间唯一时，可生成 `team_alias_suggestions.csv`，用于后续人工确认/固化别名。
12. 可将外盘表追加到原有 `stage3.db`。

## 输入结构

推荐把外部 API 原始响应包一层：

```json
{
  "provider": "the_odds_api",
  "fetched_at_utc": "2026-08-17T02:00:00Z",
  "data": [ ...原始 event 数组... ]
}
```

`fetched_at_utc` 非常重要，它是“我们真正观察到这组外盘”的时间。

## 运行

```bash
python jingcai_stage3_external_market_v1.py self-test
```

```bash
python jingcai_stage3_external_market_v1.py write-templates \
  --output-dir ./external_templates
```

```bash
python jingcai_stage3_external_market_v1.py build \
  --stage3-dir ./stage3_parser_builder_output \
  --external-input-dir ./external_raw \
  --output-dir ./stage3_external_build \
  --aliases ./team_aliases.csv \
  --league-aliases ./league_aliases.csv
```

## 输出

- `external_events.csv`
- `external_quotes_long.csv`
- `external_match_map.csv`
- `external_1x2_bookmaker_devig.csv`
- `external_consensus_1x2.csv`
- `market_risk_gate_candidates.csv`
- `external_close_1x2.csv`
- `clv_reference_join.csv`
- `unmatched_external_events.csv`
- `team_alias_suggestions.csv`
- `manifest_external.json`
- `stage3.db`（在原 Stage3 DB 上追加外盘表）

## Risk Gate 输出说明

默认的 4pp 分歧、4pp 过热、至少 3 家公司等只是**研究候选阈值**，没有冻结，也不会修改当前正式评级。
后续必须用历史同刻外盘进行严格时间序列 OOS，验证 ROI / CLV / 最大回撤后才有资格进入正式 Gate。

## The Odds API 可选采集器

`external_odds_collector_theoddsapi.py` 是可选采集器，需要环境变量 `ODDS_API_KEY`。
API Key 不会写入输出文件。

先列出当前 soccer sport keys：

```bash
ODDS_API_KEY=*** python external_odds_collector_theoddsapi.py \
  --output-dir ./external_raw \
  --list-soccer-sports
```

再对所需联赛采集：

```bash
ODDS_API_KEY=*** python external_odds_collector_theoddsapi.py \
  --output-dir ./external_raw \
  --sport-key soccer_epl \
  --regions uk,eu,au \
  --markets h2h
```

历史端点如账号支持，可传 `--historical-date`。

## 本轮验收

已通过 synthetic-only 自测：

- 同一比赛两次外盘快照
- 3 家公司 1X2
- 6 组 bookmaker snapshot 去水
- 外盘共识 2 个时点
- Sporttery 16:55 快照只拿 16:50 外盘，不会误用 16:58 的未来快照
- 16:58 外盘可作为 17:00 开赛的 T-2m closing label

Synthetic 数据只用于测试代码，不进入真实 Stage3 研究样本。
