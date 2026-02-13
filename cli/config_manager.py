"""Click-based CLI for Budget Balance Distribution configuration management."""
import os
import sys
import json
import csv
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
import click
from pydantic import ValidationError
from shared.dynamo_client import ConfigDynamoClient, WriteResult
from shared.models import AccountConfig, SpendingGroup, ThresholdConfig
from cli.formatters import (
    print_group_table,
    print_account_table,
    print_threshold_table,
    print_config_summary,
    print_dry_run_warning,
    print_success,
    print_error,
    print_validation_errors,
    print_write_warnings,
    prompt_fix_validation_errors,
    console
)


def get_iso_timestamp() -> str:
    """Get current timestamp in ISO 8601 UTC format."""
    return datetime.now(timezone.utc).isoformat()


def log_audit(client: ConfigDynamoClient, audit_table: Optional[str], action: str,
               entity_type: str, entity_id: str, details: dict) -> None:
    """
    Log an action to the audit table.

    Args:
        client: DynamoDB client (uses same connection)
        audit_table: Audit table name (optional)
        action: CREATE, UPDATE, or DELETE
        entity_type: GROUP, ACCOUNT, or THRESHOLD
        entity_id: Entity identifier
        details: Dict of changes/details to log
    """
    if not audit_table:
        return

    timestamp = get_iso_timestamp()
    actor = os.environ.get('USER', 'unknown')

    audit_item = {
        'PK': f'AUDIT#{timestamp}',
        'SK': f'{entity_type}#{entity_id}',
        'action': action,
        'actor': actor,
        'entity_type': entity_type,
        'entity_id': entity_id,
        'details': json.dumps(details),
        'timestamp': timestamp
    }

    try:
        # Use the same dynamodb resource to write to audit table
        audit_table_resource = client.dynamodb.Table(audit_table)
        audit_table_resource.put_item(Item=audit_item)
    except Exception as e:
        console.print(f"[yellow]Warning: Failed to write audit log: {e}[/yellow]")


def validate_with_retry(model_class, data: dict, entity_name: str) -> Optional[object]:
    """
    Validate data with Pydantic model, with interactive retry on errors.

    Args:
        model_class: Pydantic model class to validate with
        data: Dict of data to validate
        entity_name: Human-readable entity name for messages

    Returns:
        Validated model instance, or None if user aborted
    """
    while True:
        try:
            return model_class(**data)
        except ValidationError as e:
            errors = e.errors()
            print_validation_errors(errors)

            # Ask user if they want to fix interactively
            console.print(f"\n[yellow]Found {len(errors)} validation error(s) for {entity_name}[/yellow]")

            corrected = prompt_fix_validation_errors(errors, data)
            if corrected is None:
                # User aborted
                return None

            # Update data with corrected values and retry
            data.update(corrected)


@click.group()
@click.option('--table-name', envvar='CONFIG_TABLE_NAME', required=True,
              help='DynamoDB config table name')
@click.option('--audit-table', envvar='AUDIT_TABLE_NAME',
              help='DynamoDB audit table name for change tracking')
@click.pass_context
def cli(ctx, table_name, audit_table):
    """Budget Balance Distribution - Configuration Manager"""
    ctx.ensure_object(dict)
    ctx.obj['client'] = ConfigDynamoClient(table_name)
    ctx.obj['audit_table'] = audit_table
    ctx.obj['table_name'] = table_name


# ============================================================================
# GROUP COMMANDS
# ============================================================================

@cli.group()
def group():
    """Manage spending groups"""
    pass


@group.command('create')
@click.argument('group_id')
@click.option('--name', required=True, help='Human-readable group name')
@click.option('--budget', required=True, type=Decimal, help='Monthly budget in USD')
@click.option('--description', help='Optional group description')
@click.option('--execute', is_flag=True, help='Actually write to DynamoDB (default is dry-run)')
@click.pass_context
def group_create(ctx, group_id, name, budget, description, execute):
    """Create a new spending group"""
    client = ctx.obj['client']
    audit_table = ctx.obj['audit_table']

    # Prepare data
    now = get_iso_timestamp()
    data = {
        'group_id': group_id,
        'name': name,
        'description': description,
        'total_budget': budget,
        'active': True,
        'created_at': now,
        'updated_at': now
    }

    # Validate with interactive retry
    spending_group = validate_with_retry(SpendingGroup, data, f"group {group_id}")
    if spending_group is None:
        print_error("Operation cancelled")
        sys.exit(1)

    # Show what will be created
    console.print(f"\n[cyan]Creating spending group:[/cyan]")
    print_group_table([spending_group])

    if not execute:
        print_dry_run_warning()
        return

    # Execute write
    result = client.create_group(spending_group)

    if result.warnings:
        print_write_warnings(result)

    # Log to audit
    log_audit(client, audit_table, 'CREATE', 'GROUP', group_id, data)

    print_success(f"Created spending group: {group_id}")


@group.command('list')
@click.pass_context
def group_list(ctx):
    """List all spending groups"""
    client = ctx.obj['client']

    groups = client.list_groups()

    if not groups:
        console.print("[yellow]No spending groups found[/yellow]")
        return

    print_group_table(groups)


@group.command('update')
@click.argument('group_id')
@click.option('--name', help='Update human-readable group name')
@click.option('--budget', type=Decimal, help='Update monthly budget in USD')
@click.option('--description', help='Update group description')
@click.option('--active/--inactive', default=None, help='Set active status')
@click.option('--execute', is_flag=True, help='Actually write to DynamoDB (default is dry-run)')
@click.pass_context
def group_update(ctx, group_id, name, budget, description, active, execute):
    """Update an existing spending group"""
    client = ctx.obj['client']
    audit_table = ctx.obj['audit_table']

    # Get existing group
    existing = client.get_group(group_id)
    if existing is None:
        print_error(f"Group not found: {group_id}")
        sys.exit(1)

    # Apply updates
    data = {
        'group_id': existing.group_id,
        'name': name if name is not None else existing.name,
        'description': description if description is not None else existing.description,
        'total_budget': budget if budget is not None else existing.total_budget,
        'active': active if active is not None else existing.active,
        'created_at': existing.created_at,
        'updated_at': get_iso_timestamp()
    }

    # Validate with interactive retry
    updated_group = validate_with_retry(SpendingGroup, data, f"group {group_id}")
    if updated_group is None:
        print_error("Operation cancelled")
        sys.exit(1)

    # Show what will be updated
    console.print(f"\n[cyan]Updating spending group:[/cyan]")
    print_group_table([updated_group])

    if not execute:
        print_dry_run_warning()
        return

    # Execute write
    result = client.update_group(updated_group)

    if result.warnings:
        print_write_warnings(result)

    # Log to audit
    changes = {k: v for k, v in data.items() if k not in ['created_at', 'updated_at']}
    log_audit(client, audit_table, 'UPDATE', 'GROUP', group_id, changes)

    print_success(f"Updated spending group: {group_id}")


@group.command('delete')
@click.argument('group_id')
@click.option('--execute', is_flag=True, help='Actually write to DynamoDB (default is dry-run)')
@click.pass_context
def group_delete(ctx, group_id, execute):
    """Delete a spending group"""
    client = ctx.obj['client']
    audit_table = ctx.obj['audit_table']

    # Get existing group
    existing = client.get_group(group_id)
    if existing is None:
        print_error(f"Group not found: {group_id}")
        sys.exit(1)

    # Show what will be deleted
    console.print(f"\n[cyan]Group to delete:[/cyan]")
    print_group_table([existing])

    if not execute:
        print_dry_run_warning()
        return

    # Confirm deletion
    if not click.confirm(f"Delete group '{group_id}' and all memberships?"):
        print_error("Operation cancelled")
        sys.exit(1)

    # Execute delete
    result = client.delete_group(group_id)

    if result.warnings:
        print_write_warnings(result)

    # Log to audit
    log_audit(client, audit_table, 'DELETE', 'GROUP', group_id, {'group_id': group_id})

    print_success(f"Deleted spending group: {group_id}")


# ============================================================================
# ACCOUNT COMMANDS
# ============================================================================

@cli.group()
def account():
    """Manage accounts"""
    pass


@account.command('add')
@click.argument('account_id')
@click.option('--groups', required=True, help='Comma-separated list of group IDs')
@click.option('--name', help='Human-readable account name')
@click.option('--execute', is_flag=True, help='Actually write to DynamoDB (default is dry-run)')
@click.pass_context
def account_add(ctx, account_id, groups, name, execute):
    """Add a new account to spending groups"""
    client = ctx.obj['client']
    audit_table = ctx.obj['audit_table']

    # Parse groups
    group_list = [g.strip() for g in groups.split(',') if g.strip()]

    # Prepare data
    now = get_iso_timestamp()
    data = {
        'account_id': account_id,
        'account_name': name,
        'group_memberships': group_list,
        'active': True,
        'created_at': now,
        'updated_at': now
    }

    # Validate with interactive retry
    account_config = validate_with_retry(AccountConfig, data, f"account {account_id}")
    if account_config is None:
        print_error("Operation cancelled")
        sys.exit(1)

    # Check referential integrity (show warnings but allow)
    missing_groups = []
    for group_id in group_list:
        if client.get_group(group_id) is None:
            missing_groups.append(group_id)

    if missing_groups:
        console.print(f"[yellow]Warning: Referenced groups not found: {', '.join(missing_groups)}[/yellow]")
        console.print("[yellow]Account will be created, but these groups don't exist yet[/yellow]")

    # Show what will be created
    console.print(f"\n[cyan]Adding account:[/cyan]")
    print_account_table([account_config])

    if not execute:
        print_dry_run_warning()
        return

    # Execute write
    result = client.create_account(account_config)

    if result.warnings:
        print_write_warnings(result)

    # Log to audit
    log_audit(client, audit_table, 'CREATE', 'ACCOUNT', account_id, data)

    print_success(f"Added account: {account_id}")


@account.command('list')
@click.option('--group', help='Filter by group membership')
@click.pass_context
def account_list(ctx, group):
    """List all accounts"""
    client = ctx.obj['client']

    accounts = client.list_all_accounts()

    # Filter by group if specified
    if group:
        accounts = [a for a in accounts if group in a.group_memberships]

    if not accounts:
        console.print("[yellow]No accounts found[/yellow]")
        return

    print_account_table(accounts)


@account.command('remove')
@click.argument('account_id')
@click.option('--execute', is_flag=True, help='Actually write to DynamoDB (default is dry-run)')
@click.pass_context
def account_remove(ctx, account_id, execute):
    """Remove an account"""
    client = ctx.obj['client']
    audit_table = ctx.obj['audit_table']

    # Get existing account
    existing = client.get_account(account_id)
    if existing is None:
        print_error(f"Account not found: {account_id}")
        sys.exit(1)

    # Show what will be deleted
    console.print(f"\n[cyan]Account to remove:[/cyan]")
    print_account_table([existing])

    if not execute:
        print_dry_run_warning()
        return

    # Execute delete
    result = client.delete_account(account_id)

    if result.warnings:
        print_write_warnings(result)

    # Log to audit
    log_audit(client, audit_table, 'DELETE', 'ACCOUNT', account_id, {'account_id': account_id})

    print_success(f"Removed account: {account_id}")


@account.command('update')
@click.argument('account_id')
@click.option('--add-group', help='Add account to this group')
@click.option('--remove-group', help='Remove account from this group')
@click.option('--active/--inactive', default=None, help='Set active status')
@click.option('--execute', is_flag=True, help='Actually write to DynamoDB (default is dry-run)')
@click.pass_context
def account_update(ctx, account_id, add_group, remove_group, active, execute):
    """Update account group memberships or active status"""
    client = ctx.obj['client']
    audit_table = ctx.obj['audit_table']

    # Get existing account
    existing = client.get_account(account_id)
    if existing is None:
        print_error(f"Account not found: {account_id}")
        sys.exit(1)

    # Apply updates
    group_memberships = existing.group_memberships.copy()

    if add_group:
        if add_group not in group_memberships:
            group_memberships.append(add_group)

    if remove_group:
        if remove_group in group_memberships:
            group_memberships.remove(remove_group)

    # Validate at least one group membership
    if not group_memberships:
        print_error("Account must have at least one group membership")
        sys.exit(1)

    data = {
        'account_id': existing.account_id,
        'account_name': existing.account_name,
        'group_memberships': group_memberships,
        'active': active if active is not None else existing.active,
        'created_at': existing.created_at,
        'updated_at': get_iso_timestamp()
    }

    # Validate
    try:
        updated_account = AccountConfig(**data)
    except ValidationError as e:
        print_validation_errors(e.errors())
        print_error("Validation failed")
        sys.exit(1)

    # Show what will be updated
    console.print(f"\n[cyan]Updating account:[/cyan]")
    print_account_table([updated_account])

    if not execute:
        print_dry_run_warning()
        return

    # Delete and recreate (to update membership records)
    client.delete_account(account_id)
    result = client.create_account(updated_account)

    if result.warnings:
        print_write_warnings(result)

    # Log to audit
    changes = {
        'added_groups': [add_group] if add_group else [],
        'removed_groups': [remove_group] if remove_group else [],
        'active': data['active']
    }
    log_audit(client, audit_table, 'UPDATE', 'ACCOUNT', account_id, changes)

    print_success(f"Updated account: {account_id}")


# ============================================================================
# THRESHOLD COMMANDS
# ============================================================================

@cli.group()
def threshold():
    """Manage spending thresholds"""
    pass


@threshold.command('set')
@click.argument('group_id')
@click.option('--type', 'threshold_type', required=True,
              type=click.Choice(['absolute', 'percentage', 'fair_share']),
              help='Threshold type')
@click.option('--amount', type=Decimal, help='Dollar amount (required for absolute type)')
@click.option('--percentage', type=Decimal, help='Percentage value (required for percentage type)')
@click.option('--execute', is_flag=True, help='Actually write to DynamoDB (default is dry-run)')
@click.pass_context
def threshold_set(ctx, group_id, threshold_type, amount, percentage, execute):
    """Set a threshold for a spending group"""
    client = ctx.obj['client']
    audit_table = ctx.obj['audit_table']

    # Validate required fields for type
    if threshold_type == 'absolute' and amount is None:
        print_error("--amount is required for absolute threshold type")
        sys.exit(1)
    if threshold_type == 'percentage' and percentage is None:
        print_error("--percentage is required for percentage threshold type")
        sys.exit(1)

    # Generate threshold_id
    threshold_id = f"{group_id}-{threshold_type}"

    # Prepare data
    now = get_iso_timestamp()
    data = {
        'threshold_id': threshold_id,
        'group_id': group_id,
        'threshold_type': threshold_type,
        'absolute_amount': amount,
        'percentage_value': percentage,
        'created_at': now,
        'updated_at': now
    }

    # Validate with interactive retry
    threshold_config = validate_with_retry(ThresholdConfig, data, f"threshold {threshold_id}")
    if threshold_config is None:
        print_error("Operation cancelled")
        sys.exit(1)

    # Check if group exists
    if client.get_group(group_id) is None:
        console.print(f"[yellow]Warning: Group '{group_id}' not found[/yellow]")
        console.print("[yellow]Threshold will be created, but group doesn't exist yet[/yellow]")

    # Show what will be created
    console.print(f"\n[cyan]Setting threshold:[/cyan]")
    print_threshold_table([threshold_config])

    if not execute:
        print_dry_run_warning()
        return

    # Execute write
    result = client.create_threshold(threshold_config)

    if result.warnings:
        print_write_warnings(result)

    # Log to audit
    log_audit(client, audit_table, 'CREATE', 'THRESHOLD', threshold_id, data)

    print_success(f"Set threshold: {threshold_id}")


@threshold.command('list')
@click.option('--group', help='Filter by group ID')
@click.pass_context
def threshold_list(ctx, group):
    """List all thresholds"""
    client = ctx.obj['client']

    if group:
        thresholds = client.get_thresholds_for_group(group)
    else:
        thresholds = client.list_all_thresholds()

    if not thresholds:
        console.print("[yellow]No thresholds found[/yellow]")
        return

    print_threshold_table(thresholds)


@threshold.command('remove')
@click.argument('threshold_id')
@click.option('--execute', is_flag=True, help='Actually write to DynamoDB (default is dry-run)')
@click.pass_context
def threshold_remove(ctx, threshold_id, execute):
    """Remove a threshold"""
    client = ctx.obj['client']
    audit_table = ctx.obj['audit_table']

    # Get existing threshold (need to scan to find it)
    all_thresholds = client.list_all_thresholds()
    threshold = next((t for t in all_thresholds if t.threshold_id == threshold_id), None)

    if threshold is None:
        print_error(f"Threshold not found: {threshold_id}")
        sys.exit(1)

    # Show what will be deleted
    console.print(f"\n[cyan]Threshold to remove:[/cyan]")
    print_threshold_table([threshold])

    if not execute:
        print_dry_run_warning()
        return

    # Execute delete
    try:
        client.table.delete_item(
            Key={'PK': f'THRESHOLD#{threshold_id}', 'SK': f'GROUP#{threshold.group_id}'}
        )

        # Log to audit
        log_audit(client, audit_table, 'DELETE', 'THRESHOLD', threshold_id, {'threshold_id': threshold_id})

        print_success(f"Removed threshold: {threshold_id}")
    except Exception as e:
        print_error(f"Failed to remove threshold: {e}")
        sys.exit(1)


# ============================================================================
# BULK COMMANDS
# ============================================================================

@cli.group()
def bulk():
    """Bulk import/export operations"""
    pass


@bulk.command('import')
@click.argument('file_path')
@click.option('--format', type=click.Choice(['json', 'csv']), default='json',
              help='File format (default: json)')
@click.option('--execute', is_flag=True, help='Actually write to DynamoDB (default is dry-run)')
@click.pass_context
def bulk_import(ctx, file_path, format, execute):
    """Import configuration from JSON or CSV file"""
    client = ctx.obj['client']
    audit_table = ctx.obj['audit_table']

    # Load file
    try:
        with open(file_path, 'r') as f:
            if format == 'json':
                data = json.load(f)
            else:
                print_error("CSV import not yet implemented")
                sys.exit(1)
    except Exception as e:
        print_error(f"Failed to load file: {e}")
        sys.exit(1)

    # Validate all entities
    groups = data.get('groups', [])
    accounts = data.get('accounts', [])
    thresholds = data.get('thresholds', [])

    valid_groups = []
    invalid_groups = []

    for group_data in groups:
        try:
            group = SpendingGroup(**group_data)
            valid_groups.append(group)
        except ValidationError as e:
            invalid_groups.append({'data': group_data, 'errors': e.errors()})

    valid_accounts = []
    invalid_accounts = []

    for account_data in accounts:
        try:
            account = AccountConfig(**account_data)
            valid_accounts.append(account)
        except ValidationError as e:
            invalid_accounts.append({'data': account_data, 'errors': e.errors()})

    valid_thresholds = []
    invalid_thresholds = []

    for threshold_data in thresholds:
        try:
            threshold = ThresholdConfig(**threshold_data)
            valid_thresholds.append(threshold)
        except ValidationError as e:
            invalid_thresholds.append({'data': threshold_data, 'errors': e.errors()})

    # Show summary
    console.print(f"\n[cyan]Import Summary:[/cyan]")
    console.print(f"Valid groups: {len(valid_groups)}")
    console.print(f"Invalid groups: {len(invalid_groups)}")
    console.print(f"Valid accounts: {len(valid_accounts)}")
    console.print(f"Invalid accounts: {len(invalid_accounts)}")
    console.print(f"Valid thresholds: {len(valid_thresholds)}")
    console.print(f"Invalid thresholds: {len(invalid_thresholds)}")

    # Handle invalid entities
    if invalid_groups or invalid_accounts or invalid_thresholds:
        console.print(f"\n[yellow]Found {len(invalid_groups) + len(invalid_accounts) + len(invalid_thresholds)} invalid entities[/yellow]")

        choice = click.prompt(
            "How to proceed? [y=fix interactively, n=skip invalid, abort=cancel]",
            type=click.Choice(['y', 'n', 'abort'], case_sensitive=False),
            default='n'
        )

        if choice == 'abort':
            print_error("Import cancelled")
            sys.exit(1)
        elif choice == 'n':
            console.print("[yellow]Skipping invalid entities[/yellow]")
        # Interactive fix not implemented for bulk - too complex

    if not execute:
        print_dry_run_warning()
        return

    # Import valid entities
    warning_count = 0

    console.print("\n[cyan]Importing groups...[/cyan]")
    with client.table.batch_writer() as batch:
        for group in valid_groups:
            result = client.create_group(group)
            if result.warnings:
                warning_count += len(result.warnings)

    console.print(f"[cyan]Importing accounts...[/cyan]")
    for account in valid_accounts:
        result = client.create_account(account)
        if result.warnings:
            warning_count += len(result.warnings)

    console.print(f"[cyan]Importing thresholds...[/cyan]")
    for threshold in valid_thresholds:
        result = client.create_threshold(threshold)
        if result.warnings:
            warning_count += len(result.warnings)

    # Log to audit
    import_details = {
        'file': file_path,
        'groups': len(valid_groups),
        'accounts': len(valid_accounts),
        'thresholds': len(valid_thresholds),
        'skipped': len(invalid_groups) + len(invalid_accounts) + len(invalid_thresholds)
    }
    log_audit(client, audit_table, 'BULK_IMPORT', 'CONFIG', file_path, import_details)

    print_success(f"Imported {len(valid_groups)} groups, {len(valid_accounts)} accounts, {len(valid_thresholds)} thresholds")
    if warning_count > 0:
        console.print(f"[yellow]Total warnings: {warning_count}[/yellow]")
    if invalid_groups or invalid_accounts or invalid_thresholds:
        console.print(f"[yellow]Skipped invalid: {len(invalid_groups) + len(invalid_accounts) + len(invalid_thresholds)}[/yellow]")


@bulk.command('export')
@click.option('--format', type=click.Choice(['json']), default='json',
              help='Export format (default: json)')
@click.option('--output', help='Output file path (default: stdout)')
@click.pass_context
def bulk_export(ctx, format, output):
    """Export full configuration to JSON"""
    client = ctx.obj['client']

    # Load all configuration
    console.print("[cyan]Loading configuration...[/cyan]", err=True)

    groups = client.list_groups()
    accounts = client.list_all_accounts()
    thresholds = client.list_all_thresholds()

    # Serialize to dict
    export_data = {
        'groups': [group.model_dump() for group in groups],
        'accounts': [account.model_dump() for account in accounts],
        'thresholds': [threshold.model_dump() for threshold in thresholds],
        'exported_at': get_iso_timestamp()
    }

    # Convert Decimal to string for JSON serialization
    def decimal_default(obj):
        if isinstance(obj, Decimal):
            return str(obj)
        raise TypeError

    json_output = json.dumps(export_data, indent=2, default=decimal_default)

    # Write to file or stdout
    if output:
        with open(output, 'w') as f:
            f.write(json_output)
        print_success(f"Exported to {output}")
    else:
        print(json_output)


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    cli()
