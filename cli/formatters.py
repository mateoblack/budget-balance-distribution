"""Rich-based terminal output formatting for CLI."""
from typing import Optional
import click
from rich.console import Console
from rich.table import Table
from rich import box
from shared.models import SpendingGroup, AccountConfig, ThresholdConfig
from shared.dynamo_client import WriteResult

console = Console()


def print_group_table(groups: list[SpendingGroup]) -> None:
    """Display groups in a Rich Table."""
    table = Table(title="Spending Groups", box=box.ROUNDED)
    table.add_column("Group ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="magenta")
    table.add_column("Budget", justify="right", style="green")
    table.add_column("Active", justify="center")
    table.add_column("Created", style="dim")

    for group in groups:
        table.add_row(
            group.group_id,
            group.name,
            f"${group.total_budget:,.2f}",
            "✓" if group.active else "✗",
            group.created_at[:10]  # Just the date part
        )

    console.print(table)


def print_account_table(accounts: list[AccountConfig]) -> None:
    """Display accounts in a Rich Table."""
    table = Table(title="Accounts", box=box.ROUNDED)
    table.add_column("Account ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="magenta")
    table.add_column("Groups", style="yellow")
    table.add_column("Active", justify="center")

    for account in accounts:
        table.add_row(
            account.account_id,
            account.account_name or "(unnamed)",
            ", ".join(account.group_memberships),
            "✓" if account.active else "✗"
        )

    console.print(table)


def print_threshold_table(thresholds: list[ThresholdConfig]) -> None:
    """Display thresholds in a Rich Table."""
    table = Table(title="Thresholds", box=box.ROUNDED)
    table.add_column("Threshold ID", style="cyan", no_wrap=True)
    table.add_column("Group", style="yellow")
    table.add_column("Type", style="magenta")
    table.add_column("Value", justify="right", style="green")

    for threshold in thresholds:
        if threshold.threshold_type == "absolute":
            value = f"${threshold.absolute_amount:,.2f}"
        elif threshold.threshold_type == "percentage":
            value = f"{threshold.percentage_value}%"
        else:  # fair_share
            value = "Fair Share"

        table.add_row(
            threshold.threshold_id,
            threshold.group_id,
            threshold.threshold_type,
            value
        )

    console.print(table)


def print_config_summary(config: dict) -> None:
    """Print summary showing total groups, accounts, thresholds."""
    table = Table(title="Configuration Summary", box=box.ROUNDED)
    table.add_column("Entity Type", style="cyan")
    table.add_column("Count", justify="right", style="green")

    table.add_row("Groups", str(len(config.get("groups", []))))
    table.add_row("Accounts", str(len(config.get("accounts", []))))
    table.add_row("Thresholds", str(len(config.get("thresholds", []))))

    console.print(table)


def print_dry_run_warning() -> None:
    """Print dry-run warning message."""
    console.print("[yellow]DRY RUN:[/yellow] Use --execute to apply changes")


def print_success(message: str) -> None:
    """Print green checkmark + message."""
    console.print(f"[green]✓[/green] {message}")


def print_error(message: str) -> None:
    """Print red X + message."""
    console.print(f"[red]✗[/red] {message}")


def print_validation_errors(errors: list) -> None:
    """Print each Pydantic validation error in red with field details."""
    console.print("[red]Validation errors:[/red]")
    for error in errors:
        field = " -> ".join(str(loc) for loc in error['loc'])
        message = error['msg']
        console.print(f"  [red]✗[/red] {field}: {message}")


def print_write_warnings(result: WriteResult) -> None:
    """Print each warning from WriteResult in yellow."""
    if result.warnings:
        console.print(f"[yellow]Warnings for {result.entity_type} {result.entity_id}:[/yellow]")
        for warning in result.warnings:
            console.print(f"  [yellow]⚠[/yellow]  {warning}")


def prompt_fix_validation_errors(errors: list, original_values: dict) -> Optional[dict]:
    """
    Interactive correction loop for validation errors.

    For each validation error:
    - Display field name, current value, and error message
    - Prompt user to correct the value
    - User can type "skip" to keep original value
    - User can type "abort" to cancel entirely

    Args:
        errors: List of Pydantic validation error dicts
        original_values: Dict of original values that failed validation

    Returns:
        Dict of corrected values, or None if user aborted
    """
    corrected = original_values.copy()

    console.print("\n[cyan]Let's fix these validation errors:[/cyan]")

    for error in errors:
        # Extract field path (e.g., ['account_id'] or ['threshold_type'])
        field_path = error['loc']
        field_name = ".".join(str(loc) for loc in field_path)
        field_key = field_path[0] if len(field_path) == 1 else field_name

        current_value = original_values.get(field_key, "")
        error_msg = error['msg']

        console.print(f"\n[yellow]Field:[/yellow] {field_name}")
        console.print(f"[yellow]Current value:[/yellow] {current_value}")
        console.print(f"[red]Error:[/red] {error_msg}")

        # Prompt for new value
        try:
            new_value = click.prompt(
                f"New value for {field_name} (or 'skip' to keep current, 'abort' to cancel)",
                default=str(current_value) if current_value else "",
                show_default=True
            )

            if new_value.lower() == "abort":
                console.print("[yellow]Aborted by user[/yellow]")
                return None
            elif new_value.lower() == "skip":
                console.print(f"[dim]Keeping original value: {current_value}[/dim]")
                continue
            else:
                # Try to convert to appropriate type
                # Check if original value was a list
                if isinstance(original_values.get(field_key), list):
                    # Split by comma for list values
                    corrected[field_key] = [v.strip() for v in new_value.split(",") if v.strip()]
                elif isinstance(original_values.get(field_key), bool):
                    corrected[field_key] = new_value.lower() in ('true', 't', 'yes', 'y', '1')
                else:
                    corrected[field_key] = new_value

                console.print(f"[green]✓[/green] Updated {field_name}")

        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Aborted by user[/yellow]")
            return None

    return corrected
