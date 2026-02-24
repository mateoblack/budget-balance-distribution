"""
restore-risp-state.py — Restore RISP Cost Category state from a DynamoDB audit snapshot.

Usage:
    python scripts/restore-risp-state.py \
        --snapshot-id 2026-02-23T02:30:00Z \
        --cost-category-arn arn:aws:ce::123456789012:costcategory/xxx

    # Dry-run by default — add --execute to apply changes
    python scripts/restore-risp-state.py \
        --snapshot-id 2026-02-23T02:30:00Z \
        --cost-category-arn arn:aws:ce::123456789012:costcategory/xxx \
        --audit-table my-audit-table \
        --execute

The --snapshot-id is the DynamoDB Sort Key (SK) for the ENFORCEMENT_ACTION record
you want to restore from. You can find it by querying the audit table:

    aws dynamodb query \
        --table-name [AUDIT_TABLE] \
        --key-condition-expression "PK = :pk" \
        --expression-attribute-values '{":pk": {"S": "ENFORCEMENT_ACTION"}}' \
        --scan-index-forward false \
        --limit 5

This script reads the previous_state.enabled field from that record and restores
the Cost Category definition to include those accounts in the RISP_ENABLED rule.
"""

import json
import logging
import os
import sys

import boto3
import click
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("restore-risp-state")

DEFAULT_AUDIT_TABLE_ENV = "AUDIT_TABLE_NAME"


def get_audit_table_name(audit_table: str | None) -> str:
    """Resolve the DynamoDB audit table name from argument or environment."""
    if audit_table:
        return audit_table
    env_value = os.environ.get(DEFAULT_AUDIT_TABLE_ENV, "")
    if env_value:
        return env_value
    raise click.ClickException(
        f"--audit-table not provided and {DEFAULT_AUDIT_TABLE_ENV} environment variable is not set. "
        "Pass --audit-table or set the environment variable."
    )


def fetch_snapshot(table_name: str, snapshot_id: str) -> dict:
    """Fetch the enforcement audit record from DynamoDB by snapshot ID (SK)."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table = dynamodb.Table(table_name)

    logger.info("Fetching snapshot from DynamoDB: table=%s, SK=%s", table_name, snapshot_id)

    try:
        response = table.get_item(
            Key={
                "PK": "ENFORCEMENT_ACTION",
                "SK": snapshot_id,
            }
        )
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        raise click.ClickException(
            f"DynamoDB error fetching snapshot: {error_code} — {error_message}"
        ) from exc

    item = response.get("Item")
    if item is None:
        raise click.ClickException(
            f"No audit record found with PK=ENFORCEMENT_ACTION, SK={snapshot_id}. "
            "Verify the snapshot-id is correct by querying the audit table directly."
        )

    return item


def extract_previous_state(record: dict) -> list[str]:
    """Extract the list of previously enabled account IDs from an audit record."""
    previous_state = record.get("previous_state")
    if previous_state is None:
        raise click.ClickException(
            "Audit record does not contain a 'previous_state' field. "
            "This record may be from an older schema version that did not capture previous state."
        )

    enabled_accounts = previous_state.get("enabled")
    if enabled_accounts is None:
        raise click.ClickException(
            "Audit record 'previous_state' does not contain an 'enabled' field. "
            "Cannot determine which accounts to restore."
        )

    if not isinstance(enabled_accounts, list):
        raise click.ClickException(
            f"Expected 'previous_state.enabled' to be a list, got: {type(enabled_accounts).__name__}"
        )

    return enabled_accounts


def build_cost_category_rules(enabled_accounts: list[str]) -> list[dict]:
    """Build the Cost Category rules payload to restore RISP_ENABLED accounts."""
    return [
        {
            "Value": "RISP_ENABLED",
            "Rule": {
                "Dimensions": {
                    "Key": "LINKED_ACCOUNT",
                    "Values": enabled_accounts,
                }
            },
            "Type": "REGULAR",
        }
    ]


def print_dry_run_summary(
    cost_category_arn: str,
    enabled_accounts: list[str],
    rules: list[dict],
) -> None:
    """Print what would be done in dry-run mode."""
    click.echo("")
    click.echo("DRY-RUN MODE — no changes will be made (add --execute to apply)")
    click.echo("=" * 60)
    click.echo(f"Cost Category ARN: {cost_category_arn}")
    click.echo(f"Accounts to restore to RISP_ENABLED ({len(enabled_accounts)}):")
    for account_id in enabled_accounts:
        click.echo(f"  - {account_id}")
    click.echo("")
    click.echo("Equivalent AWS CLI command:")
    click.echo("")
    click.echo("  aws ce update-cost-category-definition \\")
    click.echo(f'    --cost-category-arn "{cost_category_arn}" \\')
    click.echo("    --rule-version CostCategoryExpression.v1 \\")
    click.echo(f"    --rules '{json.dumps(rules)}' \\")
    click.echo('    --default-value "RISP_DISABLED"')
    click.echo("")


def apply_restoration(cost_category_arn: str, rules: list[dict]) -> None:
    """Call ce:UpdateCostCategoryDefinition to restore the previous state."""
    ce_client = boto3.client("ce", region_name="us-east-1")

    logger.info("Applying Cost Category restoration: arn=%s", cost_category_arn)

    try:
        response = ce_client.update_cost_category_definition(
            CostCategoryArn=cost_category_arn,
            RuleVersion="CostCategoryExpression.v1",
            Rules=rules,
            DefaultValue="RISP_DISABLED",
        )
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        raise click.ClickException(
            f"Cost Explorer API error: {error_code} — {error_message}\n"
            "Verify the Cost Category ARN is correct and you have ce:UpdateCostCategoryDefinition permission."
        ) from exc

    effective_on = response.get("EffectiveEnd", "unknown")
    logger.info("Cost Category updated successfully. Effective: %s", effective_on)


@click.command()
@click.option(
    "--snapshot-id",
    required=True,
    help="DynamoDB Sort Key (SK) of the ENFORCEMENT_ACTION audit record to restore from. "
    "Format: ISO 8601 timestamp (e.g. 2026-02-23T02:30:00Z).",
)
@click.option(
    "--cost-category-arn",
    required=True,
    help="ARN of the Cost Category to restore (arn:aws:ce::ACCOUNT_ID:costcategory/ID). "
    "Use arn:aws: format, not arn:aws-us-gov:.",
)
@click.option(
    "--audit-table",
    default=None,
    help=f"DynamoDB audit table name. Defaults to ${DEFAULT_AUDIT_TABLE_ENV} environment variable.",
)
@click.option(
    "--execute",
    is_flag=True,
    default=False,
    help="Apply the restoration. Without this flag, runs in dry-run mode and prints what would change.",
)
def main(snapshot_id: str, cost_category_arn: str, audit_table: str | None, execute: bool) -> None:
    """Restore RISP Cost Category state from a DynamoDB audit snapshot.

    Reads the previous_state.enabled field from the audit record identified by
    --snapshot-id and restores those accounts to the RISP_ENABLED Cost Category rule.

    Runs in dry-run mode by default. Add --execute to apply changes.
    """
    # Resolve audit table name
    resolved_table = get_audit_table_name(audit_table)

    # Fetch the audit record
    record = fetch_snapshot(resolved_table, snapshot_id)

    # Log the record metadata for operator visibility
    executed_at = record.get("executed_at", "unknown")
    execution_mode = record.get("execution_mode", "unknown")
    logger.info(
        "Found snapshot: executed_at=%s, mode=%s",
        executed_at,
        execution_mode,
    )

    # Extract previous enabled accounts
    enabled_accounts = extract_previous_state(record)
    logger.info("Previous state has %d enabled accounts", len(enabled_accounts))

    if not enabled_accounts:
        click.echo(
            "WARNING: Previous state has 0 enabled accounts. "
            "Restoring would set all accounts to RISP_DISABLED. "
            "Verify this is correct before proceeding with --execute."
        )

    # Build the rules payload
    rules = build_cost_category_rules(enabled_accounts)

    if not execute:
        print_dry_run_summary(cost_category_arn, enabled_accounts, rules)
        click.echo("Add --execute to apply the restoration.")
        sys.exit(0)

    # Confirm before applying
    click.echo(f"Restoring {len(enabled_accounts)} accounts to RISP_ENABLED...")
    for account_id in enabled_accounts:
        click.echo(f"  + {account_id}")
    click.echo("")

    apply_restoration(cost_category_arn, rules)

    click.echo("")
    click.echo("Restoration applied successfully.")
    click.echo(
        "Note: Cost Category changes propagate within hours. "
        "Monitor the CloudWatch dashboard for compliance status changes."
    )


if __name__ == "__main__":
    main()
