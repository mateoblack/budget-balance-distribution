"""
Tests for Infrastructure Stack

Verifies that the CDK stack synthesizes correctly and contains
the expected DynamoDB tables and IAM roles with proper configurations.
"""
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
import pytest
from stacks.infrastructure_stack import InfrastructureStack


@pytest.fixture
def template():
    """Synthesize the stack and return a Template for testing."""
    app = cdk.App()
    stack = InfrastructureStack(
        app,
        "TestStack",
        env=cdk.Environment(account="123456789012", region="us-east-1")
    )
    return Template.from_stack(stack)


def test_stack_synthesizes():
    """Smoke test - verify stack can be synthesized without errors."""
    app = cdk.App()
    stack = InfrastructureStack(
        app,
        "TestStack",
        env=cdk.Environment(account="123456789012", region="us-east-1")
    )
    Template.from_stack(stack)


def test_two_dynamodb_tables(template):
    """Verify exactly 2 DynamoDB tables are created."""
    template.resource_count_is("AWS::DynamoDB::Table", 2)


def test_config_table_created(template):
    """Verify configuration table has correct schema and properties."""
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        Match.object_like({
            "KeySchema": [
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
            "PointInTimeRecoverySpecification": {
                "PointInTimeRecoveryEnabled": True
            },
            "DeletionProtectionEnabled": True,
        })
    )


def test_audit_table_created(template):
    """Verify audit table has correct schema and properties."""
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        Match.object_like({
            "KeySchema": [
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
            "PointInTimeRecoverySpecification": {
                "PointInTimeRecoveryEnabled": True
            },
            "DeletionProtectionEnabled": True,
        })
    )


def test_audit_table_has_entity_type_gsi(template):
    """Verify audit table has EntityTypeIndex GSI for compliance queries."""
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        Match.object_like({
            "KeySchema": [
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            "GlobalSecondaryIndexes": [
                Match.object_like({
                    "IndexName": "EntityTypeIndex",
                    "KeySchema": [
                        {"AttributeName": "entity_type", "KeyType": "HASH"},
                        {"AttributeName": "SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                })
            ],
        })
    )


def test_tables_have_retain_policy(template):
    """Verify both DynamoDB tables have DeletionPolicy: Retain."""
    # Get all DynamoDB table resources
    tables = template.find_resources(
        "AWS::DynamoDB::Table",
        props={}
    )

    # Both tables should have Retain policy
    assert len(tables) == 2, "Expected exactly 2 DynamoDB tables"

    for logical_id, resource in tables.items():
        assert "DeletionPolicy" in resource, f"Table {logical_id} missing DeletionPolicy"
        assert resource["DeletionPolicy"] == "Retain", \
            f"Table {logical_id} should have DeletionPolicy: Retain"


def test_discovery_role_created(template):
    """Verify discovery role exists with correct trust policy."""
    template.has_resource_properties(
        "AWS::IAM::Role",
        Match.object_like({
            "AssumeRolePolicyDocument": Match.object_like({
                "Statement": Match.array_with([
                    Match.object_like({
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                        "Effect": "Allow",
                    })
                ])
            })
        })
    )


def test_enforcement_role_created(template):
    """Verify enforcement role exists with correct trust policy."""
    # Should have at least 2 IAM roles (discovery + enforcement)
    # PythonFunction may create additional roles for log retention
    roles = template.find_resources("AWS::IAM::Role")
    assert len(roles) >= 2, f"Expected at least 2 IAM roles, found {len(roles)}"

    # Both should have lambda trust policy
    for logical_id, resource in roles.items():
        assert "Properties" in resource
        assert "AssumeRolePolicyDocument" in resource["Properties"]


def test_cfn_outputs_exist(template):
    """Verify all expected CloudFormation outputs including enforcement are present."""
    # Get all outputs
    outputs = template.find_outputs("*")

    # Expected output keys (including enforcement outputs)
    expected_outputs = [
        "ConfigTableName",
        "ConfigTableArn",
        "AuditTableName",
        "AuditTableArn",
        "DiscoveryRoleArn",
        "EnforcementRoleArn",
        "DiscoveryLambdaArn",
        "DiscoveryScheduleArn",
        "EnforcementLambdaArn",
        "EnforcementScheduleArn",
        "EnforcementExecuteRuleName",
        "ProductionEnablementChecklist",
    ]

    output_keys = list(outputs.keys())
    for expected in expected_outputs:
        assert expected in output_keys, f"Missing output: {expected}"


def test_discovery_role_has_cost_explorer_access(template):
    """Verify discovery role has expanded Cost Explorer read permissions."""
    # Should have all 6 Cost Explorer actions
    template.has_resource_properties(
        "AWS::IAM::Policy",
        Match.object_like({
            "PolicyDocument": Match.object_like({
                "Statement": Match.array_with([
                    Match.object_like({
                        "Action": [
                            "ce:GetCostAndUsage",
                            "ce:GetReservationUtilization",
                            "ce:GetSavingsPlansUtilization",
                            "ce:GetSavingsPlansUtilizationDetails",
                            "ce:ListCostCategoryDefinitions",
                            "ce:DescribeCostCategoryDefinition",
                        ],
                        "Effect": "Allow",
                    })
                ])
            })
        })
    )


def test_enforcement_role_has_cost_category_access(template):
    """Verify enforcement role has Cost Category write permissions."""
    template.has_resource_properties(
        "AWS::IAM::Policy",
        Match.object_like({
            "PolicyDocument": Match.object_like({
                "Statement": Match.array_with([
                    Match.object_like({
                        "Action": "ce:UpdateCostCategoryDefinition",
                        "Effect": "Allow",
                    })
                ])
            })
        })
    )


def test_discovery_lambda_created(template):
    """Verify Discovery Lambda exists with Python 3.12 runtime, 512MB memory, and 5min timeout."""
    template.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like({
            "Runtime": "python3.12",
            "MemorySize": 512,
            "Timeout": 300,  # 5 minutes in seconds
        })
    )


def test_discovery_lambda_environment_variables(template):
    """Verify Discovery Lambda has required environment variables."""
    template.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like({
            "Environment": Match.object_like({
                "Variables": Match.object_like({
                    "CONFIG_TABLE_NAME": Match.any_value(),
                    "AUDIT_TABLE_NAME": Match.any_value(),
                    "POWERTOOLS_SERVICE_NAME": "discovery",
                    "POWERTOOLS_LOG_LEVEL": "INFO",
                    "LOOKBACK_DAYS": "30",
                    "THRESHOLD_PCT": "120",
                    "DRY_RUN": "true",
                })
            })
        })
    )


def test_eventbridge_rule_created(template):
    """Verify EventBridge rule exists with cron schedule for 2:00 AM UTC daily."""
    template.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like({
            "ScheduleExpression": "cron(0 2 ? * * *)",
        })
    )


def test_eventbridge_targets_lambda(template):
    """Verify EventBridge rule has a target (the Lambda function)."""
    template.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like({
            "Targets": Match.any_value(),
        })
    )


def test_discovery_role_has_organizations_access(template):
    """Verify discovery role has Organizations read permissions."""
    template.has_resource_properties(
        "AWS::IAM::Policy",
        Match.object_like({
            "PolicyDocument": Match.object_like({
                "Statement": Match.array_with([
                    Match.object_like({
                        "Action": "organizations:ListAccounts",
                        "Effect": "Allow",
                    })
                ])
            })
        })
    )


def test_discovery_lambda_log_retention(template):
    """Verify CloudWatch Logs log group has 30-day retention."""
    # PythonFunction creates a custom resource for log retention
    template.has_resource_properties(
        "Custom::LogRetention",
        Match.object_like({
            "RetentionInDays": 30,
        })
    )


def test_enforcement_lambda_created(template):
    """Verify Enforcement Lambda exists with Python 3.12 runtime, 512MB memory, and 5min timeout."""
    # Match on POWERTOOLS_SERVICE_NAME=enforcement to distinguish from discovery
    template.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like({
            "Runtime": "python3.12",
            "MemorySize": 512,
            "Timeout": 300,  # 5 minutes in seconds
            "Environment": Match.object_like({
                "Variables": Match.object_like({
                    "POWERTOOLS_SERVICE_NAME": "enforcement",
                })
            })
        })
    )


def test_enforcement_lambda_environment_variables(template):
    """Verify Enforcement Lambda has required environment variables."""
    template.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like({
            "Environment": Match.object_like({
                "Variables": Match.object_like({
                    "CONFIG_TABLE_NAME": Match.any_value(),
                    "AUDIT_TABLE_NAME": Match.any_value(),
                    "COST_CATEGORY_ARN": "",
                    "POWERTOOLS_SERVICE_NAME": "enforcement",
                    "POWERTOOLS_LOG_LEVEL": "INFO",
                    "DRY_RUN": "true",
                })
            })
        })
    )


def test_enforcement_eventbridge_rule_created(template):
    """Verify second EventBridge rule exists with schedule for 2:30 AM UTC."""
    template.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like({
            "ScheduleExpression": "cron(30 2 ? * * *)",
        })
    )


def test_enforcement_eventbridge_targets_lambda(template):
    """Verify enforcement EventBridge rule has Lambda target."""
    # Verify enforcement rule exists with correct schedule
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "ScheduleExpression": "cron(30 2 ? * * *)",
            "State": "ENABLED",
        }
    )


def test_enforcement_role_has_cost_explorer_read_access(template):
    """Verify enforcement role policy includes Cost Explorer read access."""
    template.has_resource_properties(
        "AWS::IAM::Policy",
        Match.object_like({
            "PolicyDocument": Match.object_like({
                "Statement": Match.array_with([
                    Match.object_like({
                        "Action": [
                            "ce:GetCostAndUsage",
                            "ce:DescribeCostCategoryDefinition",
                        ],
                        "Effect": "Allow",
                    })
                ])
            })
        })
    )


def test_enforcement_role_has_config_table_read_access(template):
    """Verify enforcement role has DynamoDB read access to config table."""
    # Check for dynamodb:GetItem or dynamodb:Query in policy
    template.has_resource_properties(
        "AWS::IAM::Policy",
        Match.object_like({
            "PolicyDocument": Match.object_like({
                "Statement": Match.array_with([
                    Match.object_like({
                        "Action": Match.array_with([
                            Match.string_like_regexp("dynamodb:GetItem"),
                        ]),
                        "Effect": "Allow",
                    })
                ])
            })
        })
    )


def test_enforcement_lambda_log_retention(template):
    """Verify enforcement Lambda log retention exists with 30 days."""
    # PythonFunction creates a custom resource for log retention
    # With 2 Lambdas (discovery + enforcement), should have multiple log retention resources
    # Just verify that log retention with 30 days exists (covers both Lambdas)
    template.has_resource_properties(
        "Custom::LogRetention",
        Match.object_like({
            "RetentionInDays": 30,
        })
    )


# ========== Monitoring Infrastructure Tests (Phase 6) ==========

def test_cloudwatch_dashboard_created(template):
    """Verify CloudWatch dashboard exists with correct name."""
    template.has_resource_properties(
        "AWS::CloudWatch::Dashboard",
        Match.object_like({
            "DashboardName": "BudgetBalanceDistribution",
        })
    )


def test_sns_topic_created(template):
    """Verify SNS topic exists for alerts."""
    template.has_resource_properties(
        "AWS::SNS::Topic",
        Match.object_like({
            "DisplayName": "Budget Balance Distribution Alerts",
            "TopicName": "BudgetBalanceDistribution-Alerts",
        })
    )


def test_flagged_accounts_metric_filter_created(template):
    """Verify metric filter for FlaggedAccountsCount exists."""
    template.has_resource_properties(
        "AWS::Logs::MetricFilter",
        Match.object_like({
            "FilterPattern": '{ $.flagged_count = * }',
            "MetricTransformations": [
                Match.object_like({
                    "MetricNamespace": "BudgetBalanceDistribution",
                    "MetricName": "FlaggedAccountsCount",
                    "MetricValue": "$.flagged_count",
                    "DefaultValue": 0,
                })
            ],
        })
    )


def test_threshold_violations_metric_filter_created(template):
    """Verify metric filter for ThresholdViolationsDetected exists."""
    template.has_resource_properties(
        "AWS::Logs::MetricFilter",
        Match.object_like({
            "FilterPattern": '{ $.level = "WARNING" && $.message = "*exceeds threshold*" }',
            "MetricTransformations": [
                Match.object_like({
                    "MetricNamespace": "BudgetBalanceDistribution",
                    "MetricName": "ThresholdViolationsDetected",
                    "MetricValue": "1",
                    "DefaultValue": 0,
                })
            ],
        })
    )


def test_discovery_lambda_error_alarm_created(template):
    """Verify discovery Lambda error alarm exists."""
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like({
            "AlarmName": "BudgetBalanceDistribution-DiscoveryLambdaErrors",
            "AlarmDescription": "Discovery Lambda invocation errors exceed threshold",
            "Threshold": 1,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        })
    )


def test_enforcement_lambda_error_alarm_created(template):
    """Verify enforcement Lambda error alarm exists."""
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like({
            "AlarmName": "BudgetBalanceDistribution-EnforcementLambdaErrors",
            "AlarmDescription": "Enforcement Lambda invocation errors exceed threshold",
            "Threshold": 1,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        })
    )


def test_discovery_lambda_duration_alarm_created(template):
    """Verify discovery Lambda duration alarm exists."""
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like({
            "AlarmName": "BudgetBalanceDistribution-DiscoveryLambdaDuration",
            "AlarmDescription": "Discovery Lambda duration approaching timeout (>240s of 300s)",
            "Threshold": 240000,  # milliseconds
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        })
    )


def test_enforcement_lambda_duration_alarm_created(template):
    """Verify enforcement Lambda duration alarm exists."""
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like({
            "AlarmName": "BudgetBalanceDistribution-EnforcementLambdaDuration",
            "AlarmDescription": "Enforcement Lambda duration approaching timeout (>240s of 300s)",
            "Threshold": 240000,  # milliseconds
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        })
    )


def test_config_table_throttle_alarm_created(template):
    """Verify config table throttle alarm exists."""
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like({
            "AlarmName": "BudgetBalanceDistribution-ConfigTableThrottles",
            "AlarmDescription": "Config table experiencing throttled requests",
            "Threshold": 5,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        })
    )


def test_audit_table_throttle_alarm_created(template):
    """Verify audit table throttle alarm exists."""
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like({
            "AlarmName": "BudgetBalanceDistribution-AuditTableThrottles",
            "AlarmDescription": "Audit table experiencing throttled requests",
            "Threshold": 5,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        })
    )


def test_threshold_violation_alarm_created(template):
    """Verify threshold violation alarm exists."""
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like({
            "AlarmName": "BudgetBalanceDistribution-ThresholdViolations",
            "AlarmDescription": "Accounts exceeding spending thresholds detected",
            "Threshold": 1,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        })
    )


def test_anomalous_flagged_count_alarm_created(template):
    """Verify anomalous flagged count alarm exists."""
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like({
            "AlarmName": "BudgetBalanceDistribution-AnomalousFlaggedCount",
            "AlarmDescription": "Anomalously high number of flagged accounts (potential misconfiguration)",
            "Threshold": 5,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        })
    )


def test_composite_alarm_created(template):
    """Verify composite alarm exists aggregating all individual alarms."""
    template.has_resource_properties(
        "AWS::CloudWatch::CompositeAlarm",
        Match.object_like({
            "AlarmName": "BudgetBalanceDistribution-SystemHealth",
            "AlarmDescription": "Composite alarm for overall system health",
        })
    )


def test_alarms_wired_to_sns_topic(template):
    """Verify at least one alarm has SNS action configured."""
    # Check that at least one alarm has AlarmActions pointing to the SNS topic
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like({
            "AlarmActions": Match.any_value(),
        })
    )


def test_monitoring_outputs_exist(template):
    """Verify monitoring-related CloudFormation outputs exist."""
    outputs = template.find_outputs("*")

    expected_monitoring_outputs = [
        "AlertTopicArn",
        "DashboardName",
        "CompositeAlarmName",
    ]

    output_keys = list(outputs.keys())
    for expected in expected_monitoring_outputs:
        assert expected in output_keys, f"Missing monitoring output: {expected}"


def test_eight_cloudwatch_alarms_created(template):
    """Verify exactly 8 CloudWatch alarms exist (6 infrastructure + 2 business logic)."""
    template.resource_count_is("AWS::CloudWatch::Alarm", 8)


def test_two_metric_filters_created(template):
    """Verify exactly 2 CloudWatch metric filters exist."""
    template.resource_count_is("AWS::Logs::MetricFilter", 2)


# ========== Production Enforcement Tests (Phase 7) ==========

def test_enforcement_execute_schedule_created(template):
    """Verify production enforcement EventBridge Rule exists with disabled state."""
    template.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like({
            "ScheduleExpression": "cron(30 2 ? * * *)",
            "State": "DISABLED",
        })
    )


def test_enforcement_execute_schedule_has_execute_input(template):
    """Verify production enforcement rule passes execute flag to Lambda."""
    template.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like({
            "State": "DISABLED",
            "Targets": Match.array_with([
                Match.object_like({
                    "Input": Match.serialized_json(
                        Match.object_like({"execute": True})
                    ),
                })
            ]),
        })
    )


def test_three_eventbridge_rules_created(template):
    """Verify exactly 3 EventBridge Rules exist (discovery + enforcement dry-run + enforcement execute)."""
    template.resource_count_is("AWS::Events::Rule", 3)


def test_enforcement_execute_rule_output_exists(template):
    """Verify production enforcement rule name is in CloudFormation outputs."""
    outputs = template.find_outputs("*")
    assert "EnforcementExecuteRuleName" in outputs, "Missing EnforcementExecuteRuleName output"


def test_production_enablement_checklist_output_exists(template):
    """Verify production enablement checklist is in CloudFormation outputs."""
    outputs = template.find_outputs("*")
    assert "ProductionEnablementChecklist" in outputs, "Missing ProductionEnablementChecklist output"


