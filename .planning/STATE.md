# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-12)

**Core value:** Accounts stay within their allocated discount budget through automated enforcement—no manual intervention required once thresholds are set.
**Current focus:** Phase 01 (Address Zeus Template Gaps) — executing plans

## Current Position

Milestone: v2.0 Zeus Template Gaps
Status: In progress — Phase 01 Plan 05 complete
Last activity: 2026-02-24 — Completed Phase 01 Plan 05 (S3 plan artifact read with fallback in Enforcement Lambda, data_as_of in audit records)

Progress: 12 plans complete (v1.0) + 5 plans complete (v2.0 = 17 total)

## Performance Metrics

**Velocity:**
- Total plans completed: 17
- Average duration: 8.7 minutes
- Total execution time: 2.51 hours

**By Phase (v1.0):**

| Phase | Plans | Total Time | Avg/Plan |
|-------|-------|------------|----------|
| 01 | 1 | 2.9 min | 2.9 min |
| 02 | 2 | 9.3 min | 4.7 min |
| 03 | 3 | 19.9 min | 6.6 min |
| 04 | 2 | 21.1 min | 10.6 min |
| 05 | 2 | 15.8 min | 7.9 min |
| 06 | 1 | 32.4 min | 32.4 min |
| 07 | 1 | 25.2 min | 25.2 min |

**By Phase (v2.0):**

| Phase | Plans | Total Time | Avg/Plan |
|-------|-------|------------|----------|
| 01 (zeus gaps) | 5 | 13.4 min | 2.7 min |

**Recent Plans:**
- Phase 01 (zeus P05): 202 sec, 2 tasks, 2 files
- Phase 01 (zeus P04): (see P04 SUMMARY)
- Phase 01 (zeus P03): 199 sec, 2 tasks, 4 files
- Phase 01 (zeus P02): 117 sec, 2 tasks, 4 files
- Phase 01 (zeus P01): 317 sec, 2 tasks, 3 files
- Phase 07 P01: 1512 sec, 2 tasks, 2 files
- Phase 06 P01: 1942 sec, 2 tasks, 2 files
- Phase 05 P02: 506 sec, 2 tasks, 2 files
- Phase 05 P01: 446 sec, 2 tasks, 4 files

## Accumulated Context

### Decisions

All decisions from v1.0 are now logged in PROJECT.md Key Decisions table with outcomes.

**v1.0 MVP Summary (7 phases, 12 plans):**
- Built complete automated enforcement system with CDK infrastructure
- Lambda Powertools integration for discovery and enforcement functions
- CLI tool for FinOps configuration management with dry-run safety
- Comprehensive audit trail (CloudWatch + DynamoDB with PK/SK pattern)
- CloudWatch monitoring with 8-section dashboard and 10 alarms
- Production-ready with safety gates (disabled by default, requires manual enablement)

**v2.0 Zeus Gap Fixes (in progress):**
- Phase 01 Plan 01: S3 plan artifact bucket added to CDK stack, Lambda timeouts bumped to 10min, configurable EventBridge discovery schedule, MockPythonFunction conftest for Docker-free tests
- Phase 01 Plan 02: ThresholdConfig gains re_enable_threshold_pct (hysteresis band) and fairness_metric (RI/SP split control); determine_enforcement_actions() uses hysteresis band to prevent account oscillation
- Phase 01 Plan 03: GovCloud cross-partition billing model documented, 5-step enforcement rollback runbook, restore-risp-state.py CLI (DynamoDB snapshot → Cost Category restore), README Phase A/B boundary with 6-step checklist
- Phase 01 Plan 04: Discovery Lambda writes proposed_changes/latest.json S3 artifact with drain_score, data_as_of, RI/SP split; write_plan_artifact() function; data freshness determination
- Phase 01 Plan 05: Enforcement Lambda reads S3 plan artifact with 26h staleness gate (fallback to direct CE); data_as_of + data_age_hours in all DynamoDB audit records; artifact_source in Lambda response

**Key Technical Patterns:**
- Single-table DynamoDB design with entity-type prefixes (ACCOUNT#, GROUP#, THRESHOLD#)
- Dry-run by default everywhere (CLI --execute flag, Lambda event.execute override)
- Dual audit logging (CloudWatch for debugging, DynamoDB for compliance queries)
- Least-privilege IAM with grant methods
- RemovalPolicy.RETAIN for stateful resources
- CDK context-driven configuration: `try_get_context()` with Python `or` string fallback
- MockPythonFunction in conftest.py: session-scoped autouse patch for Docker-free CDK tests

### Decisions (v2.0)

- **CDK auto-generated S3 bucket name:** No explicit bucket_name on PlanArtifactBucket to avoid BucketAlreadyExists on re-deploy
- **MockPythonFunction conftest:** Replaces PythonFunction with inline-code Function for CDK assertion tests without Docker
- **Duration alarm at 85%:** 510000ms threshold (85% of 600s) — slightly more headroom than previous 80%
- **re_enable_threshold_pct defaults to None:** None signals "use disable_threshold as re-enable threshold" — avoids extra DynamoDB bytes for common case with no hysteresis
- **fairness_metric omitted from DynamoDB when "combined":** Reduces storage; backward-compatible reads use .get() with "combined" default
- **account_re_enable_thresholds as independent Optional dict:** Cleaner than extending account_thresholds with compound values; explicit intent, backward-compatible
- **Standalone boto3 restore script (no CDK deps):** Operators running rollback during incident may not have CDK venv active; script uses only requirements-cli.txt dependencies
- **Phase A/B boundary as docs-only (no CDK conditional):** CDK conditional would delete deployed Enforcement Lambda on existing stacks — documentation approach provides same guidance without infrastructure risk
- **MAX_ARTIFACT_AGE_HOURS=26:** 26h window accommodates scheduling drift (Discovery 2:00 AM, Enforcement 2:30 AM) plus 24h gap between runs
- **artifact_source in response dict:** "s3" vs "ce_direct" enables CloudWatch metrics/alarms distinguishing artifact-driven from fallback enforcement runs
- **data_as_of always written to DynamoDB (even as empty string):** consistent schema, avoids conditional KeyError on reads
- **data_age_hours computed at write time:** cheaper reads, no recomputation risk on query

### Pending Todos

1. **Validate RISP Group Sharing actually prevents discount application** (testing)
   - Empirically test if Cost Category exclusion stops actual discount application vs just reporting
   - TAM questioned fundamental assumption of enforcement mechanism
   - See: `.planning/todos/pending/2026-02-13-validate-risp-group-sharing-actually-prevents-discount-application.md`

### Roadmap Evolution

- Phase 8 added: Address Zeus template gaps — GovCloud docs, Phase B plan artifact (S3), drain_score metric, data freshness timestamps, RI/SP split in fairness model, rollback runbook

### Blockers/Concerns

None.

## Session Continuity

Last session: 2026-02-24
Stopped at: Completed Phase 01 Plan 05 (zeus gaps) — S3 artifact read in Enforcement Lambda + data_as_of audit records
Next action: Execute Phase 01 Plan 06
