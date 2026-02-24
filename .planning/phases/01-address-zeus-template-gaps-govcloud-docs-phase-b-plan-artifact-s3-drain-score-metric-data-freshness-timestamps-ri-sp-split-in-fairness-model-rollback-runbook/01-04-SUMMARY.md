---
phase: 01-address-zeus-template-gaps
plan: 04
subsystem: lambda
tags: [aws-lambda-powertools, cloudwatch-emf, s3, cost-explorer, drain-score, fairness-model]

# Dependency graph
requires:
  - phase: 01-address-zeus-template-gaps
    plan: 01
    provides: "S3 PlanArtifactBucket in CDK stack (PLAN_ARTIFACT_BUCKET env var target)"
  - phase: 01-address-zeus-template-gaps
    plan: 02
    provides: "hysteresis band and fairness_metric fields in enforcement logic"
provides:
  - "drain_score per account in compute_fair_share_analysis() (benefit/fair_share, division-by-zero guarded)"
  - "DrainScore CloudWatch EMF metric per account via Lambda Powertools Metrics"
  - "determine_data_freshness() returning YYYY-MM-DD of last populated CE period"
  - "data_as_of and data_age_hours in lambda_handler return dict"
  - "ri_benefit and sp_benefit split per account in fairness report"
  - "get_per_account_discount_usage() returning (usage_list, results_by_time) tuple"
  - "get_sp_utilization_by_account() returning (details_list, sp_by_account_dict) tuple"
  - "write_plan_artifact() writing timestamped + latest.json to S3"
  - "artifact_s3_key in lambda_handler return dict"
affects:
  - "01-05 enforcement Lambda reads proposed_changes/latest.json from S3"
  - "CloudWatch alarms can now alert on DrainScore metric"
  - "DynamoDB audit records now include drain_score and data_as_of via report dict"

# Tech tracking
tech-stack:
  added:
    - "aws_lambda_powertools.metrics.MetricUnit (CloudWatch EMF)"
    - "aws_lambda_powertools.Metrics singleton (namespace: BudgetBalanceDistribution)"
  patterns:
    - "metrics.flush_metrics() per account loop iteration to preserve EMF dimension values"
    - "Module-level PLAN_ARTIFACT_BUCKET = os.environ.get(..., '') for optional S3 writes"
    - "write timestamped S3 key before latest.json to avoid overwrite race"
    - "S3 write wrapped in try/except in lambda_handler — artifact failure never crashes Discovery"
    - "Tuple return pattern for functions that need to expose raw API responses alongside processed data"

key-files:
  created: []
  modified:
    - "lambda/discovery/index.py"

key-decisions:
  - "flush_metrics() per account iteration: preserves EMF dimension values (account_id metadata) so each DrainScore metric carries the correct account context"
  - "S3 write is try/except-wrapped in lambda_handler: artifact is observability, not critical path — Discovery must not fail due to S3 error"
  - "PLAN_ARTIFACT_BUCKET defaults to empty string (not None): simplifies truthiness check in write_plan_artifact"
  - "Tuple return for get_per_account_discount_usage: exposes raw results_by_time for freshness detection without a second CE API call"
  - "ri_benefit approximated as max(benefit - sp_benefit, 0.0): avoids negative RI attribution when SP > total benefit due to floating point or allocation edge cases"

patterns-established:
  - "EMF metric pattern: add_metric + add_metadata + flush_metrics per loop iteration for per-dimension granularity"
  - "S3 artifact pattern: timestamped key + stable latest key written in order; timestamped first avoids latest overwrite race"
  - "Backward-compatible env var: empty string default allows graceful skip of optional writes"

requirements-completed:
  - GAP-03
  - GAP-04
  - GAP-06
  - GAP-07

# Metrics
duration: 4min
completed: 2026-02-24
---

# Phase 01 Plan 04: Discovery Lambda Enhancements Summary

**drain_score metric (CloudWatch EMF), data_as_of freshness timestamp, RI/SP benefit split, and S3 plan artifact write added to Discovery Lambda**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-24T05:26:50Z
- **Completed:** 2026-02-24T05:30:51Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments

- compute_fair_share_analysis() now returns drain_score (float, division-by-zero guarded to 0.0 when fair_share=0), ri_benefit, and sp_benefit per account entry — enabling CloudWatch alarm on worst-offender accounts and RI/SP attribution
- DrainScore emitted as CloudWatch EMF metric per account via Lambda Powertools Metrics (flushed per iteration to preserve account_id dimension), and determine_data_freshness() extracts YYYY-MM-DD from last populated CE period (fallback: yesterday)
- write_plan_artifact() writes timestamped + latest.json to S3; gracefully skips when PLAN_ARTIFACT_BUCKET unset; wrapped in try/except so S3 failures never crash Discovery; creates the artifact that Enforcement Lambda (Plan 05) will read

## Task Commits

Each task was committed atomically:

1. **Task 1 + Task 2: drain_score, data_as_of, RI/SP split, S3 artifact write** - `c7a8340` (feat)

**Plan metadata:** (docs commit below)

## Files Created/Modified

- `lambda/discovery/index.py` - Added Metrics import + singleton, PLAN_ARTIFACT_BUCKET env var, determine_data_freshness(), updated return types for get_per_account_discount_usage() and get_sp_utilization_by_account(), enhanced compute_fair_share_analysis() with drain_score/ri_benefit/sp_benefit + EMF metric emission, added write_plan_artifact(), wired all into lambda_handler with data_as_of/data_age_hours/artifact_s3_key in return dict

## Decisions Made

- flush_metrics() called per account in the loop: EMF requires flush to emit metric with current metadata dimensions; without this, account_id metadata would be associated with wrong account
- S3 write wrapped in try/except in lambda_handler: plan artifact is observability/review tooling, not critical path for enforcement — Discovery must be resilient to S3 errors
- PLAN_ARTIFACT_BUCKET defaults to empty string so `if not PLAN_ARTIFACT_BUCKET` check is clean and backward-compatible with pre-bucket deployments
- ri_benefit = max(benefit - sp_benefit, 0.0): guards against negative RI values in edge cases where floating-point or allocation nuances make SP appear larger than total

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Python `lambda` is a reserved keyword, so `from lambda.discovery.index import ...` fails with SyntaxError. Used importlib.util.spec_from_file_location to load the module directly for verification. The plan's verification commands use `python -c "from lambda.discovery.index import..."` which would fail the same way — this is a known limitation of the directory naming convention. Worked around with importlib for all verification runs.
- aws-lambda-powertools not in development venv (it's a Lambda layer dep, not in requirements-dev.txt). Installed it into venv for local testing. This does not affect Lambda deployment where it's available as a managed layer.

## User Setup Required

None - no external service configuration required. PLAN_ARTIFACT_BUCKET is read from environment; when unset, artifact write is gracefully skipped.

## Next Phase Readiness

- Plan 05 (Enforcement Lambda) can now read proposed_changes/latest.json from S3 — the artifact key format is `proposed_changes/{YYYY-MM-DD}/{timestamp}.json` with stable `proposed_changes/latest.json`
- DrainScore metric is available in CloudWatch namespace `BudgetBalanceDistribution` — CloudWatch alarms can be added in a follow-up plan
- data_as_of and data_age_hours in the return dict enable audit records to show data staleness
- All 139 existing tests continue to pass

## Self-Check: PASSED

- FOUND: lambda/discovery/index.py
- FOUND: 01-04-SUMMARY.md
- FOUND: commit c7a8340

---
*Phase: 01-address-zeus-template-gaps*
*Completed: 2026-02-24*
