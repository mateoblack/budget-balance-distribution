"""Config loader with read-time validation for Lambda consumption."""
import logging
from decimal import Decimal
from typing import Optional
from pydantic import ValidationError
from shared.dynamo_client import ConfigDynamoClient
from shared.models import AccountConfig, SpendingGroup, ThresholdConfig
from shared.threshold import calculate_effective_threshold

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Exception raised when configuration validation fails."""

    def __init__(self, errors: list[dict]):
        """
        Initialize ConfigValidationError.

        Args:
            errors: List of error dictionaries with entity_type, entity_id, field, message
        """
        self.errors = errors
        error_summary = "\n".join([
            f"  - {e['entity_type']} {e['entity_id']}: {e.get('field', 'N/A')} - {e['message']}"
            for e in errors
        ])
        super().__init__(f"Configuration validation failed:\n{error_summary}")


def load_all_config(table_name: str, dynamodb_resource=None) -> dict:
    """
    Load all configuration entities from DynamoDB with validation.

    Reads all groups, accounts, and thresholds from DynamoDB, validates each with
    Pydantic models, then performs cross-entity referential integrity checks.

    Fails fast on invalid data: if ANY item fails validation, raises ConfigValidationError
    with all failures collected (doesn't stop at first).

    Args:
        table_name: DynamoDB table name
        dynamodb_resource: Optional boto3 DynamoDB resource (for testing)

    Returns:
        Dictionary with keys: 'groups', 'accounts', 'thresholds'
        Each value is a list of validated Pydantic models

    Raises:
        ConfigValidationError: If any validation fails
    """
    client = ConfigDynamoClient(table_name, boto3_resource=dynamodb_resource)
    validation_errors = []

    # Load and validate groups
    groups = []
    try:
        raw_groups = client.list_groups()
        groups = raw_groups
        logger.info(f"Loaded {len(groups)} groups")
    except ValidationError as e:
        for error in e.errors():
            validation_errors.append({
                'entity_type': 'GROUP',
                'entity_id': 'unknown',
                'field': '.'.join(str(loc) for loc in error['loc']),
                'message': error['msg']
            })
        logger.error(f"Group validation failed: {len(e.errors())} errors")
    except Exception as e:
        validation_errors.append({
            'entity_type': 'GROUP',
            'entity_id': 'unknown',
            'field': 'load',
            'message': str(e)
        })
        logger.error(f"Failed to load groups: {e}")

    # Load and validate accounts
    accounts = []
    try:
        raw_accounts = client.list_all_accounts()
        accounts = raw_accounts
        logger.info(f"Loaded {len(accounts)} accounts")
    except ValidationError as e:
        for error in e.errors():
            validation_errors.append({
                'entity_type': 'ACCOUNT',
                'entity_id': 'unknown',
                'field': '.'.join(str(loc) for loc in error['loc']),
                'message': error['msg']
            })
        logger.error(f"Account validation failed: {len(e.errors())} errors")
    except Exception as e:
        validation_errors.append({
            'entity_type': 'ACCOUNT',
            'entity_id': 'unknown',
            'field': 'load',
            'message': str(e)
        })
        logger.error(f"Failed to load accounts: {e}")

    # Load and validate thresholds
    thresholds = []
    try:
        raw_thresholds = client.list_all_thresholds()
        thresholds = raw_thresholds
        logger.info(f"Loaded {len(thresholds)} thresholds")
    except ValidationError as e:
        for error in e.errors():
            validation_errors.append({
                'entity_type': 'THRESHOLD',
                'entity_id': 'unknown',
                'field': '.'.join(str(loc) for loc in error['loc']),
                'message': error['msg']
            })
        logger.error(f"Threshold validation failed: {len(e.errors())} errors")
    except Exception as e:
        validation_errors.append({
            'entity_type': 'THRESHOLD',
            'entity_id': 'unknown',
            'field': 'load',
            'message': str(e)
        })
        logger.error(f"Failed to load thresholds: {e}")

    # Fail fast if any validation errors
    if validation_errors:
        raise ConfigValidationError(validation_errors)

    # Perform cross-entity validation
    config = {
        'groups': groups,
        'accounts': accounts,
        'thresholds': thresholds
    }
    integrity_errors = validate_config_integrity(config)
    if integrity_errors:
        # Convert integrity errors to validation error format
        for error_msg in integrity_errors:
            validation_errors.append({
                'entity_type': 'CONFIG',
                'entity_id': 'integrity',
                'field': 'cross-entity',
                'message': error_msg
            })
        raise ConfigValidationError(validation_errors)

    logger.info("Configuration loaded and validated successfully")
    return config


def validate_config_integrity(config: dict) -> list[str]:
    """
    Perform cross-entity referential integrity checks.

    Checks:
    - Every account's group_memberships reference existing groups
    - Every threshold's group_id references an existing group
    - Every active group has at least one threshold defined
    - No orphaned membership records (implicitly checked by load_all_config)

    Args:
        config: Dictionary with 'groups', 'accounts', 'thresholds' keys

    Returns:
        List of warning/error strings (empty list if valid)
    """
    errors = []
    groups = config['groups']
    accounts = config['accounts']
    thresholds = config['thresholds']

    # Build lookup maps
    group_ids = {g.group_id for g in groups}
    active_group_ids = {g.group_id for g in groups if g.active}
    threshold_group_ids = {t.group_id for t in thresholds}

    # Check account group memberships reference existing groups
    for account in accounts:
        for group_id in account.group_memberships:
            if group_id not in group_ids:
                errors.append(
                    f"Account {account.account_id} references non-existent group: {group_id}"
                )
                logger.error(f"Account {account.account_id} references non-existent group: {group_id}")

    # Check threshold group references exist
    for threshold in thresholds:
        if threshold.group_id not in group_ids:
            errors.append(
                f"Threshold {threshold.threshold_id} references non-existent group: {threshold.group_id}"
            )
            logger.error(f"Threshold {threshold.threshold_id} references non-existent group: {threshold.group_id}")

    # Check every active group has at least one threshold
    for group_id in active_group_ids:
        if group_id not in threshold_group_ids:
            errors.append(
                f"Active group {group_id} has no threshold configuration"
            )
            logger.warning(f"Active group {group_id} has no threshold configuration")

    if not errors:
        logger.info("Configuration integrity checks passed")

    return errors


def get_account_thresholds(config: dict) -> dict[str, Decimal]:
    """
    Calculate effective threshold for each active account.

    Uses calculate_effective_threshold from shared.threshold module to apply
    most-restrictive-wins logic across all group memberships.

    Args:
        config: Dictionary with 'groups', 'accounts', 'thresholds' keys

    Returns:
        Dictionary mapping account_id -> effective threshold (Decimal)
        Only includes active accounts
    """
    groups = config['groups']
    accounts = config['accounts']
    thresholds = config['thresholds']

    account_thresholds = {}

    for account in accounts:
        if not account.active:
            logger.debug(f"Skipping inactive account {account.account_id}")
            continue

        effective_threshold = calculate_effective_threshold(
            account=account,
            groups=groups,
            thresholds=thresholds,
            all_accounts=accounts
        )

        account_thresholds[account.account_id] = effective_threshold
        logger.debug(f"Account {account.account_id} effective threshold: {effective_threshold}")

    logger.info(f"Calculated thresholds for {len(account_thresholds)} active accounts")
    return account_thresholds


def get_account_reenablement_strategies(config: dict) -> dict[str, str]:
    """
    Derive the effective reenablement strategy for each active account.

    Most-restrictive wins: if any threshold governing an account uses "calendar",
    the account gets "calendar". An account only gets "consumption" when every
    threshold across all of its group memberships explicitly opts in to "consumption".

    Args:
        config: Dictionary with 'groups', 'accounts', 'thresholds' keys

    Returns:
        Dictionary mapping account_id -> "calendar" or "consumption".
        Only includes active accounts.
    """
    accounts = config["accounts"]
    thresholds = config["thresholds"]

    # Build group_id -> list of ThresholdConfig
    group_thresholds: dict[str, list] = {}
    for t in thresholds:
        group_thresholds.setdefault(t.group_id, []).append(t)

    strategies = {}
    for account in accounts:
        if not account.active:
            continue
        # Collect strategies from every threshold in every group this account belongs to
        account_strategies = set()
        for group_id in account.group_memberships:
            for t in group_thresholds.get(group_id, []):
                account_strategies.add(t.reenablement_strategy)
        # All thresholds must explicitly use "consumption" to opt out of calendar gating
        if account_strategies and all(s == "consumption" for s in account_strategies):
            strategies[account.account_id] = "consumption"
        else:
            strategies[account.account_id] = "calendar"

    logger.info(
        "Reenablement strategies derived: %d calendar, %d consumption",
        sum(1 for s in strategies.values() if s == "calendar"),
        sum(1 for s in strategies.values() if s == "consumption"),
    )
    return strategies
