# Budget Balanced Distribution

> Automated enforcement of fair discount allocation across AWS Organizations

**Status:** 🚧 Work in Progress

## Overview

Budget Balanced Distribution automatically monitors AWS account spending and enforces balanced RISP (Reserved Instance and Savings Plan) discount sharing. When accounts exceed their allocated discount budget, the system removes them from the discount pool until they return to compliance—no manual intervention required.

**Key Features:**
- 🔍 Daily automated monitoring of account discount consumption
- ⚖️ Fair-share threshold calculation across spending groups
- 🔒 Automatic RISP sharing enforcement (disable over-budget accounts)
- 🔄 Auto re-enablement when accounts return to compliance
- 📊 CloudWatch dashboard with real-time compliance metrics
- 🔔 SNS alerts for threshold violations
- 📝 Comprehensive audit trail (CloudWatch + DynamoDB)
- 🛡️ Dry-run by default with safety gates

## Architecture

```
┌─────────────────┐     Daily 2:00 AM UTC     ┌──────────────────┐
│  Discovery      │◄────────────────────────────┤  EventBridge    │
│  Lambda         │                             │  Schedule       │
└────────┬────────┘                             └──────────────────┘
         │
         │ Queries Cost Explorer
         │ Calculates fair-share
         │ Flags over-budget accounts
         ▼
┌─────────────────────────────────────────────┐
│          DynamoDB Tables                    │
│  • Configuration (groups, thresholds)       │
│  • Audit (enforcement history)              │
└────────┬────────────────────────────────────┘
         │
         │ Reads config & thresholds
         ▼
┌─────────────────┐     Daily 2:30 AM UTC     ┌──────────────────┐
│  Enforcement    │◄────────────────────────────┤  EventBridge    │
│  Lambda         │                             │  Schedule       │
└────────┬────────┘                             └──────────────────┘
         │
         │ Updates Cost Categories
         │ Disables RISP sharing
         │ Writes audit records
         ▼
┌─────────────────────────────────────────────┐
│      AWS Cost Category (RISP Groups)        │
│  Accounts removed from discount pool        │
└─────────────────────────────────────────────┘
```

## Phase A / Phase B Deployment Model

The system ships with two operational modes separated by an explicit activation gate.

**Phase A (Read-Only Discovery)** is the default state. The Discovery Lambda runs daily at 2:00 AM UTC, analyzes discount consumption across all configured accounts, calculates fair-share thresholds, and writes `proposed_changes.json` to S3. All findings are logged to CloudWatch. No writes are made to Cost Categories. Phase A is safe to run indefinitely — there is no risk of accidentally disabling RISP sharing.

**Phase B (Enforcement)** is activated by enabling the `EnforcementExecuteSchedule` EventBridge rule in the AWS Console. When enabled, this rule passes `{"execute": true}` to the Enforcement Lambda at 2:30 AM UTC, enabling actual Cost Category writes that disable RISP sharing for over-budget accounts.

### Phase B Enablement Checklist

Complete all steps before enabling Phase B enforcement:

1. Review at least 3 consecutive days of `proposed_changes/latest.json` in S3 — confirm the disable/enable decisions look correct for your accounts
2. Subscribe to the SNS alert topic and verify you receive test notifications (see Monitoring section)
3. Check the CloudWatch dashboard shows metrics from Discovery Lambda runs (account counts, threshold violations)
4. Query the DynamoDB audit table and verify dry-run records exist with correct account IDs
5. Review the rollback runbook (`docs/runbooks/enforcement-rollback.md`) so you know the recovery procedure if enforcement misfires
6. Enable the `EnforcementExecuteSchedule` EventBridge rule in AWS Console — the rule name is in CloudFormation outputs as `EnforcementExecuteRuleName`

## Quick Start

### Prerequisites

- AWS CDK CLI installed (`npm install -g aws-cdk`)
- Python 3.12+
- AWS account with billing access (must run from commercial partition us-east-1)
- Cost Category ARN for RISP Group Sharing

### Deploy

```bash
# 1. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -r requirements-cli.txt      # CLI tool dependencies (required before CLI commands)

# 2. Set Cost Category ARN (required)
export COST_CATEGORY_ARN="arn:aws:ce::123456789012:costcategory/12345678-1234-1234-1234-123456789012"

# 3. Bootstrap CDK (first time only)
cdk bootstrap

# 4. Deploy infrastructure
cdk deploy --context cost_category_arn=$COST_CATEGORY_ARN

# 5. Configure spending groups and thresholds
python -m cli.config_manager groups create production --budget 10000
python -m cli.config_manager accounts add 123456789012 --groups production --status active
python -m cli.config_manager thresholds set production --strategy fair_share
```

### Enable Production Enforcement

After validating the system in dry-run mode:

1. Review the CloudWatch dashboard for compliance metrics
2. Verify audit logs in DynamoDB
3. Check the ProductionEnablementChecklist CloudFormation output
4. Enable the production enforcement rule in AWS Console:
   - EventBridge → Rules → `ProductionEnforcementSchedule`
   - Click "Enable"

<details>
<summary><strong>📋 Required IAM Permissions</strong></summary>

## IAM Roles Created by CDK

This system creates two Lambda execution roles with least-privilege permissions:

### Discovery Lambda Role (Read-Only)

**Purpose:** Daily monitoring of account spend and discount consumption

**DynamoDB Permissions:**
```json
{
  "Effect": "Allow",
  "Action": [
    "dynamodb:GetItem",
    "dynamodb:Query"
  ],
  "Resource": "arn:aws:dynamodb:us-east-1:ACCOUNT:table/ConfigTable"
}
```

```json
{
  "Effect": "Allow",
  "Action": [
    "dynamodb:PutItem",
    "dynamodb:UpdateItem"
  ],
  "Resource": "arn:aws:dynamodb:us-east-1:ACCOUNT:table/AuditTable"
}
```

**Cost Explorer Permissions:**
```json
{
  "Sid": "CostExplorerReadAccess",
  "Effect": "Allow",
  "Action": [
    "ce:GetCostAndUsage",
    "ce:GetReservationUtilization",
    "ce:GetSavingsPlansUtilization",
    "ce:GetSavingsPlansUtilizationDetails",
    "ce:ListCostCategoryDefinitions",
    "ce:DescribeCostCategoryDefinition"
  ],
  "Resource": "*"
}
```

**Organizations Permissions:**
```json
{
  "Sid": "OrganizationsReadAccess",
  "Effect": "Allow",
  "Action": [
    "organizations:ListAccounts"
  ],
  "Resource": "*"
}
```

**CloudWatch Logs:**
- Managed Policy: `arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole`

---

### Enforcement Lambda Role (Write-Enabled)

**Purpose:** Daily enforcement of RISP sharing based on compliance status

**DynamoDB Permissions:**
```json
{
  "Effect": "Allow",
  "Action": [
    "dynamodb:GetItem",
    "dynamodb:Query",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem"
  ],
  "Resource": [
    "arn:aws:dynamodb:us-east-1:ACCOUNT:table/ConfigTable",
    "arn:aws:dynamodb:us-east-1:ACCOUNT:table/AuditTable"
  ]
}
```

**Cost Explorer Permissions:**
```json
{
  "Sid": "CostExplorerReadAccessEnforcement",
  "Effect": "Allow",
  "Action": [
    "ce:GetCostAndUsage",
    "ce:DescribeCostCategoryDefinition"
  ],
  "Resource": "*"
}
```

**Cost Category Write Permissions:**
```json
{
  "Sid": "CostCategoryWriteAccess",
  "Effect": "Allow",
  "Action": [
    "ce:UpdateCostCategoryDefinition"
  ],
  "Resource": "*"
}
```

**CloudWatch Logs:**
- Managed Policy: `arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole`

---

### SNS Topic Permissions

**Purpose:** Alert notifications for threshold violations and enforcement failures

- SNS topic created for alarm notifications
- FinOps team subscribes to receive alerts

---

### Notes

- **Cost Explorer APIs** do not support resource-level permissions (must use `"Resource": "*"`)
- **Organizations APIs** do not support resource-level permissions (must use `"Resource": "*"`)
- **Commercial Partition Required:** All billing APIs run from commercial partition (us-east-1), even for GovCloud workloads
- **Least Privilege:** Discovery role has NO Cost Category write access; only Enforcement role can modify RISP sharing

</details>

## Configuration

### CLI Tool

The CLI tool manages spending groups, account assignments, and thresholds:

```bash
# Create spending group
python -m cli.config_manager groups create production --budget 10000

# Add accounts to group
python -m cli.config_manager accounts add 123456789012 --groups production --status active
python -m cli.config_manager accounts add 234567890123 --groups production --status active

# Set threshold strategy
python -m cli.config_manager thresholds set production --strategy fair_share

# List configuration
python -m cli.config_manager groups list
python -m cli.config_manager accounts list
python -m cli.config_manager thresholds list

# Dry-run by default (use --execute flag for actual writes)
python -m cli.config_manager groups create staging --budget 5000 --execute
```

### Threshold Strategies

**Fair Share (Implemented in v1.0):**
- Dynamically calculates per-account limit: `total_budget / num_active_accounts`
- Accounts flagged when discount usage exceeds their fair share
- Self-balancing as accounts are added/removed

**Future Strategies (v2.0):**
- Absolute dollars: Fixed monthly spending limit per account
- Percentage-based: Accounts flagged when consuming > X% of org discount pool

## Monitoring

### CloudWatch Dashboard

Access the dashboard via CloudFormation outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name BudgetBalanceDistributionStack \
  --query 'Stacks[0].Outputs[?OutputKey==`DashboardURL`].OutputValue' \
  --output text
```

**Dashboard Sections:**
1. Account compliance overview (compliant vs flagged counts)
2. Compliance trend over time
3. Lambda invocation metrics (success/error rates)
4. Lambda duration and throttles
5. DynamoDB capacity consumption
6. Enforcement audit trail (recent actions)
7. Enforcement action history
8. Error logs and anomalies

### Alarms

**Infrastructure Alarms:**
- Discovery Lambda errors > 1 in 5 min
- Discovery Lambda duration > 4.5 min (90% of 5 min timeout)
- Enforcement Lambda errors > 1 in 5 min
- Enforcement Lambda duration > 4.5 min
- Config table read throttles
- Audit table write throttles

**Business Logic Alarms:**
- Threshold violations detected (accounts flagged)
- Anomalous flagged account count (> 5 accounts flagged in single run)

**Composite Alarm:**
- Aggregates all 10 alarms into single health indicator
- Triggers SNS notification when any alarm fires

### SNS Alerts

Subscribe to the alert topic:

```bash
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name BudgetBalanceDistributionStack \
  --query 'Stacks[0].Outputs[?OutputKey==`AlertTopicArn`].OutputValue' \
  --output text)

aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol email \
  --notification-endpoint finops-team@example.com
```

## Audit Trail

All enforcement actions are logged to two locations:

### CloudWatch Logs

Real-time structured JSON logs for debugging:

```bash
# View discovery logs
aws logs tail /aws/lambda/DiscoveryLambda --follow

# View enforcement logs
aws logs tail /aws/lambda/EnforcementLambda --follow

# Query audit records with CloudWatch Insights
aws logs start-query \
  --log-group-name /aws/lambda/EnforcementLambda \
  --query-string 'fields @timestamp, execution_mode, action, account_id, reason | filter entity_type = "enforcement_action"'
```

### DynamoDB Audit Table

Immutable append-only records for compliance queries:

```bash
# Query enforcement actions for specific account
aws dynamodb query \
  --table-name AuditTable \
  --key-condition-expression "PK = :pk" \
  --expression-attribute-values '{":pk":{"S":"ENFORCEMENT#123456789012"}}'

# Query by entity type using GSI
aws dynamodb query \
  --table-name AuditTable \
  --index-name EntityTypeIndex \
  --key-condition-expression "entity_type = :type" \
  --expression-attribute-values '{":type":{"S":"enforcement_action"}}'
```

## Safety Features

### Dry-Run by Default

**CLI Tool:**
- All write commands default to dry-run mode
- Require `--execute` flag for actual changes
- Shows preview of what would change

**Enforcement Lambda:**
- `DRY_RUN=true` environment variable by default
- Logs intended changes without executing
- Production rule disabled by default (manual enablement required)

### Audit Logging

- All configuration changes logged with actor ($USER)
- All enforcement actions recorded with full context
- Previous RISP sharing state captured for rollback

### Protected Resources

- DynamoDB tables use `RemovalPolicy.RETAIN`
- Deletion protection enabled on both tables
- Point-in-time recovery enabled for rollback

## Cost Estimate

**Daily Operational Costs:**
- Cost Explorer API calls: ~$0.10/day (5-10 API calls per run)
- Lambda invocations: ~$0.01/day (2 invocations at 512MB for 30 seconds)
- DynamoDB: Pay-per-request (minimal for small organizations)
- CloudWatch: Logs and dashboard (included in free tier for low volume)

**Estimated Total:** ~$3-5/month for small-to-medium organizations

## Development

### Run Tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest

# Run with coverage
pytest --cov=lambda --cov=shared --cov=cli --cov-report=term-missing
```

### CDK Testing

```bash
# Synthesize CloudFormation template
cdk synth

# Run CDK assertion tests
pytest tests/test_infrastructure_stack.py -v
```

### Local Development

```bash
# Activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -r requirements-cli.txt

# Run CLI in dry-run mode
python -m cli.config_manager groups list
```

## Troubleshooting

**Q: Enforcement Lambda returns "Cost Category ARN not configured"**

A: Set the `COST_CATEGORY_ARN` context variable during deployment:

```bash
cdk deploy --context cost_category_arn=arn:aws:ce::ACCOUNT:costcategory/ID
```

**Q: Discovery Lambda timing out**

A: Cost Explorer queries can take 10-30 seconds. The Lambda timeout is 5 minutes, which should be sufficient. Check CloudWatch Logs for slow API calls.

**Q: Accounts not being re-enabled after dropping below threshold**

A: Enforcement Lambda checks compliance daily at 2:30 AM UTC. Re-enablement is eventual (within 24 hours), not immediate.

**Q: "Billing APIs require us-east-1" error**

A: All Cost Explorer and Organizations APIs must run from commercial partition (us-east-1), even when managing GovCloud workloads. Deploy this stack to us-east-1.

## Documentation

- [GovCloud Billing Architecture](docs/architecture/govcloud-billing-model.md) — Why billing APIs run from commercial partition for GovCloud workloads, account pairing model, ARN format requirements
- [Enforcement Rollback Runbook](docs/runbooks/enforcement-rollback.md) — Recovery procedure if enforcement misfires, with < 15 minute restoration path

## License

Internal AWS tool for FinOps discount management.

## Support

For questions or issues:
1. Check CloudWatch dashboard for system health
2. Review CloudWatch Logs for detailed error messages
3. Query DynamoDB audit table for enforcement history
4. Contact FinOps team for configuration assistance
