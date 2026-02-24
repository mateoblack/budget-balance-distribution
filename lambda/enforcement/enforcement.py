"""Enforcement action determination logic."""
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


def determine_enforcement_actions(
    per_account_usage: list[dict],
    account_thresholds: dict[str, Decimal],
    account_re_enable_thresholds: dict[str, Decimal] | None = None,
) -> dict[str, list[str]]:
    """
    Compare per-account discount consumption against thresholds.

    Supports hysteresis band: accounts use disable_threshold to determine
    when to disable, and re_enable_threshold (lower) to determine when
    to re-enable. This prevents oscillation for borderline accounts.

    Logic:
    - If consumption > disable_threshold: add to disable list
    - If consumption <= re_enable_threshold (or disable_threshold if not set): add to enable list
    - If no threshold found for account: skip (log warning, exclude from both lists)

    Args:
        per_account_usage: List of dicts with account_id and estimated_discount_benefit
        account_thresholds: Dict mapping account_id -> disable threshold (Decimal)
        account_re_enable_thresholds: Optional dict mapping account_id -> re-enable threshold.
            If None or key missing, re-enable threshold equals disable threshold (current behavior).

    Returns:
        Dict with "enable" and "disable" keys, each containing list of account IDs
    """
    enable_accounts = []
    disable_accounts = []
    re_enable_map = account_re_enable_thresholds or {}

    for account_data in per_account_usage:
        account_id = account_data["account_id"]
        discount_consumption = Decimal(str(account_data["estimated_discount_benefit"]))

        # Get disable threshold for this account
        disable_threshold = account_thresholds.get(account_id)
        if disable_threshold is None:
            logger.warning(
                f"No threshold found for account {account_id}, excluding from enforcement"
            )
            continue

        # Get re-enable threshold (falls back to disable threshold if not set)
        re_enable_threshold = re_enable_map.get(account_id, disable_threshold)

        # Apply hysteresis: disable if over disable_threshold, enable if under re_enable_threshold
        if discount_consumption > disable_threshold:
            disable_accounts.append(account_id)
            logger.info(
                f"Account {account_id} over threshold: {float(discount_consumption)} > {float(disable_threshold)}"
            )
        elif discount_consumption <= re_enable_threshold:
            enable_accounts.append(account_id)
            logger.debug(
                f"Account {account_id} within re-enable threshold: {float(discount_consumption)} <= {float(re_enable_threshold)}"
            )
        else:
            # Account is between re_enable_threshold and disable_threshold — neither enable nor disable
            logger.info(
                f"Account {account_id} in hysteresis band: {float(re_enable_threshold)} < "
                f"{float(discount_consumption)} <= {float(disable_threshold)} — no action"
            )

    return {
        "enable": enable_accounts,
        "disable": disable_accounts,
    }
