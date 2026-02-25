"""Enforcement audit record persistence for DynamoDB."""
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def write_enforcement_audit_record(
    audit_table,
    timestamp: str,
    dry_run: bool,
    cost_category_arn: str,
    enable_list: list[str],
    disable_list: list[str],
    execution_result: str,
    snapshot_id: Optional[str] = None,
    effective_start: Optional[str] = None,
    error_message: Optional[str] = None,
    previous_state: Optional[dict] = None,
    data_as_of: str = "",
) -> dict:
    """
    Write immutable enforcement audit record to DynamoDB.

    Creates append-only records for compliance. Captures full enforcement
    context including execution mode, result, and previous state for rollback.

    Args:
        audit_table: boto3 DynamoDB Table resource
        timestamp: ISO 8601 timestamp (used as SK)
        dry_run: True if dry-run mode
        cost_category_arn: Cost Category ARN targeted
        enable_list: Account IDs enabled for RISP sharing
        disable_list: Account IDs disabled from RISP sharing
        execution_result: "SUCCESS", "ERROR", or "DRY_RUN"
        snapshot_id: SK of Cost Category snapshot (execute-success only)
        effective_start: AWS effective start timestamp (execute-success only)
        error_message: Error details (execute-error only)
        previous_state: Dict with "enabled" and "disabled" account lists (execute mode only)
        data_as_of: Freshness date of CE billing data used for this enforcement run (YYYY-MM-DD).
            Empty string when unknown. Propagated from S3 artifact or CE fallback conservative bound.

    Returns:
        The audit record dict that was written
    """
    audit_record = {
        "PK": "ENFORCEMENT_ACTION",
        "SK": timestamp,
        "entity_type": "ENFORCEMENT",
        "cost_category_arn": cost_category_arn,
        "execution_mode": "DRY_RUN" if dry_run else "EXECUTE",
        "execution_result": execution_result,
        "enable_count": len(enable_list),
        "disable_count": len(disable_list),
        "enabled_accounts": enable_list,
        "disabled_accounts": disable_list,
        "executed_at": timestamp,
        "data_as_of": data_as_of,  # Freshness timestamp of CE billing data used for this enforcement run
    }

    # Compute data_age_hours when data_as_of is provided (aids human readability and CloudWatch alarming)
    if data_as_of:
        try:
            data_as_of_dt = datetime.fromisoformat(data_as_of + "T00:00:00+00:00")
            data_age_hours = int((datetime.now(timezone.utc) - data_as_of_dt).total_seconds() / 3600)
            audit_record["data_age_hours"] = data_age_hours
        except (ValueError, TypeError):
            pass  # Don't fail audit write if data_as_of is malformed

    # Add execute-success specific fields
    if snapshot_id:
        audit_record["snapshot_id"] = snapshot_id
    if effective_start:
        audit_record["effective_start"] = effective_start

    # Add previous state for rollback capability (AUDIT-04)
    if previous_state:
        audit_record["previous_enabled_accounts"] = previous_state.get("enabled", [])
        audit_record["previous_disabled_accounts"] = previous_state.get("disabled", [])

    # Add error details
    if error_message:
        audit_record["error_message"] = error_message

    audit_table.put_item(Item=audit_record)

    logger.info(
        "Enforcement audit record written: %s mode, %s result",
        audit_record["execution_mode"],
        audit_record["execution_result"],
    )

    return audit_record


def write_account_disable_state(
    audit_table,
    account_id: str,
    disabled_month: str,
    disabled_at: str,
) -> None:
    """
    Persist the billing month an account was disabled for calendar-based re-enable gating.

    Called after a successful execute-mode disable. Allows subsequent enforcement
    runs to know which month each account was disabled in, so calendar-gated accounts
    stay out for the remainder of the billing cycle.

    Args:
        audit_table: boto3 DynamoDB Table resource
        account_id: AWS account ID
        disabled_month: Billing month the account was disabled (YYYY-MM)
        disabled_at: ISO 8601 timestamp of the enforcement run that disabled the account
    """
    audit_table.put_item(Item={
        "PK": "ACCOUNT_DISABLE_STATE",
        "SK": account_id,
        "account_id": account_id,
        "disabled_month": disabled_month,
        "disabled_at": disabled_at,
        "entity_type": "ACCOUNT_DISABLE_STATE",
    })
    logger.debug("Wrote account disable state: %s → %s", account_id, disabled_month)


def clear_account_disable_state(audit_table, account_id: str) -> None:
    """
    Remove the disable state record for an account that has been re-enabled.

    Called after a successful execute-mode re-enable. Clears the calendar gate
    so the account can be disabled again if it exceeds its threshold in a future month.

    Args:
        audit_table: boto3 DynamoDB Table resource
        account_id: AWS account ID to clear
    """
    audit_table.delete_item(Key={"PK": "ACCOUNT_DISABLE_STATE", "SK": account_id})
    logger.debug("Cleared account disable state for %s", account_id)


def load_disabled_months(audit_table, account_ids: list[str]) -> dict[str, str]:
    """
    Load the disable month for each account from DynamoDB.

    Used by enforcement logic to apply calendar-based re-enable gating.
    Only returns entries for accounts that have an active disable state record.

    Args:
        audit_table: boto3 DynamoDB Table resource
        account_ids: List of account IDs to look up

    Returns:
        Dict mapping account_id -> "YYYY-MM" for accounts with a disable state record.
        Accounts without a record are absent from the result.
    """
    result = {}
    for account_id in account_ids:
        response = audit_table.get_item(
            Key={"PK": "ACCOUNT_DISABLE_STATE", "SK": account_id}
        )
        item = response.get("Item")
        if item and "disabled_month" in item:
            result[account_id] = item["disabled_month"]
    if result:
        logger.info("Loaded disable state for %d account(s)", len(result))
    return result
