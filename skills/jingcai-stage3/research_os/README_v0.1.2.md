# Jingcai Reality-First Research OS v0.1.2-alpha

## 0. 定位与生产边界

这是 Jingcai Stage3 之上的研究/审计控制面补丁。v0.1.2 的主题是 **Full Distribution / Tail-Aware CRS**，用于修复研究层中过早的 `TTG Top-N -> CRS` 概率截断问题。

生产边界保持不变：

- 真实资金 / staking baseline：**Jingcai 3.4**
- 研究候选：**Jingcai 3.5 Alpha**
- Research OS 状态：`RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED`
- v0.1.2：**shadow/research only**
- 安装或运行本补丁不会修改 3.4 生产概率、评级或 staking 权限。

最高优先级仍是 **Jingcai Reality-First Constitution**：模型必须服从现实，而不是让现实迁就模型。

## 1. v0.1.2 已实现

1. 新 shadow 模块：`shadow_modules/full_distribution_tail_aware_shadow_v0_2.py`。
2. 在新 shadow 链中禁止比分分布完成前的 `TTG Top-N -> CRS candidate space` 早期截断。
3. 完整保留 TTG 0–7+ 去水分布，输出：mode、0–1/2–3/4–5/6+ 概率质量、4+/5+/6+/7+ tail mass、normalized entropy、effective support。
4. 完整解析 CRS exact-score 市场以及 `OTHER_H / OTHER_D / OTHER_A`，并把 exact score 表示为 `(result, margin, total, score)` 联合状态。
5. 高比分状态允许属于 H / D / A 任一方向；严禁“高比分只属于平局”的假设。
6. CRS other bucket 只有结果方向、没有精确 total/margin；因此 exact tail mass 在 other_mass>0 时只作为 lower bound，不伪造分解。
7. 完成 Sporttery HAD↔HHAD ±1 对称框架：
   - 主 -1：`HAD_H ~ HHAD_H + HHAD_D`，主胜分支拆“赢2+ / 赢1”。
   - 主 +1：`HAD_A ~ HHAD_D + HHAD_A`，客胜分支拆“赢1 / 赢2+”。
   - Cross-market consistency 始终可审计；净胜球预测只有 HAD 方向 Gate 成立后才可使用。
8. 禁止把 HAD / HHAD / TTG / CRS 当作独立概率直接相乘，避免重复计数。
9. 新增赛中结构冲击归因政策：真实赛果始终进入 OOS 评分；红牌等核验后的结构冲击只能影响因果归因，不能删除失败样本或回填赛前权重。
10. 暂不冻结 entropy、tail mass、candidate-count、HAD方向强度等阈值。

## 2. 已注册 shadow 实验

- `EXP-001-HHAD-MARGIN-DECOMPOSITION`
- `EXP-002-TTG-TAIL-MASS`
- `EXP-003-REVERSAL-PERSISTENCE`
- `EXP-004-MARKET-TIME-ALIGNMENT`
- `EXP-005-FULL-DISTRIBUTION-PRESERVATION`
- `EXP-006-TAIL-AWARE-CRS-STATE-SPACE`

实验定义位于 `experiments/`。

## 3. 自检

```bash
python shadow_modules/full_distribution_tail_aware_shadow_v0_2.py self-test
```

预期：

```json
{
  "self_test": "PASS",
  "version": "Full Distribution / Tail-Aware CRS Shadow v0.2",
  "no_early_truncation": true,
  "symmetric_plus_minus_one": true,
  "crs_full_state": true,
  "production_promotion": false
}
```

## 4. 分析五池原始快照

```bash
python shadow_modules/full_distribution_tail_aware_shadow_v0_2.py analyze \
  --input sporttery_5pools.json \
  --match 周一001 \
  --output output.json
```

输出会保留完整 TTG 分布与完整 CRS 状态，不在 TTG 中间层压缩成 Top-N。

## 5. 2026-08-16 17:05 小样本 falsification adapter

```bash
python shadow_modules/full_distribution_tail_aware_shadow_v0_2.py backtest \
  --input backtests/frozen_20260816_1705_summary.csv \
  --output backtests/frozen_20260816_1705_tail_falsification.json
```

这不是生产版 3.4 的完整正式回测，而是使用当时赛前冻结摘要做的 **信息损失 falsification**。当前 10 场结果：

- 旧 TTG Top3 命中：6/10 = 60%
- 实际 5+ 球：3 场；旧 TTG Top3 对 5+ 命中：0/3
- 旧 CRS Top3 exact 命中：1/10 = 10%
- frozen λ 只作为信息损失诊断时，旧 TTG Top3 平均遗漏约 37.15% 的 Poisson 总进球概率质量
- 但 λ-tail 单独预测 5+ 的 AUC≈0.476，且 Brier/LogLoss 不优于该小样本的简单发生率基准，因此 **λ tail alone is NOT the solution**。

结论：支持“不要过早删除概率质量”，但不支持“机械提高高比分权重”。Tail-aware 候选的真正预测价值仍必须通过更大的、严格赛前冻结 OOS 数据验证。

## 6. 001 复现检查

对 2026-08-17 周一001的 21:31 五池快照，v0.1.2 shadow 可复现：

- TTG 4+ ≈ 40.82%
- TTG 5+ ≈ 22.37%
- CRS exact 4+ tail lower bound ≈ 38.89%
- CRS exact 5+ tail lower bound ≈ 19.70%
- 3:2 exact 去水概率 ≈ 3.39%，在完整状态空间中保留，不会在 TTG 阶段提前删除。

## 7. 核心修复原则

> **压缩必须发生在最后，而不是 TTG 中间层。**

这是一项表示/治理层修复，不等于新方案已经证明可以提高投注命中或 ROI。任何生产晋级仍需严格 walk-forward/OOS，并要求显式人工批准。

## 8. 历史文件

- `README_v0.1.0_legacy.md`：v0.1.0 原始说明
- `STATUS_v0.1.0_legacy.json`：v0.1.0 状态
- `STATUS_v0.1.1_legacy.json`：v0.1.1 patch 状态
- `PATCH_NOTES_v0.1.1.md`：001 patch 历史记录
- `PATCH_NOTES_v0.1.2.md`：本次 patch notes

## 9. 版本标识说明

`jingcai_research_os_v0_1.py` 是 v0.1.0 建立的基础控制面引擎，因此它自己的内置 audit 仍会报告 `0.1.0-alpha`。v0.1.2 是在该稳定控制面之上的 **shadow patch/package version**，新增 Full Distribution / Tail-Aware CRS 模块与实验治理；没有伪装成重写基础引擎。
