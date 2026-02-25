"""Enforcement action determination logic."""
import logging
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)


def _is_calendar_blocked(
    account_id: str,
    disabled_months: dict[str, str] | None,
    account_reenablement_strategies: dict[str, str] | None,
) -> bool:
    """Return True if account should stay disabled until next calendar month.

    An account is calendar-blocked when:
    - Its reenablement_strategy is "calendar" (default)
    - It has a disable state record in DynamoDB
    - The recorded disabled_month matches the current month
    """
    strategy = (account_reenablement_strategies or {}).get(account_id, "calendar")
    if strategy != "calendar":
        return False
    disabled_month = (disabled_months or {}).get(account_id)
    if not disabled_month:
        return False
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    return disabled_month == current_month


def determine_enforcement_actions(
    per_account_usage: list[dict],
    account_thresholds: dict[str, Decimal],
    account_re_enable_thresholds: dict[str, Decimal] | None = None,
    disabled_months: dict[str, str] | None = None,
    account_reenablement_strategies: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """
    Compare per-account discount consumption against thresholds.

    Supports hysteresis band: accounts use disable_threshold to determine
    when to disable, and re_enable_threshold (lower) to determine when
    to re-enable. This prevents oscillation for borderline accounts.

    Supports calendar-based re-enablement: when reenablement_strategy is "calendar"
    (the default), an account disabled in the current billing month stays disabled
    regardless of its current consumption level. Re-enablement is only eligible once
    a new calendar month begins.

    Logic:
    - If consumption > disable_threshold: add to disable list
    - If consumption <= re_enable_threshold (or disable_threshold if not set):
        - If calendar-gated (disabled this month): skip re-enable
        - Otherwise: add to enable list
    - If no threshold found for account: skip (log warning, exclude from both lists)

    Args:
        per_account_usage: List of dicts with account_id and estimated_discount_benefit
        account_thresholds: Dict mapping account_id -> disable threshold (Decimal)
        account_re_enable_thresholds: Optional dict mapping account_id -> re-enable threshold.
            If None or key missing, re-enable threshold equals disable threshold (current behavior).
        disabled_months: Optional dict mapping account_id -> "YYYY-MM" of the month it was
            disabled. Used for calendar-based re-enablement gating.
        account_reenablement_strategies: Optional dict mapping account_id -> strategy
            ("calendar" or "consumption"). Defaults to "calendar" for all accounts.

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
            if _is_calendar_blocked(account_id, disabled_months, account_reenablement_strategies):
                logger.info(
                    f"Account {account_id} calendar-gated: disabled this billing month, "
                    f"skipping re-enable until next month"
                )
            else:
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
