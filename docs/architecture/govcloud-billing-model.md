# GovCloud Billing Architecture

## Overview

AWS designed billing APIs — Cost Explorer, Organizations, Cost Categories — to operate exclusively from the commercial partition (`aws`), even for GovCloud workloads. This is not a workaround or limitation of this tool; it is the AWS-designed model. Budget Balanced Distribution runs all billing API calls from the commercial partition (us-east-1) because that is the only partition where these APIs exist.

This document explains the account pairing model, why `ListAccounts` returns commercial account IDs, the ARN format requirements, the STS cross-partition boundary, and how GovCloud charges appear in Cost Explorer. Operators managing GovCloud workloads should read this document before configuring the system.

---

## Account Pairing Model

Every AWS GovCloud account is created via the `CreateGovCloudAccount` API, which is called from the commercial partition only. This API creates a 1:1 linked pair of accounts simultaneously:

1. A GovCloud account in the `aws-us-gov` partition
2. A paired commercial account in the `aws` partition

Both accounts share the same 12-digit account ID format (e.g., `123456789012`). The paired commercial account is automatically enrolled as a member account under the commercial payer account's AWS Organization.

```
Commercial Partition (aws)               GovCloud Partition (aws-us-gov)
┌─────────────────────────┐              ┌─────────────────────────────────┐
│ Management Account      │              │                                 │
│   Organizations         │◄────────────►│ (separate GovCloud Organization)│
│   Cost Explorer         │              │                                 │
│   (us-east-1)           │              └─────────────────────────────────┘
└─────────────────────────┘
         │
         │ ListAccounts returns:
         ▼
┌──────────────────────────────────────┐
│ 123456789012 (commercial paired)     │ ← This is the account ID used in CE
│ 234567890123 (commercial paired)     │   and Cost Categories for GovCloud workloads
└──────────────────────────────────────┘
```

The GovCloud account itself belongs to a separate, independent GovCloud Organization in the `aws-us-gov` partition. That organization is completely separate from the commercial organization — it has its own management account, its own member accounts list, and its own billing cycle.

---

## Why ListAccounts Returns Commercial IDs

When the Discovery Lambda calls `organizations:ListAccounts` from the commercial partition, it enumerates the **commercial paired accounts** — not the GovCloud accounts. This is the correct and expected behavior.

The GovCloud account does NOT appear in commercial `organizations:ListAccounts` results. The GovCloud account lives in the separate GovCloud organization, which is not accessible from the commercial partition's Organizations API. Only the paired commercial account is a member of the commercial organization.

This means: the account IDs returned by `ListAccounts` are the commercial paired account IDs for your GovCloud workloads. These are the same account IDs you will see in Cost Explorer when filtering by Linked Account. The tool uses these IDs consistently throughout — for Cost Explorer queries, for DynamoDB records, and for Cost Category rules.

There is no ID translation needed. The commercial paired account ID is the authoritative identifier used everywhere in the billing stack.

---

## ARN Format

All ARNs for billing resources use `arn:aws:` format, never `arn:aws-us-gov:`. This applies to:

- **Cost Category ARNs**: `arn:aws:ce::123456789012:costcategory/...`
- **IAM role ARNs**: `arn:aws:iam::123456789012:role/...`
- **Lambda ARNs**: `arn:aws:lambda:us-east-1:123456789012:function/...`
- **EventBridge ARNs**: `arn:aws:events:us-east-1:123456789012:rule/...`
- **DynamoDB ARNs**: `arn:aws:dynamodb:us-east-1:123456789012:table/...`
- **SNS ARNs**: `arn:aws:sns:us-east-1:123456789012:...`

The codebase enforces this via the `CLAUDE.md` project constraint. If you encounter any `arn:aws-us-gov:` ARN in billing-related code or configuration, it is incorrect and will fail with an API error in the commercial partition.

RISP Group Sharing is implemented via Cost Categories in the commercial partition. The Cost Category rules use `LINKED_ACCOUNT` dimension values (account IDs), which are the commercial paired account IDs. All enforcement operations reference these commercial ARNs.

---

## STS Cross-Partition Boundary

AWS partitions are isolated fault domains. IAM trust relationships cannot cross partition boundaries — you cannot create an `sts:AssumeRole` policy that targets a resource in a different partition. An `arn:aws-us-gov:iam::123456789012:role/SomeRole` ARN is unreachable from a principal in the `aws` (commercial) partition.

This tool does NOT need cross-partition assume-role, and you should never attempt to configure one. All billing APIs — Cost Explorer, Organizations, Cost Categories — are commercial partition APIs only. No GovCloud API calls are needed to manage discount sharing for GovCloud workloads.

The enforcement mechanism is entirely commercial-partition: the Discovery Lambda queries Cost Explorer (commercial), the Enforcement Lambda updates Cost Categories (commercial), and all IAM roles are in the commercial partition. GovCloud workload charges are attributed to the commercial paired account ID in Cost Explorer, so no GovCloud access is required.

---

## Cost Explorer Consolidation

GovCloud workload charges appear in Cost Explorer under the commercial payer account, attributed to the commercial paired account ID. When Cost Explorer returns per-account cost data using `GroupBy: [{"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}]`, GovCloud charges appear under the commercial paired account ID (e.g., `123456789012`), not under any GovCloud-specific identifier.

This consolidation happens automatically through the commercial payer/member account relationship established when `CreateGovCloudAccount` was called. The commercial payer account is billed for the GovCloud workloads of the paired commercial account member.

AWS Cost Explorer refreshes billing data at least once every 24 hours, and up to three times daily. A query at 2:00 AM UTC on February 23 may reflect cost data current as of February 21 18:00 UTC (approximately 32 hours stale). The discovery Lambda records a `data_as_of` timestamp to make this staleness visible in audit records.

---

## RISP Group Sharing Enforcement

Cost Categories use `LINKED_ACCOUNT` dimension values (account IDs) to control which accounts participate in RISP (Reserved Instance and Savings Plan) Group Sharing. Budget Balanced Distribution creates and updates a Cost Category named according to the `COST_CATEGORY_ARN` configuration parameter.

When an account exceeds its fair-share threshold, the enforcement Lambda removes its commercial paired account ID from the `RISP_ENABLED` Cost Category rule. This is the correct operation — GovCloud accounts appear as their commercial paired account IDs in Cost Categories, and the enforcement mechanism works correctly on those commercial IDs.

Cost Category changes propagate within hours and take full effect by the next Cost Explorer data refresh. The final month-end bill reflects the Cost Category state at **23:59:59 UTC on the last day of the month** — see the rollback runbook for implications when misfires occur near month-end.

---

## Verification Steps

Operators can confirm that GovCloud accounts appear correctly in Cost Explorer:

1. **List paired commercial accounts:**
   ```bash
   aws organizations list-accounts --profile management
   ```
   Note the account IDs returned. These are the commercial paired account IDs for all GovCloud accounts enrolled under the management account.

2. **Verify charges in Cost Explorer:**
   Open AWS Cost Explorer in the commercial partition (us-east-1). Filter by Linked Account and select one of the commercial paired account IDs. Verify that GovCloud workload charges appear under that account ID. If they do not appear, the commercial paired account may not be under the management account's consolidated billing.

3. **Check Cost Category enrollment:**
   ```bash
   aws ce describe-cost-category-definition \
     --cost-category-arn "$COST_CATEGORY_ARN"
   ```
   Verify the `RISP_ENABLED` rule contains the expected commercial paired account IDs.

4. **Check system logs:**
   The Discovery Lambda logs account enumeration results to CloudWatch at `/aws/lambda/DiscoveryLambda`. Verify the accounts listed match the expected commercial paired account IDs from Organizations.

---

## Open Questions

**Pending empirical validation:** Do commercial paired account IDs show GovCloud workload costs in Cost Explorer for all account configurations?

The research underlying this document indicates that GovCloud workload costs appear in Cost Explorer under the commercial paired account ID. However, this has not been empirically verified against the specific AWS Organization configuration for this deployment. If GovCloud charges do not appear under the expected commercial paired account IDs in Cost Explorer, the enforcement mechanism cannot function correctly.

See: `.planning/todos/pending/2026-02-13-validate-risp-group-sharing-actually-prevents-discount-application.md`

Before enabling Phase B enforcement for GovCloud workloads, operators should verify that Cost Explorer correctly attributes GovCloud charges to the commercial paired account IDs by following the Verification Steps above.
