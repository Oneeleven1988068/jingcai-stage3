# Jingcai Reality-First Research OS v0.1.0-alpha

## 0. 定位

这是 Jingcai Stage3 的**研究/审计控制面**，不是新的投注模型，也不会因为安装而让 Jingcai 3.5 晋级。

生产边界保持：

- 真实资金 / staking baseline：**Jingcai 3.4**
- 研究候选：**Jingcai 3.5 Alpha**
- Stage3 数据层：**3.5 Alpha Stage3 Data Layer 0.1**
- Research OS：负责证据、冻结预测、实验、失败、晋级和溯源治理

最高优先级：**Jingcai Reality-First Constitution**。

## 1. 核心架构

```text
Reality
  ↓
Raw Sources / Stage3 Collector
  ↓
Parser / Builder / External Market Bridge
  ↓
Evidence State Machine
  ↓
Probability + Market Core
  ↓
Context / Risk Gates
  ↓
Prediction Freeze Ledger  ← 开赛前不可变冻结
  ↓
Staking Decision
  ↓
Reality Settles
  ↓
Outcome Ledger
  ↓
Proper Scores / Rating Error / ROI-CLV later
  ↓
Failure Graveyard + Experiment Registry
  ↓
KEEP / DEMOTE / REJECT / PROMOTION REVIEW
```

## 2. Evidence State Machine

所有重要字段尽量明确属于哪一类：

- `OBSERVED`：直接观测/官方或明确来源发布的原始事实
- `DERIVED`：由可追溯输入通过确定性计算得到
- `MODEL_OUTPUT`：真实执行程序产生的输出
- `INFERRED`：模型/分析者推断，但不是直接观测
- `HYPOTHESIS`：待检验假设
- `INPUT_INCOMPLETE`：输入缺失
- `UNVERIFIED`：尚未可靠核验

**未经真实执行不得写 MODEL_OUTPUT。**

## 3. Prediction Freeze Ledger

正式预测必须在 kickoff 前写入 `research_prediction_freeze`。

强制约束：

- `prediction_timestamp_utc < kickoff_utc`
- H/D/A 概率若存在，必须在 [0,1] 且和为 1
- 预测 payload 生成 SHA256
- SQLite trigger 禁止 UPDATE / DELETE
- 比赛结束后只能追加 outcome，不能重写 prediction

这样赛后复盘只能面对赛前真实冻结的版本。

## 4. Experiment Registry / Falsification Contract

任何新 Gate、新特征、新概率模块在正式 OOS 前，应先注册实验：

- hypothesis
- baseline
- train / OOS window
- primary metrics
- success criteria
- failure criteria
- falsification contract
- search space
- `attempted_config_index`

后者用于记录研究者实际尝试过多少版本/阈值，避免只留下最后的赢家。

## 5. Failure Graveyard

重大错场和模型失败不删除。

每次至少记录：

- 输入是否真实
- 是否时间污染
- 哪一层把它升成高评级
- 哪个 Gate 本来可能拦截
- root cause 属于数据 / 概率 / 价格 / Gate / 阈值 / 执行哪一类
- 历史是否重复出现
- 候选修正规则是什么
- 候选规则是否仍需严格 OOS 泛化验证

原则：**赛后解释不是升级证据。**

## 6. Promotion Governance

组件状态：

- `SHADOW`
- `PROMOTION_REVIEW`
- `PRODUCTION`
- `REJECTED`
- `RETIRED`

Research OS 对 `PRODUCTION` 有硬保护：命令输入中必须显式提供 `explicit_human_approval=true`。

安装 Research OS 本身不会晋级任何 3.5 组件。

## 7. Baseline Ladder

复杂候选原则上逐级比较：

1. Sporttery 去水市场 baseline
2. 简单 external-market benchmark
3. Jingcai 3.4 production baseline
4. 当前 3.5 Alpha probability core
5. candidate module/model

复杂度不是优点。新增自由度必须用新的 OOS 证据支付成本。

## 8. 与现有 Stage3 数据库集成

可直接对现有 `stage3.db` 执行 `init`。脚本只新增 `research_*` 表，不改写原 `capture_run / match_dim / sporttery_snapshot / external_* / result` 表。

### 初始化

```bash
python jingcai_research_os_v0_1.py init --db data/stage3.db
```

### 自检

```bash
python jingcai_research_os_v0_1.py self-test
```

必须返回：

```json
{"self_test":"PASS"}
```

### 注册证据

```bash
python jingcai_research_os_v0_1.py record-evidence \
  --db data/stage3.db \
  --input examples/evidence.example.json
```

### 冻结赛前预测

```bash
python jingcai_research_os_v0_1.py freeze-prediction \
  --db data/stage3.db \
  --input examples/prediction_freeze.example.json
```

### 追加赛果

```bash
python jingcai_research_os_v0_1.py append-outcome \
  --db data/stage3.db \
  --input examples/outcome.example.json
```

### 注册实验

```bash
python jingcai_research_os_v0_1.py register-experiment \
  --db data/stage3.db \
  --input examples/experiment.example.json
```

### 记录失败

```bash
python jingcai_research_os_v0_1.py record-failure \
  --db data/stage3.db \
  --input examples/failure.example.json
```

### 基础概率评分

```bash
python jingcai_research_os_v0_1.py score \
  --db data/stage3.db \
  --model-version "Jingcai 3.4"
```

当前 v0.1 计算：

- 3-way Brier
- LogLoss
- HAD hit rate
- S hit/error rate
- A1 hit/error rate

RPS、ECE、ROI、CLV、drawdown、time-uniform confidence sequence 将在后续版本接入真实历史 ledger 后实现，不在 v0.1 中伪造“已完成”。

### 审计

```bash
python jingcai_research_os_v0_1.py audit --db data/stage3.db
```

检查 research 表数量、预测完整性、是否存在 kickoff 后冻结等问题。

## 9. Provenance Graph

`research_provenance_edge` 用于建立：

```text
raw_sha256
  → sporttery_snapshot / external_snapshot
  → evidence
  → prediction_id
  → staking decision
  → outcome
  → metric snapshot / failure / experiment decision
```

v0.1 提供底层 edge ledger；自动化图构建可在下一版接入 Parser/Builder manifest。

## 10. 当前明确未完成

Reality-First 要求未完成的内容必须直说：

- 尚未把所有现有 3.4/3.5 预测代码自动包进 freeze ledger
- 尚未把 Standings/Fatigue/Motivation 等上下文采集器自动写入 evidence ledger
- 尚未实现 time-uniform confidence sequence
- 尚未实现 PBO / Deflated Sharpe 等多重尝试惩罚
- 尚未实现完整 ROI/CLV/Max Drawdown 执行账本
- 尚未冻结 S/A1/A2 定量晋级阈值

这些都是下一阶段任务，不得标成已完成。

## 11. 版本状态

`RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED`

这意味着研究治理基础设施已激活，但不会改变 Jingcai 3.4 的真实资金生产地位。
