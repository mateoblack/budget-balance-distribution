# Quick Deployment Guide

This guide walks through deploying Budget Balance Distribution and configuring it for the first time, from zero to nightly enforcement.

---

## Prerequisites

- AWS CDK v2 installed (`npm install -g aws-cdk`)
- Python 3.12+
- AWS credentials configured for the **commercial partition** account (us-east-1) — all billing APIs run here, even if the workload accounts are GovCloud

---

## Step 0: Create the Cost Category

The enforcement Lambda updates an existing Cost Category — it does not create one. You must create it manually before deploying the CDK stack.

**What the Cost Category does:** It is the mechanism AWS uses to control RISP (Reserved Instance / Savings Plans) discount sharing. Accounts in the `RISP_ENABLED` value receive shared discounts. Accounts in `RISP_DISABLED` do not. The enforcement Lambda moves accounts between these two values nightly based on their consumption.

**Required structure:**
- Name: `RISP_Sharing_Groups`
- Rule version: `CostCategoryExpression.v1`
- Two values: `RISP_ENABLED` and `RISP_DISABLED`, both using `LINKED_ACCOUNT` dimension filters
- Default value: `RISP_DISABLED` (fail-closed — any account not explicitly listed is excluded from discounts)

### Create via AWS CLI

Collect the account IDs you want to monitor and start them all in `RISP_ENABLED`. The enforcement Lambda will move accounts to `RISP_DISABLED` as they exceed their thresholds.

```bash
# Run this from us-east-1 (billing APIs require commercial us-east-1)
aws ce create-cost-category-definition \
  --region us-east-1 \
  --name "RISP_Sharing_Groups" \
  --rule-version "CostCategoryExpression.v1" \
  --rules '[
    {
      "Value": "RISP_ENABLED",
      "Type": "REGULAR",
      "Rule": {
        "Dimensions": {
          "Key": "LINKED_ACCOUNT",
          "Values": ["111111111111", "222222222222", "333333333333"]
        }
      }
    }
  ]' \
  --default-value "RISP_DISABLED"
```

Replace the account IDs with the actual accounts you want to manage. You don't need to create the `RISP_DISABLED` rule manually — the enforcement Lambda creates it automatically on the first run that has accounts to disable.

> **GovCloud note:** Use the commercial paired account IDs (12-digit IDs from the commercial Organizations console), not GovCloud account IDs. GovCloud workload charges appear under their commercial paired account IDs in Cost Explorer.

The response includes the ARN you need for deployment:

```json
{
    "CostCategoryArn": "arn:aws:ce::123456789012:costcategory/EXAMPLE123",
    "EffectiveStart": "2026-03-01T00:00:00Z"
}
```

Save this ARN — you'll pass it to `cdk deploy` in the next step.

### Verify the Cost Category exists

```bash
aws ce list-cost-category-definitions --region us-east-1 \
  --query 'CostCategoryReferences[?Name==`RISP_Sharing_Groups`]'
```

---

## Step 1: Configure cdk.json

Open `cdk.json` and replace the placeholder:

```json
{
  "app": "python app.py",
  "context": {
    "account_id": "123456789012",
    "region": "us-east-1",
    "config_table_name": "budget-config",
    "audit_table_name": "budget-audit"
  }
}
```

`config_table_name` and `audit_table_name` are optional — the defaults above are fine for most deployments.

---

## Step 2: Deploy the CDK Stack

```bash
pip install -r requirements.txt

export COST_CATEGORY_ARN="arn:aws:ce::123456789012:costcategory/YOUR_ID_HERE"

cdk bootstrap  # first time only
cdk deploy --context cost_category_arn=$COST_CATEGORY_ARN
```

The stack is named **`BudgetInfrastructureStack`**. Deployment takes ~3 minutes and creates:

- **DynamoDB config table** (`budget-config`) — stores spending groups, accounts, and thresholds
- **DynamoDB audit table** (`budget-audit`) — immutable log of all enforcement actions
- **S3 plan artifact bucket** — Discovery writes `proposed_changes/latest.json` here; Enforcement reads it
- **Discovery Lambda** — runs at 2:00 AM UTC, queries Cost Explorer, writes plan artifact
- **Enforcement Lambda** — runs at 2:30 AM UTC, reads plan artifact, applies Cost Category changes
- **EventBridge schedules** — Discovery and dry-run Enforcement are enabled; **production Enforcement is disabled by default**
- **CloudWatch alarms + dashboard** — monitors Lambda errors, duration, DynamoDB throttles
- **SNS alert topic** — subscribe your team's email/Slack here

After deploy, note the stack outputs — you'll need `ConfigTableName`, `AuditTableName`, and `EnforcementExecuteRuleName`.

---

## Step 3: Install the CLI

```bash
pip install -r requirements-cli.txt
```

Set environment variables using the stack outputs:

```bash
export CONFIG_TABLE_NAME=budget-config
export AUDIT_TABLE_NAME=budget-audit
```

**All CLI commands are dry-run by default.** Add `--execute` to write to DynamoDB.

---

## Step 4: Create Spending Groups

A spending group defines a pool of accounts and the total monthly discount budget available to that pool.

```bash
# Preview (no write)
python -m cli.config_manager group create production \
  --name "Production Accounts" \
  --budget 100000

# Write
python -m cli.config_manager group create production \
  --name "Production Accounts" \
  --budget 100000 \
  --execute
```

Repeat for each group:

```bash
python -m cli.config_manager group create staging \
  --name "Staging Accounts" \
  --budget 30000 \
  --execute

python -m cli.config_manager group create dev \
  --name "Development Accounts" \
  --budget 10000 \
  --execute
```

Verify:

```bash
python -m cli.config_manager group list
```

---

## Step 5: Add Accounts

Register each AWS account and assign it to one or more groups.

```bash
# Single group
python -m cli.config_manager account add 111111111111 \
  --name "Prod App" \
  --groups production \
  --execute

# Multiple groups (account gets the most restrictive threshold across all groups)
python -m cli.config_manager account add 222222222222 \
  --name "Shared Services" \
  --groups production,staging \
  --execute

python -m cli.config_manager account add 333333333333 \
  --name "Staging App" \
  --groups staging \
  --execute

python -m cli.config_manager account add 444444444444 \
  --name "Dev" \
  --groups dev \
  --execute
```

Verify:

```bash
python -m cli.config_manager account list
python -m cli.config_manager account list --group production  # filter by group
```

---

## Step 6: Set Thresholds

A threshold defines when an account gets cut off from the discount pool. There are three strategies:

### Fair share (recommended)

Divides the group budget equally among all active accounts. As accounts are added or removed, thresholds recalculate automatically at runtime.

```bash
python -m cli.config_manager threshold set production \
  --type fair_share \
  --execute
# With 4 accounts in a $100,000 group: each account gets $25,000/month
```

### Absolute

Fixed dollar amount per account, regardless of group size.

```bash
python -m cli.config_manager threshold set staging \
  --type absolute \
  --amount 8000 \
  --execute
```

### Percentage

A percentage of the group's total budget.

```bash
python -m cli.config_manager threshold set dev \
  --type percentage \
  --percentage 100 \
  --execute
# $10,000 group × 100% = $10,000 per account
```

Verify:

```bash
python -m cli.config_manager threshold list
```

**Multi-group accounts use the most restrictive threshold.** An account in `production` ($25k) and `staging` ($8k) gets a $8k effective threshold.

**Re-enablement is calendar-based by default.** Once an account exceeds its threshold and gets disabled, it stays out for the rest of the billing month — even if daily spend drops. It becomes eligible for re-enablement at the start of the next month. This matches the monthly fair-share allocation model and prevents heavy consumers from burning their quota and re-entering mid-month.

---

## Step 7: Subscribe to Alerts

Get the SNS topic ARN from the stack output:

```bash
aws cloudformation describe-stacks \
  --stack-name BudgetInfrastructureStack \
  --query 'Stacks[0].Outputs[?OutputKey==`AlertTopicArn`].OutputValue' \
  --output text
```

Subscribe an email address:

```bash
aws sns subscribe \
  --topic-arn <AlertTopicArn> \
  --protocol email \
  --notification-endpoint ops-team@yourcompany.com
```

Confirm the subscription from the email you receive.

---

## Step 8: Validate with Dry-Run (3+ days)

At this point the system runs nightly but **does not make any changes**. The EventBridge schedules run at:

- **2:00 AM UTC** — Discovery Lambda queries Cost Explorer, computes per-account discount usage, writes `proposed_changes/latest.json` to S3
- **2:30 AM UTC** — Enforcement Lambda reads the plan artifact, logs what it would enable/disable, writes a dry-run audit record

Review the outputs each morning:

**S3 plan artifact** — human-readable proposed changes:
```bash
aws s3 cp s3://<PlanArtifactBucketName>/proposed_changes/latest.json - | python3 -m json.tool
```

**CloudWatch Logs** — Discovery decisions:
```
/aws/lambda/DiscoveryLambda
```

**CloudWatch Logs** — Enforcement decisions (look for `execution_mode=DRY_RUN`):
```
/aws/lambda/EnforcementLambda
```

**CloudWatch Dashboard** — account compliance overview:
```bash
aws cloudformation describe-stacks \
  --stack-name BudgetInfrastructureStack \
  --query 'Stacks[0].Outputs[?OutputKey==`DashboardName`].OutputValue' \
  --output text
```
Open the dashboard name in the CloudWatch console.

**Before enabling production enforcement, confirm all of the following for 3+ consecutive days:**

- [ ] Proposed disables are accounts that genuinely over-consumed their fair share
- [ ] Proposed enables are accounts that have low consumption or are in a new month
- [ ] No unexpected accounts flagged (check for misconfigured group memberships)
- [ ] Discovery Lambda logs show correct threshold calculations
- [ ] CloudWatch dashboard shows metrics flowing through
- [ ] SNS alert delivery confirmed

---

## Step 9: Enable Production Enforcement

Once dry-run looks correct, enable the execute schedule:

```bash
RULE_NAME=$(aws cloudformation describe-stacks \
  --stack-name BudgetInfrastructureStack \
  --query 'Stacks[0].Outputs[?OutputKey==`EnforcementExecuteRuleName`].OutputValue' \
  --output text)

aws events enable-rule --name "$RULE_NAME"
```

From this point, at 2:30 AM UTC the Enforcement Lambda will:

1. Load config from DynamoDB
2. Read the Discovery plan artifact from S3 (falls back to direct Cost Explorer query if artifact is stale)
3. Load per-account disable state (to apply calendar-based re-enable gating)
4. Determine which accounts to enable or disable
5. **Update the Cost Category definition** — removes over-budget accounts from `RISP_ENABLED`, adds them to `RISP_DISABLED`
6. Write an enforcement audit record capturing the previous state (for rollback)
7. Update per-account disable state in DynamoDB

Changes to the Cost Category take effect within a few hours in Cost Explorer.

---

## Ongoing Operations

### Update configuration

```bash
# Change a group's budget
python -m cli.config_manager group update production --budget 120000 --execute

# Move an account between groups
python -m cli.config_manager account update 222222222222 \
  --remove-group staging --add-group production --execute

# Deactivate an account (excludes it from thresholds and enforcement)
python -m cli.config_manager account update 444444444444 --inactive --execute
```

### Manual Lambda invocation (for testing)

```bash
# Trigger Discovery manually
aws lambda invoke --function-name DiscoveryLambda /tmp/discovery-out.json
cat /tmp/discovery-out.json

# Trigger Enforcement in dry-run
aws lambda invoke \
  --function-name EnforcementLambda \
  --payload '{}' \
  /tmp/enforcement-out.json
cat /tmp/enforcement-out.json

# Trigger Enforcement in execute mode (careful)
aws lambda invoke \
  --function-name EnforcementLambda \
  --payload '{"execute": true}' \
  /tmp/enforcement-out.json
```

### Bulk import (initial setup shortcut)

Instead of running individual CLI commands, you can import a full config from JSON:

```bash
python -m cli.config_manager bulk import config.json --execute
```

Format:
```json
{
  "groups": [
    {
      "group_id": "production",
      "name": "Production Accounts",
      "total_budget": "100000",
      "active": true,
      "created_at": "2026-01-01T00:00:00Z",
      "updated_at": "2026-01-01T00:00:00Z"
    }
  ],
  "accounts": [
    {
      "account_id": "111111111111",
      "account_name": "Prod App",
      "group_memberships": ["production"],
      "active": true,
      "created_at": "2026-01-01T00:00:00Z",
      "updated_at": "2026-01-01T00:00:00Z"
    }
  ],
  "thresholds": [
    {
      "threshold_id": "production-fair_share",
      "group_id": "production",
      "threshold_type": "fair_share",
      "created_at": "2026-01-01T00:00:00Z",
      "updated_at": "2026-01-01T00:00:00Z"
    }
  ]
}
```

### Rollback

See `docs/runbooks/enforcement-rollback.md` for step-by-step rollback using the audit table snapshots.

---

## How the Nightly Cycle Works

```
2:00 AM UTC — Discovery Lambda
  ├── Load config from DynamoDB (groups, accounts, thresholds)
  ├── Query Cost Explorer: 30-day per-account discount usage
  ├── Calculate effective thresholds (fair-share / absolute / percentage)
  └── Write proposed_changes/latest.json to S3

2:30 AM UTC — Enforcement Lambda
  ├── Load config from DynamoDB
  ├── Read proposed_changes/latest.json from S3
  │     └── Falls back to direct Cost Explorer query if artifact is >26h old
  ├── Load per-account disable state from audit table
  ├── Determine actions:
  │     ├── consumption > threshold       → disable
  │     ├── consumption ≤ re-enable threshold
  │     │     ├── disabled this month (calendar strategy) → stay out
  │     │     └── otherwise                               → enable
  │     └── no threshold configured for account          → skip
  │
  ├── DRY_RUN=true  → log intended changes, write dry-run audit record
  └── DRY_RUN=false → update Cost Category, write audit record, update disable state
```
