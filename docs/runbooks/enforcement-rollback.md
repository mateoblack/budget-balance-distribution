# RISP Enforcement Rollback Runbook

## When to Use This Runbook

Use this runbook if the Enforcement Lambda incorrectly disabled RISP sharing for accounts that should not have been affected. Signs of a misfire:
- SNS alert received for unexpected accounts
- CloudWatch dashboard shows unexpected compliance status changes
- FinOps team reports discount loss for accounts that were under threshold

**Time to restore: < 15 minutes for console option, < 5 minutes for CLI option.**

---

## Step 1 — Identify the Misfire (< 5 min)

1. Open CloudWatch Logs: `/aws/lambda/[EnforcementLambdaName]`
2. Find the enforcement run timestamp (look for "Enforcement Lambda starting" log entry)
3. Note the `disabled_accounts` list from the log entry
4. Note the `execution_result` field — should be "SUCCESS" for a completed run

To find the Lambda function name:
```bash
aws cloudformation describe-stacks \
  --stack-name BudgetBalanceDistributionStack \
  --query 'Stacks[0].Outputs[?contains(OutputKey, `Enforcement`)].OutputValue' \
  --output text
```

---

## Step 2 — Get Previous State (< 2 min)

**Option A: From DynamoDB audit table**
```bash
aws dynamodb query \
  --table-name [AUDIT_TABLE_NAME] \
  --key-condition-expression "PK = :pk" \
  --expression-attribute-values '{":pk": {"S": "ENFORCEMENT_ACTION"}}' \
  --scan-index-forward false \
  --limit 5
```
Find the `previous_state.enabled` field in the most recent record — this is the account list before enforcement ran.

**Option B: From S3 plan artifact**
```bash
aws s3 cp s3://[PLAN_ARTIFACT_BUCKET_NAME]/proposed_changes/latest.json - | python3 -m json.tool
```
Review `proposed_disables` — if an account appears here that shouldn't have been disabled, that's the misfire.

The audit table name and S3 bucket name are in CloudFormation outputs:
```bash
aws cloudformation describe-stacks \
  --stack-name BudgetBalanceDistributionStack \
  --query 'Stacks[0].Outputs' \
  --output table
```

---

## Step 3 — Restore Cost Category (< 10 min)

**Option A: AWS Console**
1. Open [Billing Console](https://console.aws.amazon.com/billing/) → Cost Categories
2. Find the RISP Group Sharing category (name from `COST_CATEGORY_ARN` env var)
3. Edit → restore `previous_state.enabled` accounts to the RISP_ENABLED rule
4. Save → changes take effect within hours

**Option B: AWS CLI**
```bash
aws ce update-cost-category-definition \
  --cost-category-arn "$COST_CATEGORY_ARN" \
  --rule-version CostCategoryExpression.v1 \
  --rules '[
    {
      "Value": "RISP_ENABLED",
      "Rule": {
        "Dimensions": {
          "Key": "LINKED_ACCOUNT",
          "Values": ["ACCOUNT_ID_1", "ACCOUNT_ID_2"]
        }
      },
      "Type": "REGULAR"
    }
  ]' \
  --default-value "RISP_DISABLED"
```
Replace ACCOUNT_ID_1, ACCOUNT_ID_2 with the `previous_state.enabled` account IDs.

**Option C: Restore Script**
```bash
python scripts/restore-risp-state.py \
  --snapshot-id [SK_from_audit_table] \
  --cost-category-arn "$COST_CATEGORY_ARN" \
  --execute
```
Without `--execute`, runs in dry-run mode and prints what would be restored.

See [scripts/restore-risp-state.py](../../scripts/restore-risp-state.py) for full usage.

---

## Step 4 — Verify Restoration (24-48 hours)

- Cost Category changes propagate within hours; final month-end billing reflects state at 23:59:59 UTC on last day of month
- Monitor CloudWatch dashboard for compliance status changes
- **Critical timing**: If misfire occurred in the last 3 days of the month, escalate to AWS Support to confirm month-end billing impact

To check current Cost Category state:
```bash
aws ce describe-cost-category-definition \
  --cost-category-arn "$COST_CATEGORY_ARN" \
  --query 'CostCategory.Rules'
```

---

## Step 5 — Disable Enforcement and Investigate

1. EventBridge Console → Rules → find `EnforcementExecuteSchedule` rule → Disable
2. Determine root cause: Was it a threshold misconfiguration? Config validation error? CE data staleness?
3. Fix root cause, test in dry-run mode (disable execute schedule, run manual invocation)
4. Re-enable EventBridge rule only after dry-run confirms correct behavior

To disable the enforcement schedule via CLI:
```bash
# Get the rule name from CloudFormation outputs
RULE_NAME=$(aws cloudformation describe-stacks \
  --stack-name BudgetBalanceDistributionStack \
  --query 'Stacks[0].Outputs[?OutputKey==`EnforcementExecuteRuleName`].OutputValue' \
  --output text)

aws events disable-rule --name "$RULE_NAME"
```

---

## Month-End Timing Warning

Per AWS documentation, the final bill is calculated based on Cost Category settings at **23:59:59 UTC on the last day of the month**. Mid-month corrections are applied to the current month's accruals. If enforcement misfired near month-end:
- Immediate restoration still affects the final bill positively
- Alert FinOps team to monitor billing for the affected accounts
- Consider requesting a billing adjustment from AWS Support if financial impact is significant

If the misfire occurred within 3 days of month-end, escalate immediately — do not wait for Step 4 verification before contacting AWS Support.
