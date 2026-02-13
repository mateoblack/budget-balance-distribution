"""Enforcement action determination logic."""
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


def determine_enforcement_actions(
    per_account_usage: list[dict], account_thresholds: dict[str, Decimal]
) -> dict[str, list[str]]:
    """
    Compare per-account discount consumption against thresholds.

    Determines which accounts should have RISP sharing enabled or disabled
    based on whether their estimated_discount_benefit exceeds their threshold.

    Logic:
    - If consumption > threshold: add to disable list
    - If consumption <= threshold: add to enable list
    - If no threshold found for account: skip (log warning, exclude from both lists)

    Args:
        per_account_usage: List of dicts with account_id and estimated_discount_benefit
        account_thresholds: Dict mapping account_id -> threshold (Decimal)

    Returns:
        Dict with "enable" and "disable" keys, each containing list of account IDs
    """
    enable_accounts = []
    disable_accounts = []

    for account_data in per_account_usage:
        account_id = account_data["account_id"]
        discount_consumption = Decimal(str(account_data["estimated_discount_benefit"]))

        # Get threshold for this account (may be None if not in config)
        threshold = account_thresholds.get(account_id)
        if threshold is None:
            logger.warning(
                f"No threshold found for account {account_id}, excluding from enforcement"
            )
            continue

        # Compare consumption vs threshold
        if discount_consumption > threshold:
            disable_accounts.append(account_id)
            logger.info(
                f"Account {account_id} over threshold: {float(discount_consumption)} > {float(threshold)}"
            )
        else:
            enable_accounts.append(account_id)
            logger.debug(
                f"Account {account_id} within threshold: {float(discount_consumption)} <= {float(threshold)}"
            )

    return {
        "enable": enable_accounts,
        "disable": disable_accounts,
    }
