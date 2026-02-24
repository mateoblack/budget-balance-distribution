"""Tests for Pydantic configuration models."""
import pytest
from decimal import Decimal
from datetime import datetime
from shared.models import AccountConfig, SpendingGroup, ThresholdConfig


class TestAccountConfig:
    """Test AccountConfig model validation and serialization."""

    def test_valid_account_creation(self):
        """Valid account with 12-digit ID."""
        account = AccountConfig(
            account_id="123456789012",
            account_name="Test Account",
            group_memberships=["group-1"],
            active=True,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        assert account.account_id == "123456789012"
        assert account.account_name == "Test Account"
        assert account.group_memberships == ["group-1"]
        assert account.active is True

    def test_invalid_account_11_digits(self):
        """Invalid account with 11-digit ID."""
        with pytest.raises(ValueError):
            AccountConfig(
                account_id="12345678901",
                group_memberships=["group-1"],
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_invalid_account_non_numeric(self):
        """Invalid account with non-numeric ID."""
        with pytest.raises(ValueError):
            AccountConfig(
                account_id="12345678901a",
                group_memberships=["group-1"],
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_invalid_account_empty_string(self):
        """Invalid account with empty string ID."""
        with pytest.raises(ValueError):
            AccountConfig(
                account_id="",
                group_memberships=["group-1"],
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_valid_account_must_have_group_membership(self):
        """Valid account must have at least one group membership."""
        account = AccountConfig(
            account_id="123456789012",
            group_memberships=["group-1"],
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        assert len(account.group_memberships) >= 1

    def test_invalid_account_empty_group_memberships(self):
        """Invalid account with empty group_memberships list."""
        with pytest.raises(ValueError):
            AccountConfig(
                account_id="123456789012",
                group_memberships=[],
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_account_dynamodb_round_trip(self):
        """DynamoDB round-trip preserves account data."""
        account = AccountConfig(
            account_id="123456789012",
            account_name="Test Account",
            group_memberships=["group-1", "group-2"],
            active=True,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        item = account.to_dynamodb_item()

        # Verify DynamoDB key pattern
        assert item["PK"] == "ACCOUNT#123456789012"
        assert item["SK"] == "METADATA"

        # Round-trip
        restored = AccountConfig.from_dynamodb_item(item)
        assert restored.account_id == account.account_id
        assert restored.account_name == account.account_name
        assert restored.group_memberships == account.group_memberships
        assert restored.active == account.active
        assert restored.created_at == account.created_at
        assert restored.updated_at == account.updated_at


class TestSpendingGroup:
    """Test SpendingGroup model validation and serialization."""

    def test_valid_spending_group_with_positive_budget(self):
        """Valid spending group with positive budget."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            description="Test description",
            total_budget=Decimal("10000.00"),
            active=True,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        assert group.group_id == "test-group"
        assert group.total_budget == Decimal("10000.00")

    def test_invalid_group_zero_budget(self):
        """Invalid group with zero budget."""
        with pytest.raises(ValueError):
            SpendingGroup(
                group_id="test-group",
                name="Test Group",
                total_budget=Decimal("0"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_invalid_group_negative_budget(self):
        """Invalid group with negative budget."""
        with pytest.raises(ValueError):
            SpendingGroup(
                group_id="test-group",
                name="Test Group",
                total_budget=Decimal("-100.00"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_invalid_group_id_with_uppercase(self):
        """Invalid group_id with uppercase letters."""
        with pytest.raises(ValueError):
            SpendingGroup(
                group_id="Test-Group",
                name="Test Group",
                total_budget=Decimal("10000.00"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_invalid_group_id_with_spaces(self):
        """Invalid group_id with spaces."""
        with pytest.raises(ValueError):
            SpendingGroup(
                group_id="test group",
                name="Test Group",
                total_budget=Decimal("10000.00"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_invalid_group_id_starting_with_hyphen(self):
        """Invalid group_id starting with hyphen."""
        with pytest.raises(ValueError):
            SpendingGroup(
                group_id="-test-group",
                name="Test Group",
                total_budget=Decimal("10000.00"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_invalid_group_id_ending_with_hyphen(self):
        """Invalid group_id ending with hyphen."""
        with pytest.raises(ValueError):
            SpendingGroup(
                group_id="test-group-",
                name="Test Group",
                total_budget=Decimal("10000.00"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_group_dynamodb_round_trip(self):
        """DynamoDB round-trip preserves group data."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            description="Test description",
            total_budget=Decimal("10000.00"),
            active=True,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        item = group.to_dynamodb_item()

        # Verify DynamoDB key pattern
        assert item["PK"] == "GROUP#test-group"
        assert item["SK"] == "METADATA"

        # Round-trip
        restored = SpendingGroup.from_dynamodb_item(item)
        assert restored.group_id == group.group_id
        assert restored.name == group.name
        assert restored.description == group.description
        assert restored.total_budget == group.total_budget
        assert restored.active == group.active

    def test_group_decimal_precision_preserved(self):
        """Decimal precision preserved through DynamoDB round-trip."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            total_budget=Decimal("5000.50"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        item = group.to_dynamodb_item()
        restored = SpendingGroup.from_dynamodb_item(item)
        assert restored.total_budget == Decimal("5000.50")


class TestThresholdConfig:
    """Test ThresholdConfig model validation and serialization."""

    def test_valid_absolute_threshold_with_amount(self):
        """Valid absolute threshold with amount."""
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="group-1",
            threshold_type="absolute",
            absolute_amount=Decimal("5000.00"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        assert threshold.threshold_type == "absolute"
        assert threshold.absolute_amount == Decimal("5000.00")

    def test_valid_percentage_threshold_with_value_zero(self):
        """Valid percentage threshold with value 0."""
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="group-1",
            threshold_type="percentage",
            percentage_value=Decimal("0"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        assert threshold.threshold_type == "percentage"
        assert threshold.percentage_value == Decimal("0")

    def test_valid_percentage_threshold_with_value_50(self):
        """Valid percentage threshold with value 50."""
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="group-1",
            threshold_type="percentage",
            percentage_value=Decimal("50"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        assert threshold.percentage_value == Decimal("50")

    def test_valid_percentage_threshold_with_value_100(self):
        """Valid percentage threshold with value 100."""
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="group-1",
            threshold_type="percentage",
            percentage_value=Decimal("100"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        assert threshold.percentage_value == Decimal("100")

    def test_invalid_percentage_threshold_value_over_100(self):
        """Invalid percentage threshold with value > 100."""
        with pytest.raises(ValueError):
            ThresholdConfig(
                threshold_id="thresh-1",
                group_id="group-1",
                threshold_type="percentage",
                percentage_value=Decimal("101"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_invalid_percentage_threshold_value_negative(self):
        """Invalid percentage threshold with value < 0."""
        with pytest.raises(ValueError):
            ThresholdConfig(
                threshold_id="thresh-1",
                group_id="group-1",
                threshold_type="percentage",
                percentage_value=Decimal("-1"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_valid_fair_share_threshold_no_extra_fields(self):
        """Valid fair_share threshold with no extra fields."""
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="group-1",
            threshold_type="fair_share",
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        assert threshold.threshold_type == "fair_share"
        assert threshold.absolute_amount is None
        assert threshold.percentage_value is None

    def test_invalid_absolute_threshold_missing_amount(self):
        """Invalid absolute threshold missing absolute_amount."""
        with pytest.raises(ValueError):
            ThresholdConfig(
                threshold_id="thresh-1",
                group_id="group-1",
                threshold_type="absolute",
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_invalid_percentage_threshold_missing_value(self):
        """Invalid percentage threshold missing percentage_value."""
        with pytest.raises(ValueError):
            ThresholdConfig(
                threshold_id="thresh-1",
                group_id="group-1",
                threshold_type="percentage",
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            )

    def test_threshold_dynamodb_round_trip(self):
        """DynamoDB round-trip preserves threshold data."""
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="group-1",
            threshold_type="absolute",
            absolute_amount=Decimal("5000.00"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        item = threshold.to_dynamodb_item()

        # Verify DynamoDB key pattern
        assert item["PK"] == "THRESHOLD#thresh-1"
        assert item["SK"] == "GROUP#group-1"

        # Round-trip
        restored = ThresholdConfig.from_dynamodb_item(item)
        assert restored.threshold_id == threshold.threshold_id
        assert restored.group_id == threshold.group_id
        assert restored.threshold_type == threshold.threshold_type
        assert restored.absolute_amount == threshold.absolute_amount

    def test_threshold_decimal_precision_preserved(self):
        """Decimal precision preserved through DynamoDB round-trip."""
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="group-1",
            threshold_type="absolute",
            absolute_amount=Decimal("5000.50"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        item = threshold.to_dynamodb_item()
        restored = ThresholdConfig.from_dynamodb_item(item)
        assert restored.absolute_amount == Decimal("5000.50")

    def test_threshold_config_default_re_enable_threshold_pct(self):
        """ThresholdConfig re_enable_threshold_pct defaults to None."""
        t = ThresholdConfig(
            threshold_id="t1",
            group_id="g1",
            threshold_type="fair_share",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert t.re_enable_threshold_pct is None

    def test_threshold_config_default_fairness_metric(self):
        """ThresholdConfig fairness_metric defaults to 'combined'."""
        t = ThresholdConfig(
            threshold_id="t1",
            group_id="g1",
            threshold_type="fair_share",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert t.fairness_metric == "combined"

    def test_threshold_config_re_enable_threshold_pct_serializes(self):
        """ThresholdConfig re_enable_threshold_pct serializes to DynamoDB item."""
        t = ThresholdConfig(
            threshold_id="t1",
            group_id="g1",
            threshold_type="fair_share",
            re_enable_threshold_pct=Decimal("80"),
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        item = t.to_dynamodb_item()
        assert "re_enable_threshold_pct" in item
        assert item["re_enable_threshold_pct"] == "80"

    def test_threshold_config_fairness_metric_sp_only(self):
        """ThresholdConfig fairness_metric 'sp_only' is valid."""
        t = ThresholdConfig(
            threshold_id="t1",
            group_id="g1",
            threshold_type="fair_share",
            fairness_metric="sp_only",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert t.fairness_metric == "sp_only"
        item = t.to_dynamodb_item()
        assert item["fairness_metric"] == "sp_only"
