"""Enforcement audit record persistence for DynamoDB."""
import logging
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
    }

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
