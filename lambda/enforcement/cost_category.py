"""Cost Category management for RISP sharing groups."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def build_cost_category_rules(
    enabled_accounts: list[str], disabled_accounts: list[str]
) -> list[dict]:
    """
    Build Cost Category rule dicts for UpdateCostCategoryDefinition API.

    Creates rules with LINKED_ACCOUNT dimension filters:
    - Rule 1 (if enabled_accounts): RISP_ENABLED value with enabled account IDs
    - Rule 2 (if disabled_accounts): RISP_DISABLED value with disabled account IDs

    Args:
        enabled_accounts: List of account IDs to enable for RISP sharing
        disabled_accounts: List of account IDs to disable from RISP sharing

    Returns:
        List of rule dictionaries with Value, Rule, and Type keys
        Returns empty list if both inputs are empty
    """
    rules = []

    # Rule 1: Accounts allowed to share RISP discounts
    if enabled_accounts:
        rules.append({
            "Value": "RISP_ENABLED",
            "Rule": {
                "Dimensions": {
                    "Key": "LINKED_ACCOUNT",
                    "Values": enabled_accounts,
                }
            },
            "Type": "REGULAR",
        })

    # Rule 2: Accounts excluded from RISP sharing
    if disabled_accounts:
        rules.append({
            "Value": "RISP_DISABLED",
            "Rule": {
                "Dimensions": {
                    "Key": "LINKED_ACCOUNT",
                    "Values": disabled_accounts,
                }
            },
            "Type": "REGULAR",
        })

    return rules


def capture_cost_category_snapshot(
    ce_client, audit_table, cost_category_arn: str
) -> dict:
    """
    Capture current Cost Category state before modification for rollback capability.

    Args:
        ce_client: boto3 Cost Explorer client
        audit_table: boto3 DynamoDB Table resource
        cost_category_arn: Cost Category ARN to snapshot

    Returns:
        Snapshot dictionary with PK, SK, entity_type, and Cost Category fields
    """
    # Fetch current definition
    response = ce_client.describe_cost_category_definition(
        CostCategoryArn=cost_category_arn
    )

    cost_category = response.get("CostCategory", {})

    # Build snapshot record
    timestamp = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "PK": f"SNAPSHOT#{cost_category_arn}",
        "SK": timestamp,
        "entity_type": "COST_CATEGORY_SNAPSHOT",
        "cost_category_arn": cost_category_arn,
        "name": cost_category.get("Name", ""),
        "rule_version": cost_category.get("RuleVersion", ""),
        "rules": cost_category.get("Rules", []),
        "default_value": cost_category.get("DefaultValue"),
        "effective_start": cost_category.get("EffectiveStart", ""),
        "captured_at": timestamp,
    }

    # Persist to audit table
    audit_table.put_item(Item=snapshot)

    logger.info(
        f"Cost Category snapshot captured: {snapshot['SK']}, {len(snapshot['rules'])} rules"
    )

    return snapshot


def extract_previous_state(snapshot: dict) -> dict:
    """
    Extract enabled/disabled account lists from a Cost Category snapshot.

    Parses the rules from a snapshot captured by capture_cost_category_snapshot()
    to determine which accounts were in RISP_ENABLED vs RISP_DISABLED groups
    before enforcement changes.

    Args:
        snapshot: Snapshot dict from capture_cost_category_snapshot()
                  (contains "rules" key with Cost Category rule list)

    Returns:
        Dict with "enabled" and "disabled" keys, each containing list of account IDs.
        Returns empty lists if no matching rules found.
    """
    rules = snapshot.get("rules", [])
    enabled = []
    disabled = []

    for rule in rules:
        rule_value = rule.get("Value", "")
        dimensions = rule.get("Rule", {}).get("Dimensions", {})
        account_values = dimensions.get("Values", [])

        if rule_value == "RISP_ENABLED":
            enabled.extend(account_values)
        elif rule_value == "RISP_DISABLED":
            disabled.extend(account_values)

    return {"enabled": enabled, "disabled": disabled}


def update_risp_sharing_groups(
    ce_client,
    cost_category_arn: str,
    cost_category_name: str,
    enabled_accounts: list[str],
    disabled_accounts: list[str],
    dry_run: bool = True,
) -> dict:
    """
    Update Cost Category definition to enforce RISP sharing groups.

    Args:
        ce_client: boto3 Cost Explorer client
        cost_category_arn: Cost Category ARN to update
        cost_category_name: Cost Category name (for logging)
        enabled_accounts: List of account IDs to enable for RISP sharing
        disabled_accounts: List of account IDs to disable from RISP sharing
        dry_run: If True, log intended changes without executing (default: True)

    Returns:
        If dry_run: dict with status="DRY_RUN", rules, enabled_count, disabled_count
        If execute: dict from update_cost_category_definition API response
    """
    rules = build_cost_category_rules(enabled_accounts, disabled_accounts)

    if dry_run:
        logger.info(
            f"DRY-RUN: Would update Cost Category {cost_category_name} "
            f"({len(enabled_accounts)} enabled, {len(disabled_accounts)} disabled)"
        )
        return {
            "status": "DRY_RUN",
            "rules": rules,
            "enabled_count": len(enabled_accounts),
            "disabled_count": len(disabled_accounts),
        }

    # Execute actual update
    response = ce_client.update_cost_category_definition(
        CostCategoryArn=cost_category_arn,
        RuleVersion="CostCategoryExpression.v1",
        Rules=rules,
        DefaultValue="RISP_DISABLED",  # Fail-closed: new accounts excluded by default
    )

    logger.info(
        f"Cost Category updated: {cost_category_arn}, "
        f"effective_start={response.get('EffectiveStart')}"
    )

    return response
