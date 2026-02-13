"""Threshold calculation engine with fair-share, absolute, and percentage strategies."""
import logging
from decimal import Decimal
from shared.models import AccountConfig, SpendingGroup, ThresholdConfig

# Use standard Python logging (not Powertools - this is shared code)
logger = logging.getLogger(__name__)


def calculate_fair_share_threshold(group: SpendingGroup, active_account_count: int) -> Decimal:
    """
    Calculate fair-share threshold for a group.

    Formula: group.total_budget / active_account_count

    Args:
        group: Spending group configuration
        active_account_count: Number of active accounts in the group

    Returns:
        Fair-share threshold amount (Decimal)
    """
    # Handle edge cases
    if active_account_count <= 0:
        logger.warning(
            f"Fair-share calculation for group {group.group_id}: zero active accounts, returning 0"
        )
        return Decimal("0")

    if group.total_budget <= 0:
        logger.warning(
            f"Fair-share calculation for group {group.group_id}: zero or negative budget, returning 0"
        )
        return Decimal("0")

    # Calculate fair share
    fair_share = group.total_budget / Decimal(active_account_count)
    return fair_share


def calculate_threshold_for_group(
    threshold: ThresholdConfig, group: SpendingGroup, active_account_count: int
) -> Decimal:
    """
    Calculate threshold for a group based on threshold type.

    Dispatches to appropriate calculation based on threshold_type:
    - "absolute": returns threshold.absolute_amount
    - "percentage": returns group.total_budget * (threshold.percentage_value / 100)
    - "fair_share": calls calculate_fair_share_threshold

    Args:
        threshold: Threshold configuration
        group: Spending group configuration
        active_account_count: Number of active accounts in the group

    Returns:
        Threshold amount (Decimal)
    """
    if threshold.threshold_type == "absolute":
        return threshold.absolute_amount

    elif threshold.threshold_type == "percentage":
        percentage_decimal = threshold.percentage_value / Decimal("100")
        return group.total_budget * percentage_decimal

    elif threshold.threshold_type == "fair_share":
        return calculate_fair_share_threshold(group, active_account_count)

    else:
        logger.error(f"Unknown threshold type: {threshold.threshold_type}")
        return Decimal("0")


def calculate_effective_threshold(
    account: AccountConfig,
    groups: list[SpendingGroup],
    thresholds: list[ThresholdConfig],
    all_accounts: list[AccountConfig],
) -> Decimal:
    """
    Calculate the effective threshold for an account using most-restrictive-wins logic.

    For each group the account belongs to:
    1. Find matching group object
    2. Find matching threshold config for that group
    3. Count active accounts in that group
    4. Calculate threshold using calculate_threshold_for_group
    5. Return minimum (most restrictive) across all groups

    Args:
        account: Account configuration
        groups: List of all spending groups
        thresholds: List of all threshold configurations
        all_accounts: List of all accounts (for counting active members)

    Returns:
        Effective threshold amount (Decimal) - minimum across all groups
    """
    effective_thresholds = []

    # Build lookup maps for efficiency
    group_map = {g.group_id: g for g in groups}
    threshold_map = {t.group_id: t for t in thresholds}

    for group_id in account.group_memberships:
        # Check if group exists
        if group_id not in group_map:
            logger.error(
                f"Account {account.account_id} references non-existent group {group_id}, skipping"
            )
            continue

        group = group_map[group_id]

        # Check if threshold config exists for this group
        if group_id not in threshold_map:
            logger.warning(
                f"No threshold configuration found for group {group_id}, skipping"
            )
            continue

        threshold = threshold_map[group_id]

        # Count active accounts in this group
        active_account_count = sum(
            1
            for acc in all_accounts
            if acc.active and group_id in acc.group_memberships
        )

        # Calculate threshold for this group
        group_threshold = calculate_threshold_for_group(
            threshold, group, active_account_count
        )

        effective_thresholds.append(group_threshold)
        logger.debug(
            f"Account {account.account_id} in group {group_id}: threshold={group_threshold}"
        )

    # Return minimum (most restrictive) threshold
    if not effective_thresholds:
        logger.error(
            f"No valid thresholds found for account {account.account_id}, returning 0"
        )
        return Decimal("0")

    min_threshold = min(effective_thresholds)
    logger.info(
        f"Effective threshold for account {account.account_id}: {min_threshold} "
        f"(from {len(effective_thresholds)} groups)"
    )
    return min_threshold
