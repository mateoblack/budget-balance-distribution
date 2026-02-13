"""Tests for threshold calculation engine."""
import pytest
from decimal import Decimal
from shared.models import AccountConfig, SpendingGroup, ThresholdConfig
from shared.threshold import (
    calculate_fair_share_threshold,
    calculate_threshold_for_group,
    calculate_effective_threshold,
)


class TestFairShareThreshold:
    """Test fair share threshold calculation."""

    def test_fair_share_basic_calculation(self):
        """Fair share: 10000 budget / 4 accounts = 2500."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            total_budget=Decimal("10000"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        result = calculate_fair_share_threshold(group, 4)
        assert result == Decimal("2500")

    def test_fair_share_single_account(self):
        """Fair share: 10000 budget / 1 account = 10000."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            total_budget=Decimal("10000"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        result = calculate_fair_share_threshold(group, 1)
        assert result == Decimal("10000")

    def test_fair_share_zero_accounts(self):
        """Fair share: 0 active accounts = 0 (no crash)."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            total_budget=Decimal("10000"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        result = calculate_fair_share_threshold(group, 0)
        assert result == Decimal("0")

    def test_fair_share_zero_budget(self):
        """Fair share: 0 budget = 0."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            total_budget=Decimal("0.01"),  # Use small positive value to pass validation
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        # Manually set to zero for test (bypassing validation)
        group.total_budget = Decimal("0")
        result = calculate_fair_share_threshold(group, 4)
        assert result == Decimal("0")


class TestThresholdForGroup:
    """Test threshold calculation dispatch by type."""

    def test_absolute_returns_amount_directly(self):
        """Absolute: returns amount directly (5000 -> 5000)."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            total_budget=Decimal("10000"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="test-group",
            threshold_type="absolute",
            absolute_amount=Decimal("5000"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        result = calculate_threshold_for_group(threshold, group, 4)
        assert result == Decimal("5000")

    def test_percentage_15_of_10000(self):
        """Percentage: 15% of 10000 = 1500."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            total_budget=Decimal("10000"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="test-group",
            threshold_type="percentage",
            percentage_value=Decimal("15"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        result = calculate_threshold_for_group(threshold, group, 4)
        assert result == Decimal("1500")

    def test_percentage_100_of_5000(self):
        """Percentage: 100% of 5000 = 5000."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            total_budget=Decimal("5000"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="test-group",
            threshold_type="percentage",
            percentage_value=Decimal("100"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        result = calculate_threshold_for_group(threshold, group, 4)
        assert result == Decimal("5000")

    def test_percentage_0_of_anything(self):
        """Percentage: 0% of anything = 0."""
        group = SpendingGroup(
            group_id="test-group",
            name="Test Group",
            total_budget=Decimal("10000"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        threshold = ThresholdConfig(
            threshold_id="thresh-1",
            group_id="test-group",
            threshold_type="percentage",
            percentage_value=Decimal("0"),
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        result = calculate_threshold_for_group(threshold, group, 4)
        assert result == Decimal("0")


class TestEffectiveThreshold:
    """Test effective threshold calculation with most-restrictive-wins."""

    def test_most_restrictive_two_groups_absolute(self):
        """Most restrictive wins: account in 2 groups, one gives 5000, other gives 3000 -> returns 3000."""
        account = AccountConfig(
            account_id="123456789012",
            group_memberships=["group-1", "group-2"],
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )

        groups = [
            SpendingGroup(
                group_id="group-1",
                name="Group 1",
                total_budget=Decimal("10000"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
            SpendingGroup(
                group_id="group-2",
                name="Group 2",
                total_budget=Decimal("8000"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
        ]

        thresholds = [
            ThresholdConfig(
                threshold_id="thresh-1",
                group_id="group-1",
                threshold_type="absolute",
                absolute_amount=Decimal("5000"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
            ThresholdConfig(
                threshold_id="thresh-2",
                group_id="group-2",
                threshold_type="absolute",
                absolute_amount=Decimal("3000"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
        ]

        all_accounts = [account]

        result = calculate_effective_threshold(account, groups, thresholds, all_accounts)
        assert result == Decimal("3000")

    def test_most_restrictive_absolute_vs_fair_share(self):
        """Most restrictive wins: absolute=5000 vs fair_share=2500 -> returns 2500."""
        account = AccountConfig(
            account_id="123456789012",
            group_memberships=["group-1", "group-2"],
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )

        # Create additional accounts for group-2 to test fair share
        account2 = AccountConfig(
            account_id="123456789013",
            group_memberships=["group-2"],
            active=True,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        account3 = AccountConfig(
            account_id="123456789014",
            group_memberships=["group-2"],
            active=True,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        account4 = AccountConfig(
            account_id="123456789015",
            group_memberships=["group-2"],
            active=True,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )

        groups = [
            SpendingGroup(
                group_id="group-1",
                name="Group 1",
                total_budget=Decimal("10000"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
            SpendingGroup(
                group_id="group-2",
                name="Group 2",
                total_budget=Decimal("10000"),  # 10000 / 4 accounts = 2500
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
        ]

        thresholds = [
            ThresholdConfig(
                threshold_id="thresh-1",
                group_id="group-1",
                threshold_type="absolute",
                absolute_amount=Decimal("5000"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
            ThresholdConfig(
                threshold_id="thresh-2",
                group_id="group-2",
                threshold_type="fair_share",
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
        ]

        all_accounts = [account, account2, account3, account4]

        result = calculate_effective_threshold(account, groups, thresholds, all_accounts)
        assert result == Decimal("2500")

    def test_account_references_nonexistent_group(self):
        """Account references non-existent group -> skips it, returns threshold from valid group."""
        account = AccountConfig(
            account_id="123456789012",
            group_memberships=["group-1", "nonexistent-group"],
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )

        groups = [
            SpendingGroup(
                group_id="group-1",
                name="Group 1",
                total_budget=Decimal("10000"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
        ]

        thresholds = [
            ThresholdConfig(
                threshold_id="thresh-1",
                group_id="group-1",
                threshold_type="absolute",
                absolute_amount=Decimal("5000"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
        ]

        all_accounts = [account]

        result = calculate_effective_threshold(account, groups, thresholds, all_accounts)
        assert result == Decimal("5000")

    def test_account_with_no_valid_thresholds(self):
        """Account with no valid thresholds -> returns 0."""
        account = AccountConfig(
            account_id="123456789012",
            group_memberships=["group-1"],
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )

        groups = [
            SpendingGroup(
                group_id="group-1",
                name="Group 1",
                total_budget=Decimal("10000"),
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
        ]

        thresholds = []  # No thresholds configured

        all_accounts = [account]

        result = calculate_effective_threshold(account, groups, thresholds, all_accounts)
        assert result == Decimal("0")

    def test_only_active_accounts_counted_for_fair_share(self):
        """Only active accounts counted for fair-share: 5 accounts but 2 inactive -> fair_share uses 3."""
        account = AccountConfig(
            account_id="123456789012",
            group_memberships=["group-1"],
            active=True,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )

        # 2 more active accounts
        account2 = AccountConfig(
            account_id="123456789013",
            group_memberships=["group-1"],
            active=True,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        account3 = AccountConfig(
            account_id="123456789014",
            group_memberships=["group-1"],
            active=True,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )

        # 2 inactive accounts
        account4 = AccountConfig(
            account_id="123456789015",
            group_memberships=["group-1"],
            active=False,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )
        account5 = AccountConfig(
            account_id="123456789016",
            group_memberships=["group-1"],
            active=False,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
        )

        groups = [
            SpendingGroup(
                group_id="group-1",
                name="Group 1",
                total_budget=Decimal("9000"),  # 9000 / 3 active = 3000
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
        ]

        thresholds = [
            ThresholdConfig(
                threshold_id="thresh-1",
                group_id="group-1",
                threshold_type="fair_share",
                created_at="2026-02-12T00:00:00Z",
                updated_at="2026-02-12T00:00:00Z",
            ),
        ]

        all_accounts = [account, account2, account3, account4, account5]

        result = calculate_effective_threshold(account, groups, thresholds, all_accounts)
        assert result == Decimal("3000")
