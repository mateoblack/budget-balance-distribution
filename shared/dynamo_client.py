"""DynamoDB client wrapper for configuration CRUD operations with warning-flag pattern."""
import logging
from dataclasses import dataclass, field
from typing import Optional
import boto3
from botocore.exceptions import ClientError
from shared.models import AccountConfig, SpendingGroup, ThresholdConfig

logger = logging.getLogger(__name__)


@dataclass
class WriteResult:
    """Result of a DynamoDB write operation with warning-flag pattern."""

    success: bool  # True if item was written
    warnings: list[str] = field(default_factory=list)  # Non-fatal issues (saved but flagged)
    entity_type: str = ""
    entity_id: str = ""


class ConfigDynamoClient:
    """
    DynamoDB client for configuration CRUD operations.

    Implements warning-flag pattern: saves items with _warnings attribute on conflicts
    or referential integrity issues instead of raising exceptions. This allows partial
    progress - user can fix issues later.

    Only raises on actual DynamoDB errors (network, permissions, etc.).
    """

    def __init__(self, table_name: str, boto3_resource=None):
        """
        Initialize DynamoDB client.

        Args:
            table_name: Name of the DynamoDB config table
            boto3_resource: Optional boto3 DynamoDB resource (for testing with mocks)
        """
        self.table_name = table_name
        if boto3_resource is None:
            self.dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
        else:
            self.dynamodb = boto3_resource
        self.table = self.dynamodb.Table(table_name)

    def create_group(self, group: SpendingGroup) -> WriteResult:
        """
        Create a spending group.

        Attempts conditional write (PK must not exist). On conflict, saves with
        _warnings attribute to flag the overwrite.

        Args:
            group: SpendingGroup to create

        Returns:
            WriteResult with success=True and warnings if group already existed
        """
        item = group.to_dynamodb_item()

        try:
            # Try conditional write (must not exist)
            self.table.put_item(
                Item=item,
                ConditionExpression='attribute_not_exists(PK)'
            )
            logger.info(f"Created group {group.group_id}")
            return WriteResult(
                success=True,
                entity_type="GROUP",
                entity_id=group.group_id
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                # Group already exists - save with warning flag
                warning_msg = f"Group already exists: {group.group_id}, overwriting"
                item['_warnings'] = [warning_msg]
                self.table.put_item(Item=item)
                logger.warning(warning_msg)
                return WriteResult(
                    success=True,
                    warnings=[warning_msg],
                    entity_type="GROUP",
                    entity_id=group.group_id
                )
            else:
                # Actual DynamoDB error - raise
                raise

    def get_group(self, group_id: str) -> Optional[SpendingGroup]:
        """
        Get a spending group by ID.

        Args:
            group_id: Group identifier

        Returns:
            SpendingGroup or None if not found
        """
        try:
            response = self.table.get_item(
                Key={'PK': f'GROUP#{group_id}', 'SK': 'METADATA'}
            )
            if 'Item' in response:
                return SpendingGroup.from_dynamodb_item(response['Item'])
            return None
        except ClientError as e:
            logger.error(f"Error getting group {group_id}: {e}")
            raise

    def update_group(self, group: SpendingGroup) -> WriteResult:
        """
        Update a spending group.

        Attempts conditional update (PK must exist). If not, creates as new with warning.

        Args:
            group: SpendingGroup to update

        Returns:
            WriteResult with success=True and warnings if group didn't exist
        """
        item = group.to_dynamodb_item()

        try:
            # Try conditional update (must exist)
            self.table.put_item(
                Item=item,
                ConditionExpression='attribute_exists(PK)'
            )
            logger.info(f"Updated group {group.group_id}")
            return WriteResult(
                success=True,
                entity_type="GROUP",
                entity_id=group.group_id
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                # Group doesn't exist - save as new with warning
                warning_msg = f"Group did not exist, created as new: {group.group_id}"
                item['_warnings'] = [warning_msg]
                self.table.put_item(Item=item)
                logger.warning(warning_msg)
                return WriteResult(
                    success=True,
                    warnings=[warning_msg],
                    entity_type="GROUP",
                    entity_id=group.group_id
                )
            else:
                raise

    def delete_group(self, group_id: str) -> WriteResult:
        """
        Delete a spending group and all its membership records.

        Deletes:
        - GROUP#{group_id} / METADATA (group metadata)
        - All GROUP#{group_id} / ACCOUNT#{account_id} (reverse membership index)

        Args:
            group_id: Group identifier

        Returns:
            WriteResult with success=True and warnings if group not found
        """
        try:
            # Try to delete the group metadata
            self.table.delete_item(
                Key={'PK': f'GROUP#{group_id}', 'SK': 'METADATA'},
                ConditionExpression='attribute_exists(PK)'
            )

            # Delete all membership records (reverse index)
            response = self.table.query(
                KeyConditionExpression='PK = :pk AND begins_with(SK, :sk)',
                ExpressionAttributeValues={
                    ':pk': f'GROUP#{group_id}',
                    ':sk': 'ACCOUNT#'
                }
            )

            if response.get('Items'):
                with self.table.batch_writer() as batch:
                    for item in response['Items']:
                        batch.delete_item(Key={'PK': item['PK'], 'SK': item['SK']})
                logger.info(f"Deleted group {group_id} and {len(response['Items'])} membership records")
            else:
                logger.info(f"Deleted group {group_id} (no membership records)")

            return WriteResult(
                success=True,
                entity_type="GROUP",
                entity_id=group_id
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                # Group doesn't exist
                warning_msg = f"Group not found: {group_id}, nothing deleted"
                logger.warning(warning_msg)
                return WriteResult(
                    success=True,
                    warnings=[warning_msg],
                    entity_type="GROUP",
                    entity_id=group_id
                )
            else:
                raise

    def list_groups(self) -> list[SpendingGroup]:
        """
        List all spending groups.

        Returns:
            List of SpendingGroup objects
        """
        try:
            response = self.table.scan(
                FilterExpression='entity_type = :et AND SK = :sk',
                ExpressionAttributeValues={
                    ':et': 'GROUP',
                    ':sk': 'METADATA'
                }
            )
            groups = [SpendingGroup.from_dynamodb_item(item) for item in response.get('Items', [])]
            logger.info(f"Found {len(groups)} groups")
            return groups
        except ClientError as e:
            logger.error(f"Error listing groups: {e}")
            raise

    def create_account(self, account: AccountConfig) -> WriteResult:
        """
        Create an account with metadata and membership records.

        Writes:
        - ACCOUNT#{id} / METADATA (account metadata)
        - ACCOUNT#{id} / GROUP#{gid} (forward membership index)
        - GROUP#{gid} / ACCOUNT#{id} (reverse membership index)

        Checks referential integrity: warns if any groups don't exist but still saves.

        Args:
            account: AccountConfig to create

        Returns:
            WriteResult with success=True and warnings if referenced groups missing
        """
        warnings = []

        # Check referential integrity
        for group_id in account.group_memberships:
            if self.get_group(group_id) is None:
                warnings.append(f"Referenced group not found: {group_id}")

        # Prepare metadata item
        item = account.to_dynamodb_item()
        if warnings:
            item['_warnings'] = warnings

        try:
            # Write metadata with conditional check
            self.table.put_item(
                Item=item,
                ConditionExpression='attribute_not_exists(PK)'
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                # Account already exists - overwrite with warning
                overwrite_warning = f"Account already exists: {account.account_id}, overwriting"
                warnings.append(overwrite_warning)
                item['_warnings'] = warnings
                self.table.put_item(Item=item)
                logger.warning(overwrite_warning)
            else:
                raise

        # Write membership records (both directions) using batch writer
        with self.table.batch_writer() as batch:
            for group_id in account.group_memberships:
                # Forward index: ACCOUNT#{id} / GROUP#{gid}
                batch.put_item(Item={
                    'PK': f'ACCOUNT#{account.account_id}',
                    'SK': f'GROUP#{group_id}',
                    'entity_type': 'MEMBERSHIP'
                })
                # Reverse index: GROUP#{gid} / ACCOUNT#{id}
                batch.put_item(Item={
                    'PK': f'GROUP#{group_id}',
                    'SK': f'ACCOUNT#{account.account_id}',
                    'entity_type': 'MEMBERSHIP'
                })

        if warnings:
            logger.warning(f"Created account {account.account_id} with warnings: {warnings}")
        else:
            logger.info(f"Created account {account.account_id} with {len(account.group_memberships)} group memberships")

        return WriteResult(
            success=True,
            warnings=warnings,
            entity_type="ACCOUNT",
            entity_id=account.account_id
        )

    def get_account(self, account_id: str) -> Optional[AccountConfig]:
        """
        Get an account by ID with its group memberships.

        Reads:
        - ACCOUNT#{id} / METADATA (account metadata)
        - Query ACCOUNT#{id} / GROUP# (membership records)

        Args:
            account_id: AWS account ID

        Returns:
            AccountConfig or None if not found
        """
        try:
            # Get metadata
            response = self.table.get_item(
                Key={'PK': f'ACCOUNT#{account_id}', 'SK': 'METADATA'}
            )
            if 'Item' not in response:
                return None

            # Get group memberships from membership records
            membership_response = self.table.query(
                KeyConditionExpression='PK = :pk AND begins_with(SK, :sk)',
                ExpressionAttributeValues={
                    ':pk': f'ACCOUNT#{account_id}',
                    ':sk': 'GROUP#'
                }
            )

            # Extract group IDs from SK (format: GROUP#{group_id})
            group_memberships = [
                item['SK'].replace('GROUP#', '')
                for item in membership_response.get('Items', [])
            ]

            # Override group_memberships from metadata with actual membership records
            item = response['Item']
            item['group_memberships'] = group_memberships

            return AccountConfig.from_dynamodb_item(item)
        except ClientError as e:
            logger.error(f"Error getting account {account_id}: {e}")
            raise

    def delete_account(self, account_id: str) -> WriteResult:
        """
        Delete an account and all its membership records (both directions).

        Deletes:
        - ACCOUNT#{id} / METADATA
        - All ACCOUNT#{id} / GROUP#{gid} (forward membership)
        - All GROUP#{gid} / ACCOUNT#{id} (reverse membership)

        Args:
            account_id: AWS account ID

        Returns:
            WriteResult with success=True and warnings if account not found
        """
        # Get account to find group memberships
        account = self.get_account(account_id)
        if account is None:
            warning_msg = f"Account not found: {account_id}, nothing deleted"
            logger.warning(warning_msg)
            return WriteResult(
                success=True,
                warnings=[warning_msg],
                entity_type="ACCOUNT",
                entity_id=account_id
            )

        try:
            # Delete metadata and membership records using batch writer
            with self.table.batch_writer() as batch:
                # Delete metadata
                batch.delete_item(Key={'PK': f'ACCOUNT#{account_id}', 'SK': 'METADATA'})

                # Delete forward membership records
                for group_id in account.group_memberships:
                    batch.delete_item(Key={
                        'PK': f'ACCOUNT#{account_id}',
                        'SK': f'GROUP#{group_id}'
                    })
                    # Delete reverse membership records
                    batch.delete_item(Key={
                        'PK': f'GROUP#{group_id}',
                        'SK': f'ACCOUNT#{account_id}'
                    })

            logger.info(f"Deleted account {account_id} and {len(account.group_memberships)} membership records")
            return WriteResult(
                success=True,
                entity_type="ACCOUNT",
                entity_id=account_id
            )
        except ClientError as e:
            logger.error(f"Error deleting account {account_id}: {e}")
            raise

    def create_threshold(self, threshold: ThresholdConfig) -> WriteResult:
        """
        Create a threshold configuration.

        Checks referential integrity: warns if group doesn't exist but still saves.

        Args:
            threshold: ThresholdConfig to create

        Returns:
            WriteResult with success=True and warnings if referenced group missing
        """
        warnings = []

        # Check referential integrity
        if self.get_group(threshold.group_id) is None:
            warnings.append(f"Referenced group not found: {threshold.group_id}")

        item = threshold.to_dynamodb_item()
        if warnings:
            item['_warnings'] = warnings

        try:
            # Try conditional write (must not exist)
            self.table.put_item(
                Item=item,
                ConditionExpression='attribute_not_exists(PK)'
            )
            if warnings:
                logger.warning(f"Created threshold {threshold.threshold_id} with warnings: {warnings}")
            else:
                logger.info(f"Created threshold {threshold.threshold_id}")
            return WriteResult(
                success=True,
                warnings=warnings,
                entity_type="THRESHOLD",
                entity_id=threshold.threshold_id
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                # Threshold already exists - overwrite with warning
                overwrite_warning = f"Threshold already exists: {threshold.threshold_id}, overwriting"
                warnings.append(overwrite_warning)
                item['_warnings'] = warnings
                self.table.put_item(Item=item)
                logger.warning(f"Created threshold {threshold.threshold_id} with warnings: {warnings}")
                return WriteResult(
                    success=True,
                    warnings=warnings,
                    entity_type="THRESHOLD",
                    entity_id=threshold.threshold_id
                )
            else:
                raise

    def get_thresholds_for_group(self, group_id: str) -> list[ThresholdConfig]:
        """
        Get all threshold configurations for a group.

        Args:
            group_id: Group identifier

        Returns:
            List of ThresholdConfig objects
        """
        try:
            # Use scan with filter since PK is THRESHOLD#{id}, SK is GROUP#{group_id}
            response = self.table.scan(
                FilterExpression='entity_type = :et AND SK = :sk',
                ExpressionAttributeValues={
                    ':et': 'THRESHOLD',
                    ':sk': f'GROUP#{group_id}'
                }
            )
            thresholds = [ThresholdConfig.from_dynamodb_item(item) for item in response.get('Items', [])]
            logger.info(f"Found {len(thresholds)} thresholds for group {group_id}")
            return thresholds
        except ClientError as e:
            logger.error(f"Error getting thresholds for group {group_id}: {e}")
            raise

    def list_all_accounts(self) -> list[AccountConfig]:
        """
        List all accounts with their group memberships.

        Returns:
            List of AccountConfig objects
        """
        try:
            # Scan for all account metadata
            response = self.table.scan(
                FilterExpression='entity_type = :et AND SK = :sk',
                ExpressionAttributeValues={
                    ':et': 'ACCOUNT',
                    ':sk': 'METADATA'
                }
            )

            accounts = []
            for item in response.get('Items', []):
                account_id = item['account_id']
                # Get group memberships for each account
                account = self.get_account(account_id)
                if account:
                    accounts.append(account)

            logger.info(f"Found {len(accounts)} accounts")
            return accounts
        except ClientError as e:
            logger.error(f"Error listing accounts: {e}")
            raise

    def list_all_thresholds(self) -> list[ThresholdConfig]:
        """
        List all threshold configurations.

        Returns:
            List of ThresholdConfig objects
        """
        try:
            response = self.table.scan(
                FilterExpression='entity_type = :et',
                ExpressionAttributeValues={
                    ':et': 'THRESHOLD'
                }
            )
            thresholds = [ThresholdConfig.from_dynamodb_item(item) for item in response.get('Items', [])]
            logger.info(f"Found {len(thresholds)} thresholds")
            return thresholds
        except ClientError as e:
            logger.error(f"Error listing thresholds: {e}")
            raise

    def get_warnings(self, entity_type: str, entity_id: str) -> list[str]:
        """
        Get warnings for an entity.

        Useful for CLI to show which items need attention.

        Args:
            entity_type: Type of entity (GROUP, ACCOUNT, THRESHOLD)
            entity_id: Entity identifier

        Returns:
            List of warning strings (empty if no warnings)
        """
        try:
            if entity_type == "GROUP":
                pk = f'GROUP#{entity_id}'
                sk = 'METADATA'
            elif entity_type == "ACCOUNT":
                pk = f'ACCOUNT#{entity_id}'
                sk = 'METADATA'
            elif entity_type == "THRESHOLD":
                pk = f'THRESHOLD#{entity_id}'
                # Need to find the SK (GROUP#{group_id}) - scan required
                response = self.table.scan(
                    FilterExpression='PK = :pk AND entity_type = :et',
                    ExpressionAttributeValues={
                        ':pk': pk,
                        ':et': 'THRESHOLD'
                    }
                )
                if response.get('Items'):
                    return response['Items'][0].get('_warnings', [])
                return []
            else:
                logger.error(f"Unknown entity type: {entity_type}")
                return []

            response = self.table.get_item(Key={'PK': pk, 'SK': sk})
            if 'Item' in response:
                return response['Item'].get('_warnings', [])
            return []
        except ClientError as e:
            logger.error(f"Error getting warnings for {entity_type} {entity_id}: {e}")
            raise
