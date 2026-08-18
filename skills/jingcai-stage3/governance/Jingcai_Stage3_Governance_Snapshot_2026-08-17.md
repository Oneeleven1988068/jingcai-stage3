# Jingcai Stage3 Unified Governance Snapshot

**Snapshot date:** 2026-08-17  
**Timezone:** Asia/Singapore  
**Purpose:** 作为 Jingcai 3.4 / 3.5 Alpha / Stage3 后续开发、实时分析、回测、复盘和代码实现的统一起点。若旧聊天、旧脚本与本快照冲突，以本快照及其后用户明确确认的更新为准。

## 1. 版本与生产边界

- **Jingcai 3.4** = 当前真实资金 / staking production baseline。
- **Jingcai 3.5 Alpha** = research/shadow 候选，不得静默覆盖 3.4。
- 当前数据层：**3.5 Alpha Stage3 Data Layer 0.1**
- 治理状态：`DATA_LAYER_ACTIVE__MODEL_PROMOTION_NOT_GRANTED`
- Case1/2/3：仅诊断/历史研究，不进入生产决策链。
- 新 S/A1/A2 完整量化阈值：尚未冻结。
- HAD Strong Confidence 不得冒充 CRS 比分 S。

## 2. 核心决策顺序

`HAD → HHAD → TTG → CRS → HAFU → Context/Risk Gates → Final Rating/Staking Permission`

比分 S 必须通过 Score-Market Confirmation Gate。

## 3. 评级与投注权限

- **S**：最高确认/主动进攻级，可考虑比分投注。
- **A1**：核心串关主体。
- **A2**：方向可投但风险明显，只适合保护、单场或复式，不作核心胆。
- **B**：观察不下注。
- **C**：排除。

真实投注坚持成本控制、稳健、细水长流。

## 4. Stage3 数据链

`Stage3 Collector → Parser/Builder → SQLite append-only/audit → exports → External Market Bridge → context layers`

体彩尽量同一源/同批/同时间快照保存：
- HAD
- HHAD
- CRS
- TTG
- HAFU

保存 raw JSON、canonical JSON、capture UTC/local、pool presence/count、SHA256、标准化 quote rows。
赔率不变也保留新时间快照，用于时间序列、anchor/close、CLV。
缺失关键验证信息时必须标记 `UNVERIFIED`。

## 5. External Market Bridge

职责：**audit / divergence / overheat / dispersion gate**，不是直接替代 3.4 概率核心。

支持：
- H2H / 1X2
- spreads
- totals

共识审计参考：
- de-juiced probabilities
- median probability
- MAD
- bookmaker count
- divergence 默认 4.0pp
- overheat 默认 4.0pp
- 至少 3 家 bookmaker
- External Market audit gate = `MANDATORY_LIVE`

以下仍未进入生产权重：
- External positive probability fusion
- Shin
- Residual ML
- dynamic team strength/xG
- multi-score distribution as formal HAD weight

## 6. 市场结构治理

- `Three-Way Directional Reversal Gate`
- `Favorite Crowding / Popular-Side Distortion Gate`

原则：
- 低赔/热门身份只是市场价格结果，不等于稳胆。
- 必须区分 Win Probability 与 Betting Value。
- 没有真实投注量/成交量/资金分布时，不得声称“80%资金在热门”等未观测事实。

## 7. League Context / Standings Layer

定位：**mandatory context/audit + confidence/risk gate**，不机械按排名直接加概率。

标准字段：
- rank / points / played / W-D-L
- GF / GA / GD
- rank_gap / points_gap / ppg_gap
- gf_pg_gap / ga_pg_gap / gd_pg_gap
- 主客场分项
- 近期表现相对赛季基线

状态：
- `STANDINGS_STRONG_CONFIRM`
- `STANDINGS_SOFT_CONFIRM`
- `STANDINGS_NEUTRAL`
- `STANDINGS_DIRECTIONAL_CONFLICT`
- `STANDINGS_UNVERIFIED`

专项警报：
- `RANK_ADVANTAGE_COMPRESSION`
- `LOW_TABLE_FAVORITE_WARNING`

历史回测严禁用赛后排名/积分倒灌。

## 8. Home/Away Split + Recent Form

赛季总榜、主场表现、客场表现、近期状态必须拆成不同时间尺度。
近期状态不能只看最近 5 场 W/D/L，还要结合对手强度、进失球和赛程环境。

## 9. Schedule Congestion / Travel Fatigue Layer

定位：**mandatory context/audit**。

尽量检查：
- rest_days
- matches_last_7d / 14d / 21d
- away_streak
- cross_country_trips_14d
- travel_km_14d
- timezone_shift
- extra_time_recent
- 刚踢欧战/洲际/杯赛
- 连续 3/4 天一赛
- 连续跨城/跨国转场
- 回国恢复天数
- 下一场重要赛事造成的 rotation_risk

输出：
- fixture_congestion_score
- travel_fatigue_score
- rotation_risk

无法可靠核验：`FATIGUE_UNVERIFIED`

## 10. Match Motivation / Competitive Stakes Layer

定位：**mandatory context/audit**。

每场尽量核验：
- must_win_home / away
- draw_sufficient_home / away
- must_not_lose_home / away
- points_needed_home / away
- title_race_pressure
- qualification_pressure
- relegation_pressure
- dead_rubber_home / away
- goal_difference_pressure
- next_match_priority
- motivation_score_home / away
- motivation_asymmetry
- competitive_stakes_state

必须由真实赛制、积分形势、剩余赛程支撑，不能凭主观“战意故事”。
无法可靠核验：`MOTIVATION_UNVERIFIED`

## 11. 多层联动总原则

先由概率/市场层独立形成方向，再由：
- Standings
- Home/Away
- Recent Form
- Fatigue/Travel
- Motivation
- Favorite Crowding
- Directional Reversal
- External Market divergence/overheat

执行 confirmation / conflict / risk gating。

**任何单一上下文变量都不得机械决定方向。多层冲突时，优先降级保护资金，而不是强行给胆。**

## 12. 回测治理

所有新增 Gate/Layer 先进入 3.5 Alpha shadow / OOS / Walk-forward 验证。

严禁 look-ahead / data leakage，只能使用开赛前可获得数据。

重点指标：
- overall accuracy
- Brier
- LogLoss
- RPS
- ECE
- S error rate
- A1 error rate
- high-confidence error rate
- favorite upset rate
- overheat downgrade hit rate
- directional/standings conflict loss control
- ROI/CLV（数据成熟后）
- 不同联赛/赛季阶段稳定性

Stage2 已有 OOS 小幅改善，不代表新增 Gate 已晋级。

## 13. 默认输出

聊天默认字段：
- 比赛编号
- 开赛时间
- 对阵
- HAD
- HHAD
- CRS
- TTG
- HAFU
- 风险等级
- 翻车概率 / 过热警报
- 投注建议

最终额外单列：
- S
- A1 / A2

完整解析后默认偏好：额外提供可下载横屏 Excel 打印版。

## 14. 数据上传约定

以后新的体彩 JSON 尽量统一在 **Jingcai Stage3 项目聊天**中上传，并作为 Collector / Parser / Builder / 实时赔率采集的优先输入来源。

## 15. 公开内容

XHS / 公开发布内容不得暴露模型内部细节。
