"""
Discovery Lambda — Automated Discount Consumption Analysis

Scans all accounts in the AWS Organization, pulls RI/SP utilization
and per-account discount consumption from Cost Explorer, then computes
fair-share allocation and flags accounts exceeding their share.

SAFETY:
  - This module is 100% READ-ONLY. No writes, no mutations.
  - All API calls are ce:Get*, organizations:List*, ce:Describe*
  - Safe to run repeatedly (idempotent by nature + DynamoDB persistence)

REQUIRED IAM PERMISSIONS:
  - organizations:ListAccounts
  - ce:GetReservationUtilization
  - ce:GetSavingsPlansUtilization
  - ce:GetSavingsPlansUtilizationDetails
  - ce:GetCostAndUsage
  - ce:ListCostCategoryDefinitions
  - ce:DescribeCostCategoryDefinition
  - dynamodb:PutItem, GetItem, UpdateItem, DeleteItem (for idempotency)
  - sns:Publish (optional, for alerts)
  - s3:PutObject (optional, for plan artifact)

ENVIRONMENT VARIABLES:
  - CONFIG_TABLE_NAME: DynamoDB table for configuration
  - AUDIT_TABLE_NAME: DynamoDB table for audit logs and idempotency
  - LOOKBACK_DAYS: How many days to analyze (default: 30, max: 90)
  - THRESHOLD_PCT: Alert threshold as % of fair share (default: 120)
  - DRY_RUN: Set to "false" to enable SNS publishing (default: "true")
  - SNS_TOPIC_ARN: (Optional) SNS topic for alerts
  - PLAN_ARTIFACT_BUCKET: (Optional) S3 bucket for proposed_changes artifact
  - POWERTOOLS_SERVICE_NAME: Service name for logging (default: "discovery")
  - POWERTOOLS_LOG_LEVEL: Logging level (default: "INFO")
"""

import os
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.config import Config
from aws_lambda_powertools import Logger
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools import Metrics
from aws_lambda_powertools.utilities.idempotency import (
    DynamoDBPersistenceLayer,
    IdempotencyConfig,
    idempotent,
)

# ---------------------------------------------------------------------------
# Module-level setup (connection reuse across warm Lambda invocations)
# ---------------------------------------------------------------------------

logger = Logger(service="discovery")
metrics = Metrics(namespace="BudgetBalanceDistribution")

# Boto3 config for all clients: explicit region, timeouts, adaptive retries
boto_config = Config(
    region_name="us-east-1",  # Billing APIs ALWAYS run in commercial us-east-1
    connect_timeout=10,
    read_timeout=30,
    retries={
        "max_attempts": 3,
        "mode": "adaptive",
    },
)

# Idempotency persistence layer using DynamoDB audit table
# Reuses audit table with static_pk_value to separate idempotency records
persistence_layer = DynamoDBPersistenceLayer(
    table_name=os.environ.get("AUDIT_TABLE_NAME", ""),
    key_attr="idempotency_key",
    sort_key_attr="timestamp",
    static_pk_value="idempotency#discovery",
    expiry_attr="expiration",
)

# Idempotency config
# event_key_jmespath="id" because EventBridge generates unique id per scheduled event
# expires_after_seconds=86400 (24h) is longer than daily schedule gap
idempotency_config = IdempotencyConfig(
    event_key_jmespath="id",
    expires_after_seconds=86400,
    use_local_cache=True,
    raise_on_no_idempotency_key=False,  # Allow manual invocations without id
)

# ---------------------------------------------------------------------------
# Configuration from environment variables
# ---------------------------------------------------------------------------

MAX_LOOKBACK_DAYS = 90
LOOKBACK_DAYS = min(
    int(os.environ.get("LOOKBACK_DAYS", "30")),
    MAX_LOOKBACK_DAYS
)
THRESHOLD_PCT = float(os.environ.get("THRESHOLD_PCT", "120"))
CONFIG_TABLE_NAME = os.environ.get("CONFIG_TABLE_NAME", "")
AUDIT_TABLE_NAME = os.environ.get("AUDIT_TABLE_NAME", "")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
PLAN_ARTIFACT_BUCKET = os.environ.get("PLAN_ARTIFACT_BUCKET", "")


# ---------------------------------------------------------------------------
# Client Factory
# ---------------------------------------------------------------------------

def _get_client(service: str, region: str = "us-east-1") -> Any:
    """
    Create a boto3 client with explicit timeouts and retries.
    No infinite hangs — if AWS doesn't respond in 30s, we fail loud.
    """
    return boto3.client(service, config=boto_config)


# ---------------------------------------------------------------------------
# Phase 1a: Account Discovery
# ---------------------------------------------------------------------------

def discover_accounts() -> list[dict]:
    """
    List all ACTIVE accounts in the AWS Organization.
    Returns list of {Id, Name, Email, Status}.

    Paginated — safe for orgs with hundreds of accounts.
    """
    org_client = _get_client("organizations")
    accounts = []
    paginator = org_client.get_paginator("list_accounts")

    for page in paginator.paginate():
        for acct in page.get("Accounts", []):
            if acct.get("Status") == "ACTIVE":
                accounts.append({
                    "Id": acct["Id"],
                    "Name": acct.get("Name", "UNKNOWN"),
                    "Email": acct.get("Email", ""),
                    "Status": acct["Status"],
                })

    logger.info(
        "Discovered active accounts",
        extra={"account_count": len(accounts)}
    )
    return accounts


# ---------------------------------------------------------------------------
# Phase 1b: RI Utilization
# ---------------------------------------------------------------------------

def get_ri_utilization(start_date: str, end_date: str) -> dict:
    """
    Get Reserved Instance utilization for the org.
    Returns overall utilization % and per-account breakdown.
    """
    ce_client = _get_client("ce")

    try:
        response = ce_client.get_reservation_utilization(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="MONTHLY",
            GroupBy=[{"Type": "DIMENSION", "Key": "SUBSCRIPTION_ID"}],
        )

        total = response.get("Total", {})
        utilization_pct = total.get("UtilizationPercentage", "0")

        result = {
            "total_utilization_pct": float(utilization_pct),
            "total_purchased_hours": float(
                total.get("PurchasedHours", "0")
            ),
            "total_used_hours": float(total.get("TotalActualHours", "0")),
            "total_net_savings": float(
                total.get("NetRISavings", "0")
            ),
            "by_period": [],
        }

        for period in response.get("UtilizationsByTime", []):
            result["by_period"].append({
                "start": period["TimePeriod"]["Start"],
                "end": period["TimePeriod"]["End"],
                "utilization_pct": float(
                    period.get("Total", {}).get("UtilizationPercentage", "0")
                ),
                "net_savings": float(
                    period.get("Total", {}).get("NetRISavings", "0")
                ),
            })

        logger.info(
            "RI utilization retrieved",
            extra={
                "utilization_pct": result['total_utilization_pct'],
                "net_savings": result['total_net_savings']
            }
        )
        return result

    except ce_client.exceptions.DataUnavailableException:
        logger.warning("RI utilization data not available — no active RIs?")
        return {"total_utilization_pct": 0, "total_net_savings": 0, "by_period": []}
    except Exception as e:
        logger.error("Failed to get RI utilization", extra={"error": str(e)})
        raise


# ---------------------------------------------------------------------------
# Phase 1c: Savings Plans Utilization
# ---------------------------------------------------------------------------

def get_sp_utilization(start_date: str, end_date: str) -> dict:
    """
    Get Savings Plans utilization for the org.
    Returns overall utilization and per-account breakdown.
    """
    ce_client = _get_client("ce")

    try:
        response = ce_client.get_savings_plans_utilization(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="MONTHLY",
        )

        total = response.get("Total", {})
        utilization = total.get("Utilization", {})

        result = {
            "total_utilization_pct": float(
                utilization.get("UtilizationPercentage", "0")
            ),
            "total_commitment": float(
                utilization.get("TotalCommitment", "0")
            ),
            "used_commitment": float(
                utilization.get("UsedCommitment", "0")
            ),
            "unused_commitment": float(
                utilization.get("UnusedCommitment", "0")
            ),
            "net_savings": float(
                total.get("Savings", {}).get("NetSavings", "0")
            ),
            "by_period": [],
        }

        for period in response.get("SavingsPlansUtilizationsByTime", []):
            p_util = period.get("Utilization", {})
            p_savings = period.get("Savings", {})
            result["by_period"].append({
                "start": period["TimePeriod"]["Start"],
                "end": period["TimePeriod"]["End"],
                "utilization_pct": float(
                    p_util.get("UtilizationPercentage", "0")
                ),
                "net_savings": float(p_savings.get("NetSavings", "0")),
            })

        logger.info(
            "SP utilization retrieved",
            extra={
                "utilization_pct": result['total_utilization_pct'],
                "net_savings": result['net_savings'],
                "unused_commitment": result['unused_commitment']
            }
        )
        return result

    except ce_client.exceptions.DataUnavailableException:
        logger.warning("SP utilization data not available — no active SPs?")
        return {
            "total_utilization_pct": 0, "total_commitment": 0,
            "used_commitment": 0, "unused_commitment": 0,
            "net_savings": 0, "by_period": [],
        }
    except Exception as e:
        logger.error("Failed to get SP utilization", extra={"error": str(e)})
        raise


# ---------------------------------------------------------------------------
# Phase 1d: Per-Account Discount Consumption
# ---------------------------------------------------------------------------

def get_per_account_discount_usage(
    start_date: str, end_date: str
) -> tuple[list[dict], list]:
    """
    Get per-account cost breakdown showing how much each account
    is consuming in discounted vs on-demand rates.

    Uses LINKED_ACCOUNT grouping with multiple metrics to show
    the blended vs unblended cost delta (which reveals discount flow).

    Returns a tuple of (usage_list, results_by_time) where results_by_time
    is the raw CE ResultsByTime list used for data freshness detection.
    """
    ce_client = _get_client("ce")

    try:
        response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="MONTHLY",
            Metrics=[
                "UnblendedCost",
                "BlendedCost",
                "NetUnblendedCost",
                "AmortizedCost",
            ],
            GroupBy=[
                {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
            ],
        )

        results_by_time = response.get("ResultsByTime", [])
        accounts = {}

        for period in results_by_time:
            for group in period.get("Groups", []):
                account_id = group["Keys"][0]
                metrics_data = group["Metrics"]

                if account_id not in accounts:
                    accounts[account_id] = {
                        "account_id": account_id,
                        "unblended_cost": 0.0,
                        "blended_cost": 0.0,
                        "net_unblended_cost": 0.0,
                        "amortized_cost": 0.0,
                    }

                accounts[account_id]["unblended_cost"] += float(
                    metrics_data.get("UnblendedCost", {}).get("Amount", "0")
                )
                accounts[account_id]["blended_cost"] += float(
                    metrics_data.get("BlendedCost", {}).get("Amount", "0")
                )
                accounts[account_id]["net_unblended_cost"] += float(
                    metrics_data.get("NetUnblendedCost", {}).get("Amount", "0")
                )
                accounts[account_id]["amortized_cost"] += float(
                    metrics_data.get("AmortizedCost", {}).get("Amount", "0")
                )

        # Compute discount benefit: difference between what they'd pay
        # on-demand vs what they actually paid (amortized)
        result = []
        for acct_id, data in accounts.items():
            # The discount benefit is approximated by the gap between
            # unblended (on-demand equivalent) and amortized (with RI/SP)
            discount_benefit = data["unblended_cost"] - data["amortized_cost"]
            data["estimated_discount_benefit"] = max(discount_benefit, 0.0)
            result.append(data)

        result.sort(key=lambda x: x["estimated_discount_benefit"], reverse=True)

        total_discount_pool = sum(a['estimated_discount_benefit'] for a in result)
        logger.info(
            "Per-account discount analysis complete",
            extra={
                "account_count": len(result),
                "total_discount_pool": total_discount_pool
            }
        )
        return result, results_by_time

    except Exception as e:
        logger.error("Failed to get per-account discount usage", extra={"error": str(e)})
        raise


# ---------------------------------------------------------------------------
# Phase 1e: SP Utilization Details (per-account SP consumption)
# ---------------------------------------------------------------------------

def get_sp_utilization_by_account(
    start_date: str, end_date: str
) -> tuple[list[dict], dict[str, float]]:
    """
    Get Savings Plans utilization broken down by account.
    This shows which accounts are actually consuming SP benefits.

    Returns a tuple of (details_list, sp_by_account_dict) where
    sp_by_account_dict maps account_id -> total net_savings.
    """
    ce_client = _get_client("ce")

    try:
        response = ce_client.get_savings_plans_utilization_details(
            TimePeriod={"Start": start_date, "End": end_date},
        )

        results = []
        for detail in response.get("SavingsPlansUtilizationDetails", []):
            attrs = detail.get("Attributes", {})
            utilization = detail.get("Utilization", {})
            savings = detail.get("Savings", {})

            results.append({
                "sp_arn": detail.get("SavingsPlanArn", ""),
                "account_id": attrs.get("AccountId", "UNKNOWN"),
                "region": attrs.get("Region", ""),
                "sp_type": attrs.get("SavingsPlansType", ""),
                "utilization_pct": float(
                    utilization.get("UtilizationPercentage", "0")
                ),
                "used_commitment": float(
                    utilization.get("UsedCommitment", "0")
                ),
                "unused_commitment": float(
                    utilization.get("UnusedCommitment", "0")
                ),
                "net_savings": float(savings.get("NetSavings", "0")),
            })

        # Build per-account SP benefit map (sum net_savings per account)
        sp_by_account: dict[str, float] = {}
        for detail in results:
            acct_id = detail["account_id"]
            sp_by_account[acct_id] = sp_by_account.get(acct_id, 0.0) + detail["net_savings"]

        logger.info(
            "SP utilization details retrieved",
            extra={"savings_plan_count": len(results)}
        )
        return results, sp_by_account

    except ce_client.exceptions.DataUnavailableException:
        logger.warning("SP utilization details not available")
        return [], {}  # (empty results list, empty sp_by_account dict)
    except Exception as e:
        logger.error("Failed to get SP utilization details", extra={"error": str(e)})
        raise


# ---------------------------------------------------------------------------
# Phase 1f: Existing Cost Categories (check current RISP group config)
# ---------------------------------------------------------------------------

def get_existing_cost_categories() -> list[dict]:
    """
    List all existing Cost Category definitions.
    This tells us if RISP Group Sharing is already configured.
    """
    ce_client = _get_client("ce")
    categories = []

    try:
        paginator = ce_client.get_paginator("list_cost_category_definitions")
        for page in paginator.paginate():
            for cat in page.get("CostCategoryReferences", []):
                # Fetch full definition for each
                detail = ce_client.describe_cost_category_definition(
                    CostCategoryArn=cat["CostCategoryArn"]
                )
                cost_cat = detail.get("CostCategory", {})
                categories.append({
                    "arn": cost_cat.get("CostCategoryArn", ""),
                    "name": cost_cat.get("Name", ""),
                    "effective_start": cost_cat.get("EffectiveStart", ""),
                    "rule_count": len(cost_cat.get("Rules", [])),
                    "rules_summary": [
                        {
                            "value": r.get("Value", ""),
                            "type": r.get("Type", "REGULAR"),
                        }
                        for r in cost_cat.get("Rules", [])
                    ],
                })

        logger.info(
            "Retrieved existing cost categories",
            extra={"category_count": len(categories)}
        )
        return categories

    except Exception as e:
        logger.error("Failed to list cost categories", extra={"error": str(e)})
        raise


# ---------------------------------------------------------------------------
# Data Freshness Detection
# ---------------------------------------------------------------------------

def determine_data_freshness(results_by_time: list) -> str:
    """
    Estimate the freshest date for which Cost Explorer has complete billing data.

    CE refreshes billing data at least once every 24 hours, up to 3x daily.
    The safest bound is to find the last period with non-zero cost data.
    If no populated periods found, falls back to yesterday as a conservative estimate.

    Args:
        results_by_time: The ResultsByTime list from ce:GetCostAndUsage response

    Returns:
        ISO date string (YYYY-MM-DD) of the freshest data point
    """
    populated = [
        p for p in results_by_time
        if any(
            float(g.get("Metrics", {}).get("UnblendedCost", {}).get("Amount", "0")) > 0
            for g in p.get("Groups", [])
        )
    ]
    if populated:
        return populated[-1]["TimePeriod"]["End"]
    # Conservative fallback: yesterday
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Fair Share Analysis
# ---------------------------------------------------------------------------

def compute_fair_share_analysis(
    accounts: list[dict],
    per_account_usage: list[dict],
    threshold_pct: float = THRESHOLD_PCT,
    sp_by_account: dict[str, float] | None = None,
) -> dict:
    """
    Given N accounts and the total discount pool Z, compute:
      fair_share = Z / N
      flag any account consuming > threshold_pct% of fair_share

    Includes drain_score (benefit / fair_share, guarded against division by zero),
    ri_benefit (approximate RI attribution), and sp_benefit (per-account SP benefit).

    Returns a report with flagged accounts and recommendations.
    """
    n_accounts = len(accounts)
    if n_accounts == 0:
        return {"error": "No accounts found", "flagged": []}

    # Build lookup for account names
    account_names = {a["Id"]: a["Name"] for a in accounts}

    # Total discount pool
    total_discount = sum(
        a["estimated_discount_benefit"] for a in per_account_usage
    )

    if total_discount <= 0:
        return {
            "total_discount_pool": 0,
            "n_accounts": n_accounts,
            "fair_share_per_account": 0,
            "threshold_pct": threshold_pct,
            "flagged": [],
            "all_accounts": [],
            "message": "No discount benefits detected in this period",
        }

    fair_share = total_discount / n_accounts
    threshold_amount = fair_share * (threshold_pct / 100.0)

    flagged = []
    all_accounts_report = []

    for usage in per_account_usage:
        acct_id = usage["account_id"]
        benefit = usage["estimated_discount_benefit"]
        pct_of_fair = (benefit / fair_share * 100) if fair_share > 0 else 0

        sp_benefit = (sp_by_account or {}).get(acct_id, 0.0)
        # RI benefit is total benefit minus SP benefit (approximate attribution)
        ri_benefit = max(benefit - sp_benefit, 0.0)
        drain_score = round(benefit / fair_share, 4) if fair_share > 0 else 0.0

        entry = {
            "account_id": acct_id,
            "account_name": account_names.get(acct_id, "UNKNOWN"),
            "discount_benefit": round(benefit, 2),
            "fair_share": round(fair_share, 2),
            "pct_of_fair_share": round(pct_of_fair, 1),
            "drain_score": drain_score,           # normalized score vs fair share
            "ri_benefit": round(ri_benefit, 2),   # approximate RI attribution
            "sp_benefit": round(sp_benefit, 2),   # per-account SP benefit
            "over_threshold": benefit > threshold_amount,
        }

        # Emit drain_score as CloudWatch metric for CloudWatch alarms/dashboards
        metrics.add_metric(name="DrainScore", unit=MetricUnit.Count, value=drain_score)
        metrics.add_metadata(key="account_id", value=acct_id)
        metrics.flush_metrics()  # Flush per account to preserve dimension values

        all_accounts_report.append(entry)

        if benefit > threshold_amount:
            entry["overage_amount"] = round(benefit - fair_share, 2)
            flagged.append(entry)

    flagged.sort(key=lambda x: x["discount_benefit"], reverse=True)

    report = {
        "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_discount_pool": round(total_discount, 2),
        "n_accounts": n_accounts,
        "fair_share_per_account": round(fair_share, 2),
        "threshold_pct": threshold_pct,
        "threshold_amount": round(threshold_amount, 2),
        "flagged_count": len(flagged),
        "flagged_accounts": flagged,
        "all_accounts": all_accounts_report,
    }

    if flagged:
        logger.warning(
            "Accounts exceeding fair share threshold detected",
            extra={
                "flagged_count": len(flagged),
                "threshold_pct": threshold_pct,
                "fair_share": fair_share
            }
        )
        for f in flagged:
            logger.warning(
                "Flagged account",
                extra={
                    "account_name": f['account_name'],
                    "account_id": f['account_id'],
                    "discount_benefit": f['discount_benefit'],
                    "pct_of_fair_share": f['pct_of_fair_share']
                }
            )
    else:
        logger.info(
            "All accounts within fair share threshold",
            extra={
                "total_discount_pool": total_discount,
                "fair_share_per_account": fair_share
            }
        )

    return report


# ---------------------------------------------------------------------------
# Alert Publishing (optional, gated by DRY_RUN)
# ---------------------------------------------------------------------------

def publish_alert(report: dict) -> None:
    """
    Publish alert to SNS if flagged accounts exist.
    Only publishes if DRY_RUN is false AND SNS_TOPIC_ARN is set.
    """
    if not report.get("flagged_accounts"):
        logger.info("No flagged accounts — no alert needed")
        return

    if DRY_RUN:
        logger.info(
            "DRY_RUN mode — skipping alert publication",
            extra={"flagged_count": report['flagged_count']}
        )
        return

    if not SNS_TOPIC_ARN:
        logger.warning("No SNS_TOPIC_ARN set — cannot publish alert")
        return

    sns_client = _get_client("sns")

    subject = (
        f"[BUDGET BALANCE] {report['flagged_count']} account(s) "
        f"exceeding RI/SP fair share"
    )

    # Build human-readable message
    lines = [
        f"Discovery Report — {report['analysis_timestamp']}",
        f"",
        f"Total Discount Pool:     ${report['total_discount_pool']:,.2f}",
        f"Active Accounts:         {report['n_accounts']}",
        f"Fair Share Per Account:   ${report['fair_share_per_account']:,.2f}",
        f"Alert Threshold:         {report['threshold_pct']}%",
        f"",
        f"FLAGGED ACCOUNTS ({report['flagged_count']}):",
        f"{'='*60}",
    ]

    for f in report["flagged_accounts"]:
        lines.extend([
            f"  Account:  {f['account_name']} ({f['account_id']})",
            f"  Benefit:  ${f['discount_benefit']:,.2f} "
            f"({f['pct_of_fair_share']}% of fair share)",
            f"  Overage:  ${f['overage_amount']:,.2f}",
            f"  ---",
        ])

    lines.extend([
        f"",
        f"ACTION REQUIRED:",
        f"Review the flagged accounts in the Billing Console and consider",
        f"adjusting RISP Group Sharing preferences to prevent further drain.",
    ])

    message = "\n".join(lines)

    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject[:100],  # SNS subject max 100 chars
            Message=message,
        )
        logger.info("Alert published to SNS", extra={"topic_arn": SNS_TOPIC_ARN})
    except Exception as e:
        logger.error("Failed to publish SNS alert", extra={"error": str(e)})
        # Don't raise — alerting failure shouldn't crash the pipeline


# ---------------------------------------------------------------------------
# Plan Artifact (S3 write for human review before enforcement)
# ---------------------------------------------------------------------------

def write_plan_artifact(report: dict, data_as_of: str) -> str:
    """
    Write proposed changes to S3 for human review before enforcement.

    Writes two objects:
    1. Timestamped: proposed_changes/{YYYY-MM-DD}/{timestamp}.json (permanent record)
    2. Stable: proposed_changes/latest.json (what Enforcement Lambda reads)

    Safe to skip if PLAN_ARTIFACT_BUCKET is not set (e.g., pre-S3-bucket deployments).

    Returns the timestamped S3 key, or empty string if skipped.

    Pitfall: Write timestamped key first, then latest.json. If concurrent invocations
    happen, idempotency (already in place) prevents duplicate Discovery runs.
    """
    if not PLAN_ARTIFACT_BUCKET:
        logger.warning("PLAN_ARTIFACT_BUCKET not set — skipping plan artifact write")
        return ""

    s3_client = boto3.client("s3", config=boto_config)
    timestamp = datetime.now(timezone.utc).isoformat()

    artifact = {
        "generated_at": timestamp,
        "data_as_of": data_as_of,
        "analysis_window_start": report.get("analysis_window_start", ""),
        "analysis_window_end": report.get("analysis_window_end", ""),
        "total_discount_pool": report.get("total_discount_pool", 0),
        "fair_share_per_account": report.get("fair_share_per_account", 0),
        "n_accounts": report.get("n_accounts", 0),
        "threshold_pct": report.get("threshold_pct", 0),
        "proposed_disables": [
            a["account_id"] for a in report.get("flagged_accounts", [])
        ],
        "proposed_enables": [
            a["account_id"] for a in report.get("all_accounts", [])
            if not a.get("over_threshold", False)
        ],
        "accounts": report.get("all_accounts", []),
    }

    artifact_json = json.dumps(artifact, indent=2, default=str)

    # Write timestamped version first (permanent record, avoids overwrite race)
    timestamped_key = f"proposed_changes/{timestamp[:10]}/{timestamp}.json"
    s3_client.put_object(
        Bucket=PLAN_ARTIFACT_BUCKET,
        Key=timestamped_key,
        Body=artifact_json,
        ContentType="application/json",
    )

    # Write stable "latest" pointer (what Enforcement Lambda reads)
    s3_client.put_object(
        Bucket=PLAN_ARTIFACT_BUCKET,
        Key="proposed_changes/latest.json",
        Body=artifact_json,
        ContentType="application/json",
    )

    logger.info(
        "Plan artifact written to S3",
        extra={
            "bucket": PLAN_ARTIFACT_BUCKET,
            "timestamped_key": timestamped_key,
            "proposed_disable_count": len(artifact["proposed_disables"]),
            "proposed_enable_count": len(artifact["proposed_enables"]),
        }
    )
    return timestamped_key


# ---------------------------------------------------------------------------
# Lambda Handler
# ---------------------------------------------------------------------------

@logger.inject_lambda_context(log_event=True)
@idempotent(persistence_store=persistence_layer, config=idempotency_config)
def lambda_handler(event: dict, context: Any) -> dict:
    """
    Discovery Lambda entry point.

    Can be invoked by:
      - EventBridge scheduled rule (daily)
      - Manual invocation for testing
      - Step Functions for orchestration

    Returns a small summary dict to avoid DynamoDB 400KB item size limit.
    Full report details are logged to CloudWatch.
    """
    logger.info(
        "Discovery Lambda starting",
        extra={
            "lookback_days": LOOKBACK_DAYS,
            "threshold_pct": THRESHOLD_PCT,
            "dry_run": DRY_RUN
        }
    )

    # Compute time window
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_date = (
        datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    logger.info(f"Analysis window: {start_date} to {end_date}")

    # --- Step 1: Discover accounts ---
    accounts = discover_accounts()
    if not accounts:
        logger.error("No active accounts found — aborting")
        return {"status": "ERROR", "message": "No active accounts"}

    # --- Step 2: RI Utilization ---
    ri_util = get_ri_utilization(start_date, end_date)

    # --- Step 3: SP Utilization ---
    sp_util = get_sp_utilization(start_date, end_date)

    # --- Step 4: Per-Account Discount Consumption ---
    per_account, results_by_time = get_per_account_discount_usage(start_date, end_date)

    # Determine data freshness from CE results
    data_as_of = determine_data_freshness(results_by_time)
    data_age_hours = int(
        (datetime.now(timezone.utc) - datetime.fromisoformat(data_as_of + "T00:00:00+00:00")).total_seconds() / 3600
    )

    # --- Step 5: SP Details by Account ---
    sp_details, sp_by_account = get_sp_utilization_by_account(start_date, end_date)

    # --- Step 6: Existing Cost Categories ---
    cost_categories = get_existing_cost_categories()

    # --- Step 7: Fair Share Analysis ---
    report = compute_fair_share_analysis(accounts, per_account, sp_by_account=sp_by_account)
    report["data_as_of"] = data_as_of
    report["data_age_hours"] = data_age_hours

    # --- Step 8: Publish Alert if needed ---
    publish_alert(report)

    # --- Step 9: Write plan artifact to S3 ---
    # analysis_window_start and analysis_window_end are available from the time window computation above
    report["analysis_window_start"] = start_date
    report["analysis_window_end"] = end_date
    try:
        artifact_key = write_plan_artifact(report, data_as_of)
    except Exception as s3_err:
        logger.error(
            "Failed to write plan artifact to S3 — continuing",
            extra={"error": str(s3_err)}
        )
        artifact_key = ""
    if artifact_key:
        logger.info("Plan artifact available for review", extra={"s3_key": artifact_key})

    # Log full report details to CloudWatch (no size limit)
    logger.info(
        "Discovery complete — full report",
        extra={
            "discovery": {
                "accounts": accounts,
                "account_count": len(accounts),
            },
            "ri_utilization": ri_util,
            "sp_utilization": sp_util,
            "sp_details_by_account": sp_details,
            "per_account_discount_usage": per_account,
            "existing_cost_categories": cost_categories,
            "fair_share_analysis": report,
        }
    )

    logger.info(
        "Discovery Lambda complete",
        extra={
            "account_count": len(accounts),
            "flagged_count": report.get('flagged_count', 0),
            "data_as_of": data_as_of,
            "data_age_hours": data_age_hours,
        }
    )

    # Return SMALL summary to avoid 400KB DynamoDB item size limit
    # (used by idempotency persistence layer)
    return {
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "accounts_discovered": len(accounts),
        "flagged_count": report.get("flagged_count", 0),
        "total_discount_pool": report.get("total_discount_pool", 0),
        "fair_share_per_account": report.get("fair_share_per_account", 0),
        "data_as_of": data_as_of,
        "data_age_hours": data_age_hours,
        "artifact_s3_key": artifact_key if artifact_key else None,
    }
