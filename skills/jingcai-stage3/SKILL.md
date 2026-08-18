# Jingcai Stage3 Canonical Skill v1.0.0

## Identity
This Skill is the canonical orchestration layer for the Jingcai Stage3 football research and live-analysis system.

It **does not promote Jingcai 3.5 Alpha into real-money production**. The production boundary is fixed:

- `Jingcai 3.4 = REAL_MONEY / STAKING BASELINE`
- `Jingcai 3.5 Alpha = SHADOW / RESEARCH CANDIDATE`
- `RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED`

The highest-priority rule is the **Reality-First Constitution**:

> 模型必须服从现实，而不是让现实迁就模型。  
> 不维护模型的尊严，只维护事实的尊严。

No lower-level model, rating, narrative, version number, or betting construction may override this constitution.

---

## 1. Mandatory evidence states
Every material claim must be classified as one of:

- `OBSERVED`
- `DERIVED`
- `MODEL_OUTPUT`
- `INFERRED`
- `HYPOTHESIS`
- `INPUT_INCOMPLETE`
- `UNVERIFIED`

Never silently convert missing information into observed facts.

Before using any odds, probability, injury, motivation, fatigue, market-flow, or context input, ask:
1. What is the raw fact?
2. Where did it come from?
3. Who measured/published it?
4. When was it captured?
5. Is the raw record preserved?
6. Can the calculation be reproduced?
7. Is there independent evidence?
8. How would an error be detected?

---

## 2. Hard production boundary
### Production
- Jingcai 3.4 remains the real-money/staking baseline.
- Case1/2/3 legacy interval rules are retired from production.

### Shadow / research only
- Jingcai 3.5 Alpha Stage2/Stage3 improvements.
- External positive probability fusion.
- Shin positive fusion.
- Residual ML probability correction.
- Dynamic team/xG positive HAD weight.
- Multi-score-distribution positive HAD weight.
- CRS Two-Sided Presentation.
- Hybrid CRS Satellite construction.
- New S/A1/A2 quantitative thresholds until frozen by explicit OOS promotion.

No silent promotion is permitted.

---

## 3. Mandatory live execution order
For live Sporttery analysis execute in this order:

1. `SOURCE_INTEGRITY_GATE`
2. `SPORTTERY_SALES_CUTOFF_GATE`
3. Parse / join official snapshots by official numeric `matchId`
4. HAD de-vig and directional state
5. HHAD event-space decomposition
6. TTG **full 0–7+ distribution**
7. CRS **full exact-score + OTHER_H / OTHER_D / OTHER_A distribution**
8. HAFU path distribution
9. Time-alignment / stale-pool audit
10. ExternalOdds target-event coverage audit
11. Browser/web fallback if ExternalOdds target coverage is incomplete
12. External market divergence / overheat / dispersion audit
13. Context gates: standings, form, fatigue, motivation, injuries, venue, competition state
14. Three-Way Directional Reversal Gate
15. Favorite Crowding / Popular-Side Distortion Gate
16. Final Compression / Bet Construction Gate
17. Rating: S / A1 / A2 / B / C
18. `SPORTTERY_SALES_CUTOFF_GATE` re-check before staking permission
19. Prediction Freeze Ledger

Do not skip a gate because a conclusion already “looks clear.”

---

## 4. Sales cutoff
For China Sporttery football:
- Monday–Friday: **22:00**
- Saturday–Sunday: **23:00**

`未开赛` does not mean `仍可投注`.

After cutoff, snapshots may only be used for:
- `POST_SALE_RESEARCH`
- CLV / closing-line analysis
- audit
- backtest

Never rewrite a frozen pre-sale prediction using post-sale or in-play information.

---

## 5. HAD / HHAD event semantics
### Home -1
Latent states:
- `H2P`: home wins by 2+
- `H1`: home wins by exactly 1
- `D`: draw
- `A1`: away wins by exactly 1
- `A2P`: away wins by 2+

Ordinary HAD:
- H = H2P + H1
- D = D
- A = A1 + A2P

Home -1 HHAD:
- H = H2P
- D = H1
- A = D + A1 + A2P

Therefore:
- `HHAD(-1) H + D ≈ HAD H`
- `HHAD(-1) A ≈ HAD D + HAD A`

**Home -1 HHAD H+D is ordinary HAD H, not draw protection.**

### Home +1
- HHAD H = H2P + H1 + D
- HHAD D = A1
- HHAD A = A2P

Therefore:
- `HHAD(+1) D + A ≈ HAD A`
- `HHAD(+1) H ≈ HAD H + HAD D`

**Home +1 HHAD D+A is ordinary HAD A, not draw protection.**

Never mix Sporttery HHAD 3-way semantics with global 2-way Asian handicap semantics.

---

## 6. TTG full-distribution rule
**Compression must happen at the end, never in the TTG middle layer.**

Mandatory TTG outputs:
- 0–7+ de-vigged distribution
- mode
- 0–1 / 2–3 / 4–5 / 6+ bands
- tail mass 4+ / 5+ / 6+ / 7+
- normalized entropy
- effective support
- retained probability mass = 1.0
- `truncation = NONE`

Forbidden:
- `TTG Top-N -> CRS candidate space` early truncation.

---

## 7. CRS state space and presentation
Represent exact score as joint state:
`(result, margin, total, score)`.

Preserve:
- every exact score available from source
- `OTHER_H`
- `OTHER_D`
- `OTHER_A`

Do not assume high-score tail equals draw tail.
Do not fabricate exact total or margin inside OTHER buckets.
Do not multiply HAD × HHAD × TTG × CRS as if independent.

Presentation should include:
- center
- left tail
- right tail
- extreme right tail
- associated TTG mass
- display coverage

Separate axes:
- ordinary direction rating
- `CRS_SINGLE_OPPORTUNITY = NO / CENTER / TAIL`

A score opportunity does **not** require S rating.

---

## 8. Hybrid CRS Satellite governance
Shadow/research only unless explicitly promoted.

Role separation:
- CRS satellite = high-price score opportunity
- A1/A2 anchors = directional protection legs

Rules:
- Never promote B/C merely to fill a hybrid ticket.
- Mixed-pool compatibility must be observed in actual Sporttery calculator/UI before claiming support.
- Multiple score selections expand combination count/cost and must be audited.
- Compare matched-cost OOS alternatives before claiming superiority.

---

## 9. External market policy
External markets are primarily for:
- audit
- divergence detection
- overheat diagnostics
- dispersion diagnostics
- time-alignment checks

They do not replace the 3.4 probability core and are not automatically fused positively.

### Mandatory target coverage gate
For every intended live target match:
- target count
- matched external event count
- unmatched target list
- event-link confidence
- bookmaker count
- freshness

If any target event is missing:
`EXTERNAL_COVERAGE_GATE = FAIL`

Then automatically perform **browser/web fallback** for the missing target events. Do not stop at `INPUT_INCOMPLETE` if browser access is available.

However, browser reports / previews are not equivalent to reproducible Sharp multi-book price consensus. Label them separately:
- `EXTERNAL_PRICE_CONFIRM`
- `EXTERNAL_CONTEXT_CONFIRM`

Never use an unrelated same-team event as a substitute match.

---

## 10. External market deduplication
When external odds exist:
- preserve RAW quotes
- deduplicate same-family books
- maintain SHARP subset
- isolate different handicap/total lines
- audit freshness

Known family caution examples:
- Unibet regional variants: same family
- Betsson / NordicBet: same family

Preferred SHARP examples when available:
- Pinnacle
- Betfair Exchange
- Matchbook

Never invent transaction volume, betting money, public percentages, or crowding data.

---

## 11. Context gates
### Fatigue
Track when observed:
- rest days
- matches in 7/14/21 days
- away streak
- travel
- timezone shift
- extra time
- congestion
- rotation

Missing => `FATIGUE_UNVERIFIED`.

### Motivation
Use only observed competition-state facts such as:
- must-win
- draw sufficient
- already qualified
- rotation incentives

Missing => `MOTIVATION_UNVERIFIED`.

“Strong motivation = must win” is forbidden.

### Favorite crowding
Low odds only mean market favorite pricing.
They do not automatically mean:
- safe banker
- positive expected value
- public money concentration

Without real volume/funding distribution, crowding is a hypothesis only.

---

## 12. Rating semantics
- **S**: highest-confirmation opportunity. Not granted by low odds alone.
- **A1**: core accumulator anchor.
- **A2**: direction playable with meaningful risk; protection/single/combination only, not core banker.
- **B**: observe; no bet.
- **C**: exclude.

Default staking scope: only S/A1/A2.

No new numeric S/A1/A2 thresholds may be treated as frozen until explicit OOS promotion.

---

## 13. Prediction freeze and postmortem
Before sale cutoff, freeze:
- timestamp
- source timestamps
- evidence state
- HAD/HHAD/TTG/CRS/HAFU outputs
- external coverage status
- context status
- rating
- permitted bet construction

After result:
- compare frozen forecast vs observed result
- investigate S/A1 misses first
- separate input failure / mapping failure / gate failure / compression failure / model failure / shock
- in-play red cards or shocks may explain outcome context, but may not retroactively rewrite pre-match weights
- every rejected experiment or failure leaves an auditable record

---

## 14. Default live output
Use concise Chinese output. Default table fields:
- 比赛编号
- 开赛时间
- 对阵
- 胜平负（H/D/A）
- 让球胜平负（HHAD）
- 比分（CRS）
- 总进球（TTG）
- 半全场（HAFU）
- 风险等级（S/A1/A2/B/C）
- 翻车概率/过热警报
- 投注建议（单选/双选/混合）

At the end separately list:
- S
- A1
- A2

Do not expose internal model mechanics in public/XHS output.

---

## 15. R9 optional mode
R9 is a separate consumer mode, not evidence that a match is production-grade in the 5-pool model.

Principles:
- choose 9 from 14 by **deleting bad matches**, not by force-fitting every match
- do not promote a B/C match just to fill nine slots
- calculate combination expansion exactly
- prioritize cost control
- distinguish screenshot R9 numbering from Sporttery weekday match numbering

---

## 16. Component registry
Canonical code and research assets live in:
- `core/` — Stage3 data layer, 3.4 baseline code, 3.5 Alpha code, parser/builder, external market bridge/collector
- `research_os/` — Reality-First Research OS through v0.1.5, including v0.1.1–v0.1.5 inherited experiments/modules
- `governance/` — frozen governance snapshots
- `runtime/` — Skill-level gates/orchestration helpers
- `policies/` — canonical policies that override lower-level convenience behavior

If a lower-level file conflicts with this SKILL.md, this SKILL.md and the Reality-First Constitution take precedence.
