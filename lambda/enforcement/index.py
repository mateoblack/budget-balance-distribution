"""
Enforcement Lambda — Automated RISP Sharing Group Updates

Loads configuration, queries Cost Explorer for per-account discount usage,
compares against thresholds, then updates Cost Category definitions to
enable/disable RISP sharing for accounts based on their consumption.

SAFETY:
  - Defaults to DRY-RUN mode (no writes) unless explicitly enabled
  - Captures snapshots before any Cost Category modifications
  - All changes audited to DynamoDB with timestamp and actor info
  - Fail-closed: accounts without thresholds excluded from RISP sharing

REQUIRED IAM PERMISSIONS:
  - ce:GetCostAndUsage
  - ce:DescribeCostCategoryDefinition
  - ce:UpdateCostCategoryDefinition
  - dynamodb:GetItem, Query (config table)
  - dynamodb:PutItem, UpdateItem (audit table for idempotency + audit records)

ENVIRONMENT VARIABLES:
  - CONFIG_TABLE_NAME: DynamoDB table for configuration
  - AUDIT_TABLE_NAME: DynamoDB table for audit logs and idempotency
  - COST_CATEGORY_ARN: ARN of Cost Category to update (must be set at deploy time)
  - DRY_RUN: "true" (default) or "false" - controls write behavior
  - POWERTOOLS_SERVICE_NAME: Service name for logging (default: "enforcement")
  - POWERTOOLS_LOG_LEVEL: Logging level (default: "INFO")
"""

import os
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.idempotency import (
    DynamoDBPersistenceLayer,
    IdempotencyConfig,
    idempotent,
)

from cost_category import (
    build_cost_category_rules,
    capture_cost_category_snapshot,
    update_risp_sharing_groups,
    extract_previous_state,
)
from enforcement import determine_enforcement_actions
from shared.config_loader import (
    load_all_config,
    get_account_thresholds,
    ConfigValidationError,
)
from audit import write_enforcement_audit_record

# ---------------------------------------------------------------------------
# Module-level setup (connection reuse across warm Lambda invocations)
# ---------------------------------------------------------------------------

logger = Logger(service="enforcement")

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
    static_pk_value="idempotency#enforcement",
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

CONFIG_TABLE_NAME = os.environ.get("CONFIG_TABLE_NAME", "")
AUDIT_TABLE_NAME = os.environ.get("AUDIT_TABLE_NAME", "")
COST_CATEGORY_ARN = os.environ.get("COST_CATEGORY_ARN", "")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
LOOKBACK_DAYS = 30  # Match discovery Lambda default


# ---------------------------------------------------------------------------
# Client Factory
# ---------------------------------------------------------------------------

def _get_client(service: str) -> Any:
    """
    Create a boto3 client with explicit timeouts and retries.
    All clients use us-east-1 region for billing APIs.
    """
    return boto3.client(service, config=boto_config)


# ---------------------------------------------------------------------------
# Per-Account Discount Usage Query (duplicated from discovery)
# ---------------------------------------------------------------------------

def get_per_account_discount_usage(
    start_date: str, end_date: str
) -> list[dict]:
    """
    Get per-account cost breakdown showing discount consumption.

    Uses LINKED_ACCOUNT grouping with UnblendedCost and AmortizedCost metrics.
    Estimated discount benefit = UnblendedCost - AmortizedCost (on-demand vs discounted).

    This function is intentionally duplicated from discovery rather than shared,
    because enforcement needs independent Cost Explorer access to avoid timing
    dependencies on discovery execution.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        List of dicts with account_id, unblended_cost, amortized_cost,
        and estimated_discount_benefit
    """
    ce_client = _get_client("ce")

    try:
        response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="MONTHLY",
            Metrics=[
                "UnblendedCost",
                "AmortizedCost",
            ],
            GroupBy=[
                {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
            ],
        )

        accounts = {}

        for period in response.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                account_id = group["Keys"][0]
                metrics = group["Metrics"]

                if account_id not in accounts:
                    accounts[account_id] = {
                        "account_id": account_id,
                        "unblended_cost": 0.0,
                        "amortized_cost": 0.0,
                    }

                accounts[account_id]["unblended_cost"] += float(
                    metrics.get("UnblendedCost", {}).get("Amount", "0")
                )
                accounts[account_id]["amortized_cost"] += float(
                    metrics.get("AmortizedCost", {}).get("Amount", "0")
                )

        # Compute discount benefit
        result = []
        for acct_id, data in accounts.items():
            discount_benefit = data["unblended_cost"] - data["amortized_cost"]
            data["estimated_discount_benefit"] = max(discount_benefit, 0.0)
            result.append(data)

        result.sort(key=lambda x: x["estimated_discount_benefit"], reverse=True)

        total_discount_pool = sum(a['estimated_discount_benefit'] for a in result)
        logger.info(
            "Per-account discount usage retrieved",
            extra={
                "account_count": len(result),
                "total_discount_pool": total_discount_pool
            }
        )
        return result

    except Exception as e:
        logger.error("Failed to get per-account discount usage", extra={"error": str(e)})
        raise


# ---------------------------------------------------------------------------
# Lambda Handler
# ---------------------------------------------------------------------------

@logger.inject_lambda_context(log_event=True)
@idempotent(persistence_store=persistence_layer, config=idempotency_config)
def lambda_handler(event: dict, context: Any) -> dict:
    """
    Enforcement Lambda entry point.

    Can be invoked by:
      - EventBridge scheduled rule (daily at 2:30 AM UTC)
      - Manual invocation for testing
      - Step Functions for orchestration

    Event input:
      - execute: boolean (optional) - if True, overrides DRY_RUN env var to enable writes

    Returns a small summary dict to avoid DynamoDB 400KB item size limit.
    Full details logged to CloudWatch.
    """
    # Determine execution mode: DRY_RUN env var default, overridable by event
    execute_mode = event.get("execute", False)
    dry_run = not execute_mode if execute_mode else DRY_RUN
    timestamp = datetime.now(timezone.utc).isoformat()

    execution_mode_str = "EXECUTE" if not dry_run else "DRY_RUN"

    # Add execution context to all logs in this invocation
    logger.append_keys(
        execution_mode=execution_mode_str,
        cost_category_arn=COST_CATEGORY_ARN,
        enforcement_run_id=timestamp,
    )

    logger.info(
        "Enforcement Lambda starting",
        extra={
            "execution_mode": execution_mode_str,
            "lookback_days": LOOKBACK_DAYS,
            "cost_category_arn": COST_CATEGORY_ARN
        }
    )

    # Validate required configuration
    if not COST_CATEGORY_ARN:
        error_msg = "COST_CATEGORY_ARN environment variable not set"
        logger.error(error_msg)
        return {
            "status": "ERROR",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": error_msg
        }

    # Compute time window (last 30 days, same as discovery)
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_date = (
        datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    logger.info(f"Analysis window: {start_date} to {end_date}")

    # --- Step 1: Load configuration ---
    try:
        config = load_all_config(CONFIG_TABLE_NAME)
        logger.info(
            "Configuration loaded",
            extra={
                "group_count": len(config['groups']),
                "account_count": len(config['accounts']),
                "threshold_count": len(config['thresholds'])
            }
        )
    except ConfigValidationError as e:
        error_msg = f"Configuration validation failed: {len(e.errors)} errors"
        logger.error(error_msg, extra={"validation_errors": e.errors})
        return {
            "status": "ERROR",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": error_msg,
            "validation_errors": e.errors
        }
    except Exception as e:
        error_msg = f"Failed to load configuration: {str(e)}"
        logger.error(error_msg, extra={"error": str(e)})
        return {
            "status": "ERROR",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": error_msg
        }

    # --- Step 2: Calculate account thresholds ---
    account_thresholds = get_account_thresholds(config)
    logger.info(
        "Account thresholds calculated",
        extra={"account_threshold_count": len(account_thresholds)}
    )

    if not account_thresholds:
        warning_msg = "No account thresholds calculated - check configuration"
        logger.warning(warning_msg)
        return {
            "status": "WARNING",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": warning_msg
        }

    # --- Step 3: Query Cost Explorer for per-account discount usage ---
    try:
        per_account_usage = get_per_account_discount_usage(start_date, end_date)
        logger.info(
            "Per-account usage data retrieved",
            extra={"usage_data_count": len(per_account_usage)}
        )
    except Exception as e:
        error_msg = f"Failed to query Cost Explorer: {str(e)}"
        logger.error(error_msg, extra={"error": str(e)})
        return {
            "status": "ERROR",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": error_msg
        }

    if not per_account_usage:
        warning_msg = "No per-account usage data returned from Cost Explorer"
        logger.warning(warning_msg)
        return {
            "status": "WARNING",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": warning_msg
        }

    # --- Step 4: Determine enforcement actions ---
    enforcement_actions = determine_enforcement_actions(
        per_account_usage, account_thresholds
    )
    enable_list = enforcement_actions["enable"]
    disable_list = enforcement_actions["disable"]

    logger.info(
        "Enforcement actions determined",
        extra={
            "enable_count": len(enable_list),
            "disable_count": len(disable_list),
            "enabled_accounts": enable_list,
            "disabled_accounts": disable_list
        }
    )

    # --- Step 5: Log action summary ---
    logger.info(
        f"Action summary: {len(enable_list)} accounts to enable, "
        f"{len(disable_list)} accounts to disable"
    )

    # --- Step 6: Execute or dry-run ---

    if dry_run:
        # Step 6a: Dry-run mode
        logger.info(
            "DRY-RUN: Would update Cost Category",
            extra={
                "enable_count": len(enable_list),
                "disable_count": len(disable_list),
                "enabled_accounts": enable_list,
                "disabled_accounts": disable_list,
            }
        )

        # Write dry-run audit record to DynamoDB (AUDIT-02, AUDIT-03)
        dynamodb_resource = boto3.resource("dynamodb", config=boto_config)
        audit_tbl = dynamodb_resource.Table(AUDIT_TABLE_NAME)
        write_enforcement_audit_record(
            audit_table=audit_tbl,
            timestamp=timestamp,
            dry_run=True,
            cost_category_arn=COST_CATEGORY_ARN,
            enable_list=enable_list,
            disable_list=disable_list,
            execution_result="DRY_RUN",
        )

        return {
            "status": "DRY_RUN",
            "timestamp": timestamp,
            "enable_count": len(enable_list),
            "disable_count": len(disable_list),
            "enabled_accounts": enable_list,
            "disabled_accounts": disable_list,
        }

    # Step 6b: Execute mode
    ce_client = _get_client("ce")
    dynamodb = boto3.resource("dynamodb", config=boto_config)
    audit_table = dynamodb.Table(AUDIT_TABLE_NAME)

    try:
        # Capture snapshot before modification
        snapshot = capture_cost_category_snapshot(
            ce_client, audit_table, COST_CATEGORY_ARN
        )
        snapshot_id = snapshot["SK"]  # ISO timestamp
        logger.info(f"Cost Category snapshot captured: {snapshot_id}")

        # Extract previous state for rollback capability (AUDIT-04)
        previous_state = extract_previous_state(snapshot)
        logger.info(
            "Previous RISP sharing state extracted",
            extra={
                "previous_enabled_count": len(previous_state["enabled"]),
                "previous_disabled_count": len(previous_state["disabled"]),
            }
        )

        # Update Cost Category definition
        update_response = update_risp_sharing_groups(
            ce_client,
            COST_CATEGORY_ARN,
            "RISP_Sharing_Groups",
            enable_list,
            disable_list,
            dry_run=False,
        )

        effective_start = update_response.get("EffectiveStart", "")
        logger.info(
            f"Cost Category updated successfully, effective_start={effective_start}"
        )

        # Write audit record with full context (AUDIT-01 through AUDIT-04)
        write_enforcement_audit_record(
            audit_table=audit_table,
            timestamp=timestamp,
            dry_run=False,
            cost_category_arn=COST_CATEGORY_ARN,
            enable_list=enable_list,
            disable_list=disable_list,
            execution_result="SUCCESS",
            snapshot_id=snapshot_id,
            effective_start=effective_start,
            previous_state=previous_state,
        )

        return {
            "status": "EXECUTED",
            "timestamp": timestamp,
            "enable_count": len(enable_list),
            "disable_count": len(disable_list),
            "effective_start": effective_start,
            "snapshot_id": snapshot_id,
        }

    except Exception as e:
        error_msg = f"Failed to update Cost Category: {str(e)}"
        logger.exception("Enforcement failed", extra={"error": str(e)})

        # Write error audit record (AUDIT-02, AUDIT-03)
        try:
            write_enforcement_audit_record(
                audit_table=audit_table,
                timestamp=timestamp,
                dry_run=False,
                cost_category_arn=COST_CATEGORY_ARN,
                enable_list=enable_list,
                disable_list=disable_list,
                execution_result="ERROR",
                error_message=str(e),
            )
        except Exception as audit_err:
            logger.error("Failed to write error audit record", extra={"audit_error": str(audit_err)})

        return {
            "status": "ERROR",
            "timestamp": timestamp,
            "message": error_msg,
        }
