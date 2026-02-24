# Roadmap: Budget Balanced Distribution

## Overview

This roadmap transforms the existing zeus-discovery.py read-only monitoring script into a production-grade automated enforcement system. Starting with CDK infrastructure and DynamoDB configuration tables, we migrate discovery logic to Lambda with EventBridge scheduling, then add enforcement capabilities with dry-run safety gates, comprehensive audit logging, monitoring dashboards, and finally enable production enforcement with automatic re-enablement for compliant accounts.

## Milestones

- ✅ **v1.0 MVP** — Phases 1-7 (shipped 2026-02-12)

## Phases

<details>
<summary>✅ v1.0 MVP (Phases 1-7) — SHIPPED 2026-02-12</summary>

- [x] Phase 1: Infrastructure Foundation (1/1 plans) — completed 2026-02-11
- [x] Phase 2: Discovery Lambda (2/2 plans) — completed 2026-02-12
- [x] Phase 3: Configuration Schema (3/3 plans) — completed 2026-02-12
- [x] Phase 4: Enforcement Engine (Dry-Run) (2/2 plans) — completed 2026-02-12
- [x] Phase 5: Audit & Compliance (2/2 plans) — completed 2026-02-12
- [x] Phase 6: Monitoring & Dashboards (1/1 plan) — completed 2026-02-12
- [x] Phase 7: Production Enforcement (1/1 plan) — completed 2026-02-12

**Delivered:**
- Complete CDK infrastructure with protected DynamoDB tables and IAM roles
- Production Lambda functions (discovery + enforcement) with Powertools integration
- CLI tool for FinOps configuration management
- Comprehensive audit trail (CloudWatch + DynamoDB)
- CloudWatch monitoring dashboard with SNS alerts
- Production-ready enforcement (disabled by default for safety)

See: [.planning/milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md) for full details

</details>

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Infrastructure Foundation | v1.0 | 1/1 | ✓ Complete | 2026-02-11 |
| 2. Discovery Lambda | v1.0 | 2/2 | ✓ Complete | 2026-02-12 |
| 3. Configuration Schema | v1.0 | 3/3 | ✓ Complete | 2026-02-12 |
| 4. Enforcement Engine (Dry-Run) | v1.0 | 2/2 | ✓ Complete | 2026-02-12 |
| 5. Audit & Compliance | v1.0 | 2/2 | ✓ Complete | 2026-02-12 |
| 6. Monitoring & Dashboards | v1.0 | 1/1 | ✓ Complete | 2026-02-12 |
| 7. Production Enforcement | v1.0 | 1/1 | ✓ Complete | 2026-02-12 |

### Phase 1: Address Zeus template gaps — GovCloud docs, Phase B plan artifact (S3), drain_score metric, data freshness timestamps, RI/SP split in fairness model, rollback runbook

**Goal:** Close 7 identified gaps between v1.0 MVP and Zeus template requirements: GovCloud architecture documentation, S3 human-reviewable plan artifact, drain_score metric, data freshness timestamps, RI/SP split config option, re-enablement hysteresis, and rollback runbook.
**Depends on:** Phase 0 (v1.0 MVP shipped)
**Plans:** 5 plans

Plans:
- [x] 01-01-PLAN.md — CDK S3 bucket, Lambda timeout 10min, configurable EventBridge schedule, updated tests — completed 2026-02-24
- [x] 01-02-PLAN.md — ThresholdConfig hysteresis (re_enable_threshold_pct) and fairness_metric fields, enforcement logic update — completed 2026-02-24
- [x] 01-03-PLAN.md — GovCloud architecture doc, enforcement rollback runbook, restore-risp-state.py script, README fixes — completed 2026-02-24
- [x] 01-04-PLAN.md — Discovery Lambda: drain_score, data_as_of, RI/SP split, S3 artifact write — completed 2026-02-24
- [x] 01-05-PLAN.md — Enforcement Lambda: S3 artifact read with fallback, data_as_of in audit records — completed 2026-02-24

---

## Next Steps

Start next milestone: `/gsd:new-milestone`
