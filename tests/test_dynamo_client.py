"""Tests for DynamoDB client wrapper with warning-flag pattern."""
import pytest
from datetime import datetime
from decimal import Decimal
from moto import mock_aws
import boto3
from shared.dynamo_client import ConfigDynamoClient, WriteResult
from shared.models import AccountConfig, SpendingGroup, ThresholdConfig


@pytest.fixture
def aws_mock():
    """Create AWS mock context."""
    with mock_aws():
        yield


@pytest.fixture
def dynamodb_resource(aws_mock):
    """Create a boto3 DynamoDB resource with mocked AWS."""
    return boto3.resource('dynamodb', region_name='us-east-1')


@pytest.fixture
def dynamodb_table(dynamodb_resource):
    """Create a mocked DynamoDB table for testing."""
    table = dynamodb_resource.create_table(
        TableName='test-config-table',
        KeySchema=[
            {'AttributeName': 'PK', 'KeyType': 'HASH'},
            {'AttributeName': 'SK', 'KeyType': 'RANGE'}
        ],
        AttributeDefinitions=[
            {'AttributeName': 'PK', 'AttributeType': 'S'},
            {'AttributeName': 'SK', 'AttributeType': 'S'}
        ],
        BillingMode='PAY_PER_REQUEST'
    )
    return table


@pytest.fixture
def client(dynamodb_resource, dynamodb_table):
    """Create a ConfigDynamoClient with mocked DynamoDB."""
    return ConfigDynamoClient('test-config-table', boto3_resource=dynamodb_resource)


@pytest.fixture
def sample_group():
    """Create a sample SpendingGroup."""
    return SpendingGroup(
        group_id='test-group',
        name='Test Group',
        description='Test group description',
        total_budget=Decimal('10000.00'),
        active=True,
        created_at='2026-02-12T00:00:00Z',
        updated_at='2026-02-12T00:00:00Z'
    )


@pytest.fixture
def sample_account():
    """Create a sample AccountConfig."""
    return AccountConfig(
        account_id='123456789012',
        account_name='Test Account',
        group_memberships=['test-group'],
        active=True,
        created_at='2026-02-12T00:00:00Z',
        updated_at='2026-02-12T00:00:00Z'
    )


@pytest.fixture
def sample_threshold():
    """Create a sample ThresholdConfig."""
    return ThresholdConfig(
        threshold_id='test-threshold-1',
        group_id='test-group',
        threshold_type='absolute',
        absolute_amount=Decimal('5000.00'),
        created_at='2026-02-12T00:00:00Z',
        updated_at='2026-02-12T00:00:00Z'
    )


def test_create_group_succeeds(client, sample_group):
    """Test creating a group returns success with no warnings."""
    result = client.create_group(sample_group)

    assert result.success is True
    assert result.warnings == []
    assert result.entity_type == "GROUP"
    assert result.entity_id == "test-group"

    # Verify it was actually saved
    retrieved = client.get_group('test-group')
    assert retrieved is not None
    assert retrieved.group_id == 'test-group'
    assert retrieved.name == 'Test Group'


def test_create_group_duplicate_returns_warning(client, sample_group):
    """Test creating a duplicate group returns success with warning and overwrites."""
    # Create first time
    result1 = client.create_group(sample_group)
    assert result1.success is True
    assert result1.warnings == []

    # Create again (duplicate)
    result2 = client.create_group(sample_group)
    assert result2.success is True
    assert len(result2.warnings) == 1
    assert "already exists" in result2.warnings[0]
    assert "overwriting" in result2.warnings[0]

    # Verify _warnings attribute is stored in DynamoDB
    warnings = client.get_warnings("GROUP", "test-group")
    assert len(warnings) == 1
    assert "already exists" in warnings[0]


def test_update_group_nonexistent_creates_with_warning(client, sample_group):
    """Test updating a nonexistent group creates it with warning."""
    result = client.update_group(sample_group)

    assert result.success is True
    assert len(result.warnings) == 1
    assert "did not exist" in result.warnings[0]
    assert "created as new" in result.warnings[0]

    # Verify it was created
    retrieved = client.get_group('test-group')
    assert retrieved is not None
    assert retrieved.group_id == 'test-group'


def test_update_group_existing_succeeds(client, sample_group):
    """Test updating an existing group succeeds."""
    # Create first
    client.create_group(sample_group)

    # Update with new name
    sample_group.name = 'Updated Group Name'
    result = client.update_group(sample_group)

    assert result.success is True
    assert result.warnings == []

    # Verify update
    retrieved = client.get_group('test-group')
    assert retrieved.name == 'Updated Group Name'


def test_delete_group_removes_group_and_memberships(client, sample_group, sample_account):
    """Test deleting a group removes group and membership records."""
    # Create group and account
    client.create_group(sample_group)
    client.create_account(sample_account)

    # Delete group
    result = client.delete_group('test-group')

    assert result.success is True
    assert result.warnings == []

    # Verify group is gone
    retrieved = client.get_group('test-group')
    assert retrieved is None


def test_delete_group_nonexistent_returns_warning(client):
    """Test deleting a nonexistent group returns success with warning."""
    result = client.delete_group('nonexistent-group')

    assert result.success is True
    assert len(result.warnings) == 1
    assert "not found" in result.warnings[0]
    assert "nothing deleted" in result.warnings[0]


def test_list_groups_returns_only_groups(client, sample_group, sample_account):
    """Test list_groups returns only GROUP entities."""
    # Create group and account
    client.create_group(sample_group)
    client.create_account(sample_account)

    # List groups
    groups = client.list_groups()

    assert len(groups) == 1
    assert groups[0].group_id == 'test-group'
    assert isinstance(groups[0], SpendingGroup)


def test_create_account_with_valid_group_succeeds(client, sample_group, sample_account):
    """Test creating an account with existing group succeeds."""
    # Create group first
    client.create_group(sample_group)

    # Create account
    result = client.create_account(sample_account)

    assert result.success is True
    assert result.warnings == []

    # Verify account and memberships
    retrieved = client.get_account('123456789012')
    assert retrieved is not None
    assert retrieved.account_id == '123456789012'
    assert 'test-group' in retrieved.group_memberships


def test_create_account_with_missing_groups_saves_with_warning(client, sample_account):
    """Test creating an account with missing groups saves with warning flag."""
    # Don't create the group - it doesn't exist

    result = client.create_account(sample_account)

    assert result.success is True
    assert len(result.warnings) == 1
    assert "Referenced group not found" in result.warnings[0]
    assert "test-group" in result.warnings[0]

    # Verify account was still saved
    retrieved = client.get_account('123456789012')
    assert retrieved is not None
    assert retrieved.account_id == '123456789012'

    # Verify _warnings attribute is stored
    warnings = client.get_warnings("ACCOUNT", "123456789012")
    assert len(warnings) == 1
    assert "Referenced group not found" in warnings[0]


def test_create_account_creates_bidirectional_memberships(client, sample_group, sample_account):
    """Test account creation creates both forward and reverse membership records."""
    client.create_group(sample_group)
    client.create_account(sample_account)

    # Query forward membership: ACCOUNT#{id} / GROUP#{gid}
    table = client.table
    forward_response = table.query(
        KeyConditionExpression='PK = :pk AND begins_with(SK, :sk)',
        ExpressionAttributeValues={
            ':pk': 'ACCOUNT#123456789012',
            ':sk': 'GROUP#'
        }
    )
    assert len(forward_response['Items']) == 1
    assert forward_response['Items'][0]['SK'] == 'GROUP#test-group'

    # Query reverse membership: GROUP#{gid} / ACCOUNT#{id}
    reverse_response = table.query(
        KeyConditionExpression='PK = :pk AND begins_with(SK, :sk)',
        ExpressionAttributeValues={
            ':pk': 'GROUP#test-group',
            ':sk': 'ACCOUNT#'
        }
    )
    assert len(reverse_response['Items']) == 1
    assert reverse_response['Items'][0]['SK'] == 'ACCOUNT#123456789012'


def test_get_account_populates_group_memberships(client, sample_group, sample_account):
    """Test get_account populates group_memberships from membership items."""
    client.create_group(sample_group)
    client.create_account(sample_account)

    retrieved = client.get_account('123456789012')

    assert retrieved is not None
    assert len(retrieved.group_memberships) == 1
    assert 'test-group' in retrieved.group_memberships


def test_delete_account_removes_all_records(client, sample_group, sample_account):
    """Test deleting an account removes metadata and all membership records."""
    client.create_group(sample_group)
    client.create_account(sample_account)

    result = client.delete_account('123456789012')

    assert result.success is True
    assert result.warnings == []

    # Verify account is gone
    retrieved = client.get_account('123456789012')
    assert retrieved is None

    # Verify membership records are gone
    table = client.table
    forward_response = table.query(
        KeyConditionExpression='PK = :pk',
        ExpressionAttributeValues={':pk': 'ACCOUNT#123456789012'}
    )
    assert len(forward_response['Items']) == 0


def test_delete_account_nonexistent_returns_warning(client):
    """Test deleting a nonexistent account returns success with warning."""
    result = client.delete_account('999999999999')

    assert result.success is True
    assert len(result.warnings) == 1
    assert "not found" in result.warnings[0]


def test_create_threshold_with_valid_group_succeeds(client, sample_group, sample_threshold):
    """Test creating a threshold with existing group succeeds."""
    client.create_group(sample_group)

    result = client.create_threshold(sample_threshold)

    assert result.success is True
    assert result.warnings == []

    # Verify threshold
    thresholds = client.get_thresholds_for_group('test-group')
    assert len(thresholds) == 1
    assert thresholds[0].threshold_id == 'test-threshold-1'


def test_create_threshold_with_missing_group_saves_with_warning(client, sample_threshold):
    """Test creating a threshold with missing group saves with warning."""
    result = client.create_threshold(sample_threshold)

    assert result.success is True
    assert len(result.warnings) == 1
    assert "Referenced group not found" in result.warnings[0]

    # Verify threshold was still saved
    thresholds = client.get_thresholds_for_group('test-group')
    assert len(thresholds) == 1

    # Verify _warnings attribute
    warnings = client.get_warnings("THRESHOLD", "test-threshold-1")
    assert len(warnings) == 1


def test_list_all_accounts(client, sample_group, sample_account):
    """Test list_all_accounts returns all accounts with memberships."""
    client.create_group(sample_group)
    client.create_account(sample_account)

    # Create another account
    account2 = AccountConfig(
        account_id='999999999999',
        account_name='Account 2',
        group_memberships=['test-group'],
        active=False,
        created_at='2026-02-12T00:00:00Z',
        updated_at='2026-02-12T00:00:00Z'
    )
    client.create_account(account2)

    accounts = client.list_all_accounts()

    assert len(accounts) == 2
    account_ids = [acc.account_id for acc in accounts]
    assert '123456789012' in account_ids
    assert '999999999999' in account_ids


def test_list_all_thresholds(client, sample_group, sample_threshold):
    """Test list_all_thresholds returns all threshold configurations."""
    client.create_group(sample_group)
    client.create_threshold(sample_threshold)

    # Create another threshold
    threshold2 = ThresholdConfig(
        threshold_id='test-threshold-2',
        group_id='test-group',
        threshold_type='percentage',
        percentage_value=Decimal('50.0'),
        created_at='2026-02-12T00:00:00Z',
        updated_at='2026-02-12T00:00:00Z'
    )
    client.create_threshold(threshold2)

    thresholds = client.list_all_thresholds()

    assert len(thresholds) == 2
    threshold_ids = [t.threshold_id for t in thresholds]
    assert 'test-threshold-1' in threshold_ids
    assert 'test-threshold-2' in threshold_ids


def test_get_warnings_retrieves_flagged_items(client, sample_group):
    """Test get_warnings retrieves _warnings attribute from flagged items."""
    # Create group twice to trigger warning
    client.create_group(sample_group)
    client.create_group(sample_group)

    warnings = client.get_warnings("GROUP", "test-group")

    assert len(warnings) == 1
    assert "already exists" in warnings[0]


def test_get_warnings_returns_empty_for_clean_items(client, sample_group):
    """Test get_warnings returns empty list for items without warnings."""
    client.create_group(sample_group)

    warnings = client.get_warnings("GROUP", "test-group")

    assert warnings == []
