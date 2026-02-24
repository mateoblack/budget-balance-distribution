"""Unit tests for enforcement business logic."""
import pytest
from unittest.mock import Mock, patch
from decimal import Decimal


class TestBuildCostCategoryRules:
    """Test build_cost_category_rules function."""

    def test_both_enabled_and_disabled_accounts(self):
        """Test with both enabled and disabled accounts returns 2 rules."""
        import sys
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        enabled = ["111111111111", "222222222222"]
        disabled = ["333333333333"]

        rules = cost_category.build_cost_category_rules(enabled, disabled)

        assert len(rules) == 2

        # First rule should be RISP_ENABLED
        assert rules[0]["Value"] == "RISP_ENABLED"
        assert rules[0]["Type"] == "REGULAR"
        assert rules[0]["Rule"]["Dimensions"]["Key"] == "LINKED_ACCOUNT"
        assert rules[0]["Rule"]["Dimensions"]["Values"] == enabled

        # Second rule should be RISP_DISABLED
        assert rules[1]["Value"] == "RISP_DISABLED"
        assert rules[1]["Type"] == "REGULAR"
        assert rules[1]["Rule"]["Dimensions"]["Key"] == "LINKED_ACCOUNT"
        assert rules[1]["Rule"]["Dimensions"]["Values"] == disabled

    def test_only_enabled_accounts(self):
        """Test with only enabled accounts returns 1 rule (RISP_ENABLED only)."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        enabled = ["111111111111", "222222222222"]
        disabled = []

        rules = cost_category.build_cost_category_rules(enabled, disabled)

        assert len(rules) == 1
        assert rules[0]["Value"] == "RISP_ENABLED"
        assert rules[0]["Rule"]["Dimensions"]["Values"] == enabled

    def test_only_disabled_accounts(self):
        """Test with only disabled accounts returns 1 rule (RISP_DISABLED only)."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        enabled = []
        disabled = ["333333333333", "444444444444"]

        rules = cost_category.build_cost_category_rules(enabled, disabled)

        assert len(rules) == 1
        assert rules[0]["Value"] == "RISP_DISABLED"
        assert rules[0]["Rule"]["Dimensions"]["Values"] == disabled

    def test_both_lists_empty(self):
        """Test with both lists empty returns empty list."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        rules = cost_category.build_cost_category_rules([], [])

        assert rules == []

    def test_account_ids_appear_in_correct_values_list(self):
        """Test account IDs appear in correct Values list."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        enabled = ["111111111111"]
        disabled = ["999999999999"]

        rules = cost_category.build_cost_category_rules(enabled, disabled)

        # Verify account IDs are in the correct rules
        enabled_rule = next((r for r in rules if r["Value"] == "RISP_ENABLED"), None)
        disabled_rule = next((r for r in rules if r["Value"] == "RISP_DISABLED"), None)

        assert enabled_rule is not None
        assert disabled_rule is not None
        assert "111111111111" in enabled_rule["Rule"]["Dimensions"]["Values"]
        assert "999999999999" in disabled_rule["Rule"]["Dimensions"]["Values"]


class TestDetermineEnforcementActions:
    """Test determine_enforcement_actions function."""

    def test_account_over_threshold_goes_to_disable_list(self):
        """Test account over threshold goes to disable list."""
        import importlib
        enforcement = importlib.import_module('lambda.enforcement.enforcement')

        per_account_usage = [
            {"account_id": "111111111111", "estimated_discount_benefit": 6000.00}
        ]
        account_thresholds = {
            "111111111111": Decimal("5000.00")
        }

        actions = enforcement.determine_enforcement_actions(per_account_usage, account_thresholds)

        assert actions["disable"] == ["111111111111"]
        assert actions["enable"] == []

    def test_account_under_threshold_goes_to_enable_list(self):
        """Test account under threshold goes to enable list."""
        import importlib
        enforcement = importlib.import_module('lambda.enforcement.enforcement')

        per_account_usage = [
            {"account_id": "222222222222", "estimated_discount_benefit": 3000.00}
        ]
        account_thresholds = {
            "222222222222": Decimal("5000.00")
        }

        actions = enforcement.determine_enforcement_actions(per_account_usage, account_thresholds)

        assert actions["enable"] == ["222222222222"]
        assert actions["disable"] == []

    def test_account_exactly_at_threshold_stays_enabled(self):
        """Test account exactly at threshold stays enabled (not strictly greater than)."""
        import importlib
        enforcement = importlib.import_module('lambda.enforcement.enforcement')

        per_account_usage = [
            {"account_id": "333333333333", "estimated_discount_benefit": 5000.00}
        ]
        account_thresholds = {
            "333333333333": Decimal("5000.00")
        }

        actions = enforcement.determine_enforcement_actions(per_account_usage, account_thresholds)

        assert actions["enable"] == ["333333333333"]
        assert actions["disable"] == []

    def test_account_with_no_threshold_config_excluded(self):
        """Test account with no threshold config is excluded from both lists."""
        import importlib
        enforcement = importlib.import_module('lambda.enforcement.enforcement')

        per_account_usage = [
            {"account_id": "444444444444", "estimated_discount_benefit": 2000.00}
        ]
        account_thresholds = {}  # No threshold for this account

        actions = enforcement.determine_enforcement_actions(per_account_usage, account_thresholds)

        assert actions["enable"] == []
        assert actions["disable"] == []

    def test_mixed_scenario(self):
        """Test mixed scenario with some over, some under, some missing threshold."""
        import importlib
        enforcement = importlib.import_module('lambda.enforcement.enforcement')

        per_account_usage = [
            {"account_id": "111111111111", "estimated_discount_benefit": 6000.00},  # Over
            {"account_id": "222222222222", "estimated_discount_benefit": 3000.00},  # Under
            {"account_id": "333333333333", "estimated_discount_benefit": 1000.00},  # No threshold
            {"account_id": "444444444444", "estimated_discount_benefit": 5000.00},  # Exactly at
        ]
        account_thresholds = {
            "111111111111": Decimal("5000.00"),
            "222222222222": Decimal("4000.00"),
            "444444444444": Decimal("5000.00"),
            # 333333333333 missing
        }

        actions = enforcement.determine_enforcement_actions(per_account_usage, account_thresholds)

        assert set(actions["enable"]) == {"222222222222", "444444444444"}
        assert actions["disable"] == ["111111111111"]
        # 333333333333 should not appear in either list

    def test_empty_inputs_return_empty_lists(self):
        """Test empty inputs return empty lists."""
        import importlib
        enforcement = importlib.import_module('lambda.enforcement.enforcement')

        actions = enforcement.determine_enforcement_actions([], {})

        assert actions["enable"] == []
        assert actions["disable"] == []


class TestDetermineEnforcementActionsHysteresis:
    """Test hysteresis band behavior in determine_enforcement_actions."""

    def test_hysteresis_band_no_action(self):
        """Account in hysteresis band (between re_enable and disable thresholds) gets no action."""
        import importlib
        enforcement = importlib.import_module('lambda.enforcement.enforcement')

        per_account_usage = [
            {"account_id": "111111111111", "estimated_discount_benefit": 110.0},
        ]
        # disable at 120, re-enable at 80 — account at 110 is in the band
        account_thresholds = {"111111111111": Decimal("120")}
        account_re_enable_thresholds = {"111111111111": Decimal("80")}

        result = enforcement.determine_enforcement_actions(
            per_account_usage, account_thresholds, account_re_enable_thresholds
        )

        assert "111111111111" not in result["enable"]
        assert "111111111111" not in result["disable"]

    def test_hysteresis_band_enables_below_re_enable_threshold(self):
        """Account below re_enable_threshold (well under both) gets enabled."""
        import importlib
        enforcement = importlib.import_module('lambda.enforcement.enforcement')

        per_account_usage = [
            {"account_id": "222222222222", "estimated_discount_benefit": 50.0},
        ]
        account_thresholds = {"222222222222": Decimal("120")}
        account_re_enable_thresholds = {"222222222222": Decimal("80")}

        result = enforcement.determine_enforcement_actions(
            per_account_usage, account_thresholds, account_re_enable_thresholds
        )

        assert "222222222222" in result["enable"]
        assert "222222222222" not in result["disable"]

    def test_hysteresis_band_disables_above_disable_threshold(self):
        """Account above disable_threshold gets disabled even with hysteresis."""
        import importlib
        enforcement = importlib.import_module('lambda.enforcement.enforcement')

        per_account_usage = [
            {"account_id": "333333333333", "estimated_discount_benefit": 150.0},
        ]
        account_thresholds = {"333333333333": Decimal("120")}
        account_re_enable_thresholds = {"333333333333": Decimal("80")}

        result = enforcement.determine_enforcement_actions(
            per_account_usage, account_thresholds, account_re_enable_thresholds
        )

        assert "333333333333" in result["disable"]
        assert "333333333333" not in result["enable"]

    def test_no_re_enable_thresholds_backward_compatible(self):
        """When account_re_enable_thresholds is None, behavior is identical to v1.0."""
        import importlib
        enforcement = importlib.import_module('lambda.enforcement.enforcement')

        per_account_usage = [
            {"account_id": "444444444444", "estimated_discount_benefit": 80.0},
            {"account_id": "555555555555", "estimated_discount_benefit": 130.0},
        ]
        account_thresholds = {
            "444444444444": Decimal("100"),
            "555555555555": Decimal("100"),
        }

        result = enforcement.determine_enforcement_actions(per_account_usage, account_thresholds)

        assert "444444444444" in result["enable"]
        assert "555555555555" in result["disable"]


class TestUpdateRispSharingGroups:
    """Test update_risp_sharing_groups function."""

    def test_dry_run_returns_dry_run_status_without_api_call(self):
        """Test dry_run=True returns DRY_RUN status without calling API."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        mock_ce_client = Mock()
        enabled = ["111111111111"]
        disabled = ["222222222222"]

        result = cost_category.update_risp_sharing_groups(
            mock_ce_client,
            "arn:aws:ce::123456789012:costcategory/abc123",
            "RISP_Sharing_Groups",
            enabled,
            disabled,
            dry_run=True
        )

        # Should NOT call update_cost_category_definition
        mock_ce_client.update_cost_category_definition.assert_not_called()

        # Should return DRY_RUN status
        assert result["status"] == "DRY_RUN"
        assert "rules" in result
        assert result["enabled_count"] == 1
        assert result["disabled_count"] == 1

    def test_execute_calls_update_cost_category_definition(self):
        """Test dry_run=False calls update_cost_category_definition with correct parameters."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        mock_ce_client = Mock()
        mock_ce_client.update_cost_category_definition.return_value = {
            "CostCategoryArn": "arn:aws:ce::123456789012:costcategory/abc123",
            "EffectiveStart": "2026-02-01T00:00:00Z"
        }

        cost_category_arn = "arn:aws:ce::123456789012:costcategory/abc123"
        cost_category_name = "RISP_Sharing_Groups"
        enabled = ["111111111111"]
        disabled = ["222222222222"]

        result = cost_category.update_risp_sharing_groups(
            mock_ce_client,
            cost_category_arn,
            cost_category_name,
            enabled,
            disabled,
            dry_run=False
        )

        # Should call update_cost_category_definition
        assert mock_ce_client.update_cost_category_definition.called

        # Verify call arguments
        call_args = mock_ce_client.update_cost_category_definition.call_args
        assert call_args[1]["CostCategoryArn"] == cost_category_arn
        assert call_args[1]["RuleVersion"] == "CostCategoryExpression.v1"
        assert call_args[1]["DefaultValue"] == "RISP_DISABLED"

        # Should return API response
        assert result["CostCategoryArn"] == cost_category_arn
        assert "EffectiveStart" in result

    def test_default_value_is_risp_disabled(self):
        """Test DefaultValue is 'RISP_DISABLED' (fail-closed)."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        mock_ce_client = Mock()
        mock_ce_client.update_cost_category_definition.return_value = {}

        cost_category.update_risp_sharing_groups(
            mock_ce_client,
            "arn:aws:ce::123456789012:costcategory/abc123",
            "RISP_Sharing_Groups",
            [],
            [],
            dry_run=False
        )

        # Extract the call arguments
        call_args = mock_ce_client.update_cost_category_definition.call_args
        assert call_args[1]["DefaultValue"] == "RISP_DISABLED"


class TestExtractPreviousState:
    """Test extract_previous_state function."""

    def test_extract_both_enabled_and_disabled(self):
        """Test snapshot with both RISP_ENABLED and RISP_DISABLED rules returns both lists correctly."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        snapshot = {
            "rules": [
                {
                    "Value": "RISP_ENABLED",
                    "Rule": {
                        "Dimensions": {
                            "Key": "LINKED_ACCOUNT",
                            "Values": ["111111111111", "222222222222"]
                        }
                    },
                    "Type": "REGULAR"
                },
                {
                    "Value": "RISP_DISABLED",
                    "Rule": {
                        "Dimensions": {
                            "Key": "LINKED_ACCOUNT",
                            "Values": ["333333333333"]
                        }
                    },
                    "Type": "REGULAR"
                }
            ]
        }

        result = cost_category.extract_previous_state(snapshot)

        assert result["enabled"] == ["111111111111", "222222222222"]
        assert result["disabled"] == ["333333333333"]

    def test_extract_only_enabled(self):
        """Test snapshot with only RISP_ENABLED rule returns enabled accounts and empty disabled list."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        snapshot = {
            "rules": [
                {
                    "Value": "RISP_ENABLED",
                    "Rule": {
                        "Dimensions": {
                            "Key": "LINKED_ACCOUNT",
                            "Values": ["111111111111", "222222222222"]
                        }
                    },
                    "Type": "REGULAR"
                }
            ]
        }

        result = cost_category.extract_previous_state(snapshot)

        assert result["enabled"] == ["111111111111", "222222222222"]
        assert result["disabled"] == []

    def test_extract_only_disabled(self):
        """Test snapshot with only RISP_DISABLED rule returns empty enabled list and disabled accounts."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        snapshot = {
            "rules": [
                {
                    "Value": "RISP_DISABLED",
                    "Rule": {
                        "Dimensions": {
                            "Key": "LINKED_ACCOUNT",
                            "Values": ["333333333333", "444444444444"]
                        }
                    },
                    "Type": "REGULAR"
                }
            ]
        }

        result = cost_category.extract_previous_state(snapshot)

        assert result["enabled"] == []
        assert result["disabled"] == ["333333333333", "444444444444"]

    def test_extract_empty_rules(self):
        """Test snapshot with empty rules list returns both lists empty."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        snapshot = {"rules": []}

        result = cost_category.extract_previous_state(snapshot)

        assert result["enabled"] == []
        assert result["disabled"] == []

    def test_extract_no_rules_key(self):
        """Test snapshot without 'rules' key returns both lists empty (defensive)."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        snapshot = {}

        result = cost_category.extract_previous_state(snapshot)

        assert result["enabled"] == []
        assert result["disabled"] == []

    def test_extract_ignores_other_values(self):
        """Test snapshot with rules having other Value strings (not RISP_ENABLED/DISABLED) are ignored."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        snapshot = {
            "rules": [
                {
                    "Value": "RISP_ENABLED",
                    "Rule": {
                        "Dimensions": {
                            "Key": "LINKED_ACCOUNT",
                            "Values": ["111111111111"]
                        }
                    },
                    "Type": "REGULAR"
                },
                {
                    "Value": "OTHER_CATEGORY",
                    "Rule": {
                        "Dimensions": {
                            "Key": "LINKED_ACCOUNT",
                            "Values": ["999999999999"]
                        }
                    },
                    "Type": "REGULAR"
                },
                {
                    "Value": "RISP_DISABLED",
                    "Rule": {
                        "Dimensions": {
                            "Key": "LINKED_ACCOUNT",
                            "Values": ["222222222222"]
                        }
                    },
                    "Type": "REGULAR"
                }
            ]
        }

        result = cost_category.extract_previous_state(snapshot)

        assert result["enabled"] == ["111111111111"]
        assert result["disabled"] == ["222222222222"]
        # 999999999999 should not appear in either list


class TestCaptureCostCategorySnapshot:
    """Test capture_cost_category_snapshot function."""

    def test_snapshot_captures_all_fields(self):
        """Test snapshot captures all fields from describe response."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        mock_ce_client = Mock()
        mock_ce_client.describe_cost_category_definition.return_value = {
            "CostCategory": {
                "CostCategoryArn": "arn:aws:ce::123456789012:costcategory/abc123",
                "Name": "RISP_Sharing_Groups",
                "RuleVersion": "CostCategoryExpression.v1",
                "Rules": [
                    {"Value": "RISP_ENABLED", "Rule": {}, "Type": "REGULAR"}
                ],
                "DefaultValue": "RISP_DISABLED",
                "EffectiveStart": "2026-02-01T00:00:00Z"
            }
        }

        mock_audit_table = Mock()
        cost_category_arn = "arn:aws:ce::123456789012:costcategory/abc123"

        snapshot = cost_category.capture_cost_category_snapshot(
            mock_ce_client,
            mock_audit_table,
            cost_category_arn
        )

        # Verify describe was called
        mock_ce_client.describe_cost_category_definition.assert_called_once_with(
            CostCategoryArn=cost_category_arn
        )

        # Verify snapshot structure
        assert snapshot["PK"] == f"SNAPSHOT#{cost_category_arn}"
        assert "SK" in snapshot  # Timestamp
        assert snapshot["entity_type"] == "COST_CATEGORY_SNAPSHOT"
        assert snapshot["cost_category_arn"] == cost_category_arn
        assert snapshot["name"] == "RISP_Sharing_Groups"
        assert snapshot["rule_version"] == "CostCategoryExpression.v1"
        assert len(snapshot["rules"]) == 1
        assert snapshot["default_value"] == "RISP_DISABLED"
        assert snapshot["effective_start"] == "2026-02-01T00:00:00Z"
        assert "captured_at" in snapshot

        # Verify snapshot was written to audit table
        mock_audit_table.put_item.assert_called_once()
        written_item = mock_audit_table.put_item.call_args[1]["Item"]
        assert written_item["PK"] == snapshot["PK"]

    def test_snapshot_pk_format(self):
        """Test snapshot PK format is 'SNAPSHOT#{arn}'."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        mock_ce_client = Mock()
        mock_ce_client.describe_cost_category_definition.return_value = {
            "CostCategory": {
                "CostCategoryArn": "arn:aws:ce::123456789012:costcategory/xyz789",
                "Name": "Test",
                "RuleVersion": "v1",
                "Rules": [],
                "EffectiveStart": "2026-02-01T00:00:00Z"
            }
        }

        mock_audit_table = Mock()

        snapshot = cost_category.capture_cost_category_snapshot(
            mock_ce_client,
            mock_audit_table,
            "arn:aws:ce::123456789012:costcategory/xyz789"
        )

        assert snapshot["PK"] == "SNAPSHOT#arn:aws:ce::123456789012:costcategory/xyz789"

    def test_snapshot_written_to_audit_table(self):
        """Test snapshot is written to audit table."""
        import importlib
        cost_category = importlib.import_module('lambda.enforcement.cost_category')

        mock_ce_client = Mock()
        mock_ce_client.describe_cost_category_definition.return_value = {
            "CostCategory": {
                "CostCategoryArn": "arn:aws:ce::123456789012:costcategory/abc",
                "Name": "Test",
                "RuleVersion": "v1",
                "Rules": [],
                "EffectiveStart": "2026-02-01T00:00:00Z"
            }
        }

        mock_audit_table = Mock()

        cost_category.capture_cost_category_snapshot(
            mock_ce_client,
            mock_audit_table,
            "arn:aws:ce::123456789012:costcategory/abc"
        )

        # Verify put_item was called once
        mock_audit_table.put_item.assert_called_once()
        call_args = mock_audit_table.put_item.call_args
        assert "Item" in call_args[1]
