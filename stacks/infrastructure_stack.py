"""
Budget Balance Distribution - Infrastructure Stack

Creates the foundational infrastructure resources:
- DynamoDB tables (configuration and audit)
- IAM execution roles (discovery and enforcement)
"""
from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    Duration,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_logs as logs,
    aws_cloudwatch as cloudwatch,
    aws_sns as sns,
    aws_cloudwatch_actions as cw_actions,
    aws_s3 as s3,
)
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from constructs import Construct


class InfrastructureStack(Stack):
    """
    Stack containing DynamoDB tables and IAM roles for Budget Balance Distribution.

    Resources:
    - Configuration table: PK (partition key) + SK (sort key) for single-table design
      Uses entity-type prefix pattern (ACCOUNT#, GROUP#, THRESHOLD#)
    - Audit table: PK (partition key) + SK (sort key) for single-table design
      EntityTypeIndex GSI for compliance queries by entity_type
    - Discovery role: read-only access to config table + Cost Explorer
    - Enforcement role: write access to both tables + Cost Category write

    All stateful resources use RemovalPolicy.RETAIN and deletion_protection.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Configuration Table
        # Single-table design with generic PK/SK keys for all entity types
        # (accounts, groups, thresholds) using entity-type prefix pattern
        self.config_table = dynamodb.Table(
            self,
            "ConfigTable",
            partition_key=dynamodb.Attribute(
                name="PK",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            deletion_protection=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Audit Table
        # Audit table: PK (partition key) + SK (sort key) for single-table design
        self.audit_table = dynamodb.Table(
            self,
            "AuditTable",
            partition_key=dynamodb.Attribute(
                name="PK",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            deletion_protection=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Add GSI for querying by entity_type (compliance queries)
        self.audit_table.add_global_secondary_index(
            index_name="EntityTypeIndex",
            partition_key=dynamodb.Attribute(
                name="entity_type",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK",
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # Plan Artifact Bucket
        # Discovery Lambda writes proposed_changes.json here; Enforcement Lambda reads it
        # Human-reviewable gate between discovery and enforcement phases
        self.plan_artifact_bucket = s3.Bucket(
            self, "PlanArtifactBucket",
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireOldArtifacts",
                    expiration=Duration.days(90),
                    noncurrent_version_expiration=Duration.days(30),
                )
            ],
            removal_policy=RemovalPolicy.RETAIN,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
        )

        # Discovery Role
        # Read-only execution role for Discovery Lambda (Phase 2)
        self.discovery_role = iam.Role(
            self,
            "DiscoveryRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Read-only execution role for Discovery Lambda",
        )

        # Grant read access to config table (GetItem, Query)
        self.config_table.grant_read_data(self.discovery_role)

        # Add Cost Explorer read access (all APIs needed by zeus-discovery.py)
        self.discovery_role.add_to_policy(
            iam.PolicyStatement(
                sid="CostExplorerReadAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "ce:GetCostAndUsage",
                    "ce:GetReservationUtilization",
                    "ce:GetSavingsPlansUtilization",
                    "ce:GetSavingsPlansUtilizationDetails",
                    "ce:ListCostCategoryDefinitions",
                    "ce:DescribeCostCategoryDefinition",
                ],
                resources=["*"],  # Cost Explorer APIs don't support resource-level permissions
            )
        )

        # Add Organizations read access
        self.discovery_role.add_to_policy(
            iam.PolicyStatement(
                sid="OrganizationsReadAccess",
                effect=iam.Effect.ALLOW,
                actions=["organizations:ListAccounts"],
                resources=["*"],  # Organizations APIs don't support resource-level permissions
            )
        )

        # Grant write access to audit table for idempotency records
        self.audit_table.grant_write_data(self.discovery_role)

        # Grant Discovery Lambda write access to plan artifact bucket
        self.plan_artifact_bucket.grant_put(self.discovery_role)

        # Add Lambda basic execution policy for CloudWatch Logs
        self.discovery_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        # Discovery Lambda
        # PythonFunction with automatic dependency bundling
        self.discovery_lambda = PythonFunction(
            self, "DiscoveryLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            entry="lambda/discovery",
            index="index.py",
            handler="lambda_handler",
            timeout=Duration.minutes(10),
            memory_size=512,
            role=self.discovery_role,
            environment={
                "CONFIG_TABLE_NAME": self.config_table.table_name,
                "AUDIT_TABLE_NAME": self.audit_table.table_name,
                "POWERTOOLS_SERVICE_NAME": "discovery",
                "POWERTOOLS_LOG_LEVEL": "INFO",
                "LOOKBACK_DAYS": "30",
                "THRESHOLD_PCT": "120",
                "DRY_RUN": "true",
                "PLAN_ARTIFACT_BUCKET": self.plan_artifact_bucket.bucket_name,
            },
            log_retention=logs.RetentionDays.ONE_MONTH,
        )

        # EventBridge Schedule
        # Trigger Discovery Lambda daily at configurable time (default 2:00 AM UTC)
        # Override via CDK context: -c discovery_hour=3 -c discovery_minute=30
        discovery_hour = self.node.try_get_context("discovery_hour") or "2"
        discovery_minute = self.node.try_get_context("discovery_minute") or "0"
        self.discovery_schedule = events.Rule(
            self, "DiscoverySchedule",
            description="Trigger Discovery Lambda daily at 2:00 AM UTC",
            schedule=events.Schedule.cron(
                minute=discovery_minute,
                hour=discovery_hour,
                month="*",
                week_day="*",
                year="*",
            ),
        )
        self.discovery_schedule.add_target(
            targets.LambdaFunction(self.discovery_lambda)
        )

        # Enforcement Role
        # Write-enabled execution role for Enforcement Lambda (Phase 4)
        self.enforcement_role = iam.Role(
            self,
            "EnforcementRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Write-enabled execution role for Enforcement Lambda",
        )

        # Grant read access to config table (GetItem, Query)
        self.config_table.grant_read_data(self.enforcement_role)

        # Grant write access to config table (PutItem)
        self.config_table.grant_write_data(self.enforcement_role)

        # Grant read access to audit table (for idempotency checks)
        self.audit_table.grant_read_data(self.enforcement_role)

        # Grant write access to audit table (PutItem)
        self.audit_table.grant_write_data(self.enforcement_role)

        # Grant Enforcement Lambda read access to plan artifact bucket
        self.plan_artifact_bucket.grant_read(self.enforcement_role)

        # Add Cost Explorer READ access for enforcement
        self.enforcement_role.add_to_policy(
            iam.PolicyStatement(
                sid="CostExplorerReadAccessEnforcement",
                effect=iam.Effect.ALLOW,
                actions=[
                    "ce:GetCostAndUsage",
                    "ce:DescribeCostCategoryDefinition",
                ],
                resources=["*"],  # Cost Explorer APIs don't support resource-level permissions
            )
        )

        # Add Cost Category write access
        self.enforcement_role.add_to_policy(
            iam.PolicyStatement(
                sid="CostCategoryWriteAccess",
                effect=iam.Effect.ALLOW,
                actions=["ce:UpdateCostCategoryDefinition"],
                resources=["*"],  # Cost Explorer APIs don't support resource-level permissions
            )
        )

        # Add Lambda basic execution policy for CloudWatch Logs
        self.enforcement_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        # Enforcement Lambda
        # PythonFunction with automatic dependency bundling
        self.enforcement_lambda = PythonFunction(
            self, "EnforcementLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            entry="lambda/enforcement",
            index="index.py",
            handler="lambda_handler",
            timeout=Duration.minutes(10),
            memory_size=512,
            role=self.enforcement_role,
            environment={
                "CONFIG_TABLE_NAME": self.config_table.table_name,
                "AUDIT_TABLE_NAME": self.audit_table.table_name,
                "COST_CATEGORY_ARN": "",  # Set via CDK context or SSM at deploy time
                "POWERTOOLS_SERVICE_NAME": "enforcement",
                "POWERTOOLS_LOG_LEVEL": "INFO",
                "DRY_RUN": "true",
                "PLAN_ARTIFACT_BUCKET": self.plan_artifact_bucket.bucket_name,
            },
            log_retention=logs.RetentionDays.ONE_MONTH,
        )

        # EventBridge Schedule for Enforcement
        # Trigger Enforcement Lambda daily at 2:30 AM UTC (after Discovery at 2:00 AM)
        self.enforcement_schedule = events.Rule(
            self, "EnforcementSchedule",
            description="Trigger Enforcement Lambda daily at 2:30 AM UTC (after Discovery at 2:00 AM)",
            schedule=events.Schedule.cron(
                minute="30",
                hour="2",
                month="*",
                week_day="*",
                year="*",
            ),
        )
        self.enforcement_schedule.add_target(
            targets.LambdaFunction(self.enforcement_lambda)
        )

        # ========== Production Enforcement (Phase 7) ==========

        # Production Enforcement EventBridge Rule
        # Same cron schedule as dry-run rule (2:30 AM UTC), but disabled by default
        # Passes {"execute": true} to override DRY_RUN environment variable
        # Must be manually enabled via AWS Console after validation
        self.enforcement_execute_schedule = events.Rule(
            self, "EnforcementExecuteSchedule",
            description="Enforcement Lambda with actual Cost Category writes (DISABLED until validated)",
            enabled=False,  # CRITICAL: Must be manually enabled via AWS Console after validation
            schedule=events.Schedule.cron(
                minute="30",
                hour="2",
                month="*",
                week_day="*",
                year="*",
            ),
        )
        self.enforcement_execute_schedule.add_target(
            targets.LambdaFunction(
                self.enforcement_lambda,
                event=events.RuleTargetInput.from_object({
                    "execute": True  # Overrides DRY_RUN environment variable
                })
            )
        )

        # ========== Monitoring Resources (Phase 6) ==========

        # SNS Topic for alerts
        self.alert_topic = sns.Topic(
            self,
            "AlertTopic",
            display_name="Budget Balance Distribution Alerts",
            topic_name="BudgetBalanceDistribution-Alerts",
        )

        # CloudWatch Metric Filters
        # Extract flagged_count from discovery Lambda structured logs
        self.flagged_accounts_metric_filter = logs.MetricFilter(
            self,
            "FlaggedAccountsMetricFilter",
            log_group=self.discovery_lambda.log_group,
            metric_namespace="BudgetBalanceDistribution",
            metric_name="FlaggedAccountsCount",
            filter_pattern=logs.FilterPattern.literal('{ $.flagged_count = * }'),
            metric_value="$.flagged_count",
            default_value=0,
        )

        # Extract threshold violations from discovery Lambda logs
        self.threshold_violations_metric_filter = logs.MetricFilter(
            self,
            "ThresholdViolationsMetricFilter",
            log_group=self.discovery_lambda.log_group,
            metric_namespace="BudgetBalanceDistribution",
            metric_name="ThresholdViolationsDetected",
            filter_pattern=logs.FilterPattern.literal('{ $.level = "WARNING" && $.message = "*exceeds threshold*" }'),
            metric_value="1",
            default_value=0,
        )

        # CloudWatch Alarms
        # Lambda error alarms
        self.discovery_lambda_error_alarm = cloudwatch.Alarm(
            self,
            "DiscoveryLambdaErrorAlarm",
            alarm_name="BudgetBalanceDistribution-DiscoveryLambdaErrors",
            alarm_description="Discovery Lambda invocation errors exceed threshold",
            metric=self.discovery_lambda.metric_errors(
                statistic=cloudwatch.Stats.SUM,
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.discovery_lambda_error_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alert_topic)
        )

        self.enforcement_lambda_error_alarm = cloudwatch.Alarm(
            self,
            "EnforcementLambdaErrorAlarm",
            alarm_name="BudgetBalanceDistribution-EnforcementLambdaErrors",
            alarm_description="Enforcement Lambda invocation errors exceed threshold",
            metric=self.enforcement_lambda.metric_errors(
                statistic=cloudwatch.Stats.SUM,
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.enforcement_lambda_error_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alert_topic)
        )

        # Lambda duration alarms (approaching timeout)
        self.discovery_lambda_duration_alarm = cloudwatch.Alarm(
            self,
            "DiscoveryLambdaDurationAlarm",
            alarm_name="BudgetBalanceDistribution-DiscoveryLambdaDuration",
            alarm_description="Discovery Lambda duration approaching timeout (>510s of 600s)",
            metric=self.discovery_lambda.metric_duration(
                statistic=cloudwatch.Stats.MAXIMUM,
                period=Duration.minutes(5),
            ),
            threshold=510000,  # 510 seconds in milliseconds (85% of 10min)
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.discovery_lambda_duration_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alert_topic)
        )

        self.enforcement_lambda_duration_alarm = cloudwatch.Alarm(
            self,
            "EnforcementLambdaDurationAlarm",
            alarm_name="BudgetBalanceDistribution-EnforcementLambdaDuration",
            alarm_description="Enforcement Lambda duration approaching timeout (>510s of 600s)",
            metric=self.enforcement_lambda.metric_duration(
                statistic=cloudwatch.Stats.MAXIMUM,
                period=Duration.minutes(5),
            ),
            threshold=510000,  # 510 seconds in milliseconds (85% of 10min)
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.enforcement_lambda_duration_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alert_topic)
        )

        # DynamoDB throttling alarms
        self.config_table_throttle_alarm = cloudwatch.Alarm(
            self,
            "ConfigTableThrottleAlarm",
            alarm_name="BudgetBalanceDistribution-ConfigTableThrottles",
            alarm_description="Config table experiencing throttled requests",
            metric=cloudwatch.Metric(
                namespace="AWS/DynamoDB",
                metric_name="UserErrors",
                dimensions_map={"TableName": self.config_table.table_name},
                statistic=cloudwatch.Stats.SUM,
                period=Duration.minutes(5),
            ),
            threshold=5,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.config_table_throttle_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alert_topic)
        )

        self.audit_table_throttle_alarm = cloudwatch.Alarm(
            self,
            "AuditTableThrottleAlarm",
            alarm_name="BudgetBalanceDistribution-AuditTableThrottles",
            alarm_description="Audit table experiencing throttled requests",
            metric=cloudwatch.Metric(
                namespace="AWS/DynamoDB",
                metric_name="UserErrors",
                dimensions_map={"TableName": self.audit_table.table_name},
                statistic=cloudwatch.Stats.SUM,
                period=Duration.minutes(5),
            ),
            threshold=5,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.audit_table_throttle_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alert_topic)
        )

        # Business logic alarms
        self.threshold_violation_alarm = cloudwatch.Alarm(
            self,
            "ThresholdViolationAlarm",
            alarm_name="BudgetBalanceDistribution-ThresholdViolations",
            alarm_description="Accounts exceeding spending thresholds detected",
            metric=cloudwatch.Metric(
                namespace="BudgetBalanceDistribution",
                metric_name="ThresholdViolationsDetected",
                statistic=cloudwatch.Stats.SUM,
                period=Duration.hours(24),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.threshold_violation_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alert_topic)
        )

        self.anomalous_flagged_count_alarm = cloudwatch.Alarm(
            self,
            "AnomalousFlaggedCountAlarm",
            alarm_name="BudgetBalanceDistribution-AnomalousFlaggedCount",
            alarm_description="Anomalously high number of flagged accounts (potential misconfiguration)",
            metric=cloudwatch.Metric(
                namespace="BudgetBalanceDistribution",
                metric_name="FlaggedAccountsCount",
                statistic=cloudwatch.Stats.MAXIMUM,
                period=Duration.hours(24),
            ),
            threshold=5,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.anomalous_flagged_count_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alert_topic)
        )

        # Composite alarm aggregating all individual alarms
        self.composite_alarm = cloudwatch.CompositeAlarm(
            self,
            "SystemHealthCompositeAlarm",
            composite_alarm_name="BudgetBalanceDistribution-SystemHealth",
            alarm_description="Composite alarm for overall system health",
            alarm_rule=cloudwatch.AlarmRule.any_of(
                cloudwatch.AlarmRule.from_alarm(self.discovery_lambda_error_alarm, cloudwatch.AlarmState.ALARM),
                cloudwatch.AlarmRule.from_alarm(self.enforcement_lambda_error_alarm, cloudwatch.AlarmState.ALARM),
                cloudwatch.AlarmRule.from_alarm(self.discovery_lambda_duration_alarm, cloudwatch.AlarmState.ALARM),
                cloudwatch.AlarmRule.from_alarm(self.enforcement_lambda_duration_alarm, cloudwatch.AlarmState.ALARM),
                cloudwatch.AlarmRule.from_alarm(self.config_table_throttle_alarm, cloudwatch.AlarmState.ALARM),
                cloudwatch.AlarmRule.from_alarm(self.audit_table_throttle_alarm, cloudwatch.AlarmState.ALARM),
                cloudwatch.AlarmRule.from_alarm(self.threshold_violation_alarm, cloudwatch.AlarmState.ALARM),
                cloudwatch.AlarmRule.from_alarm(self.anomalous_flagged_count_alarm, cloudwatch.AlarmState.ALARM),
            ),
        )
        self.composite_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alert_topic)
        )

        # CloudWatch Dashboard
        self.dashboard = cloudwatch.Dashboard(
            self,
            "BudgetBalanceDistributionDashboard",
            dashboard_name="BudgetBalanceDistribution",
        )

        # Widget 1: Account Compliance Overview
        self.dashboard.add_widgets(
            cloudwatch.LogQueryWidget(
                title="Account Compliance Overview - Flagged Accounts",
                log_group_names=[self.discovery_lambda.log_group.log_group_name],
                query_lines=[
                    "fields @timestamp, account_id, current_spend, threshold, overage",
                    "| filter level = \"WARNING\" and message like /exceeds threshold/",
                    "| sort @timestamp desc",
                    "| limit 50",
                ],
                width=24,
            )
        )

        # Widget 2: Compliance Trend
        self.dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Flagged Accounts Trend",
                left=[
                    cloudwatch.Metric(
                        namespace="BudgetBalanceDistribution",
                        metric_name="FlaggedAccountsCount",
                        statistic=cloudwatch.Stats.MAXIMUM,
                        period=Duration.hours(1),
                    )
                ],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Threshold Violations (Daily)",
                left=[
                    cloudwatch.Metric(
                        namespace="BudgetBalanceDistribution",
                        metric_name="ThresholdViolationsDetected",
                        statistic=cloudwatch.Stats.SUM,
                        period=Duration.hours(24),
                    )
                ],
                width=12,
            ),
        )

        # Widget 3: Lambda Invocations
        self.dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Lambda Invocations",
                left=[
                    self.discovery_lambda.metric_invocations(
                        statistic=cloudwatch.Stats.SUM,
                        period=Duration.hours(1),
                        label="Discovery Invocations",
                    ),
                    self.enforcement_lambda.metric_invocations(
                        statistic=cloudwatch.Stats.SUM,
                        period=Duration.hours(1),
                        label="Enforcement Invocations",
                    ),
                ],
                right=[
                    self.discovery_lambda.metric_errors(
                        statistic=cloudwatch.Stats.SUM,
                        period=Duration.hours(1),
                        label="Discovery Errors",
                    ),
                    self.enforcement_lambda.metric_errors(
                        statistic=cloudwatch.Stats.SUM,
                        period=Duration.hours(1),
                        label="Enforcement Errors",
                    ),
                ],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Lambda Duration (ms)",
                left=[
                    self.discovery_lambda.metric_duration(
                        statistic=cloudwatch.Stats.AVERAGE,
                        period=Duration.hours(1),
                        label="Discovery Avg",
                    ),
                    self.discovery_lambda.metric_duration(
                        statistic=cloudwatch.Stats.MAXIMUM,
                        period=Duration.hours(1),
                        label="Discovery Max",
                    ),
                    self.enforcement_lambda.metric_duration(
                        statistic=cloudwatch.Stats.AVERAGE,
                        period=Duration.hours(1),
                        label="Enforcement Avg",
                    ),
                    self.enforcement_lambda.metric_duration(
                        statistic=cloudwatch.Stats.MAXIMUM,
                        period=Duration.hours(1),
                        label="Enforcement Max",
                    ),
                ],
                width=12,
            ),
        )

        # Widget 4: DynamoDB Metrics
        self.dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Config Table - Read/Write Capacity",
                left=[
                    cloudwatch.Metric(
                        namespace="AWS/DynamoDB",
                        metric_name="ConsumedReadCapacityUnits",
                        dimensions_map={"TableName": self.config_table.table_name},
                        statistic=cloudwatch.Stats.SUM,
                        period=Duration.minutes(5),
                        label="Read Capacity",
                    ),
                    cloudwatch.Metric(
                        namespace="AWS/DynamoDB",
                        metric_name="ConsumedWriteCapacityUnits",
                        dimensions_map={"TableName": self.config_table.table_name},
                        statistic=cloudwatch.Stats.SUM,
                        period=Duration.minutes(5),
                        label="Write Capacity",
                    ),
                ],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Audit Table - Read/Write Capacity",
                left=[
                    cloudwatch.Metric(
                        namespace="AWS/DynamoDB",
                        metric_name="ConsumedReadCapacityUnits",
                        dimensions_map={"TableName": self.audit_table.table_name},
                        statistic=cloudwatch.Stats.SUM,
                        period=Duration.minutes(5),
                        label="Read Capacity",
                    ),
                    cloudwatch.Metric(
                        namespace="AWS/DynamoDB",
                        metric_name="ConsumedWriteCapacityUnits",
                        dimensions_map={"TableName": self.audit_table.table_name},
                        statistic=cloudwatch.Stats.SUM,
                        period=Duration.minutes(5),
                        label="Write Capacity",
                    ),
                ],
                width=12,
            ),
        )

        # Widget 5: Enforcement Audit Trail
        self.dashboard.add_widgets(
            cloudwatch.LogQueryWidget(
                title="Enforcement Audit Trail - Recent Actions",
                log_group_names=[self.enforcement_lambda.log_group.log_group_name],
                query_lines=[
                    "fields @timestamp, execution_mode, action, account_id, message",
                    "| filter entity_type = \"enforcement\"",
                    "| sort @timestamp desc",
                    "| limit 50",
                ],
                width=24,
            )
        )

        # Widget 6: Enforcement History Summary
        self.dashboard.add_widgets(
            cloudwatch.LogQueryWidget(
                title="Enforcement History - Enable/Disable Counts",
                log_group_names=[self.enforcement_lambda.log_group.log_group_name],
                query_lines=[
                    "fields action",
                    "| filter entity_type = \"enforcement\"",
                    "| stats count() by action",
                ],
                width=12,
            ),
            cloudwatch.LogQueryWidget(
                title="Execution Mode Distribution",
                log_group_names=[self.enforcement_lambda.log_group.log_group_name],
                query_lines=[
                    "fields execution_mode",
                    "| filter entity_type = \"enforcement\"",
                    "| stats count() by execution_mode",
                ],
                width=12,
            ),
        )

        # Widget 7: Error Logs
        self.dashboard.add_widgets(
            cloudwatch.LogQueryWidget(
                title="Recent Errors (Discovery + Enforcement)",
                log_group_names=[
                    self.discovery_lambda.log_group.log_group_name,
                    self.enforcement_lambda.log_group.log_group_name,
                ],
                query_lines=[
                    "fields @timestamp, @log, level, message, exception",
                    "| filter level = \"ERROR\"",
                    "| sort @timestamp desc",
                    "| limit 50",
                ],
                width=24,
            )
        )

        # Outputs for cross-phase reference
        CfnOutput(
            self,
            "ConfigTableName",
            value=self.config_table.table_name,
            description="Configuration table name",
        )

        CfnOutput(
            self,
            "ConfigTableArn",
            value=self.config_table.table_arn,
            description="Configuration table ARN",
        )

        CfnOutput(
            self,
            "AuditTableName",
            value=self.audit_table.table_name,
            description="Audit table name",
        )

        CfnOutput(
            self,
            "AuditTableArn",
            value=self.audit_table.table_arn,
            description="Audit table ARN",
        )

        CfnOutput(
            self,
            "DiscoveryRoleArn",
            value=self.discovery_role.role_arn,
            description="Discovery Lambda execution role ARN",
        )

        CfnOutput(
            self,
            "EnforcementRoleArn",
            value=self.enforcement_role.role_arn,
            description="Enforcement Lambda execution role ARN",
        )

        CfnOutput(
            self,
            "DiscoveryLambdaArn",
            value=self.discovery_lambda.function_arn,
            description="Discovery Lambda function ARN",
        )

        CfnOutput(
            self,
            "DiscoveryScheduleArn",
            value=self.discovery_schedule.rule_arn,
            description="Discovery EventBridge schedule rule ARN",
        )

        CfnOutput(
            self,
            "EnforcementLambdaArn",
            value=self.enforcement_lambda.function_arn,
            description="Enforcement Lambda function ARN",
        )

        CfnOutput(
            self,
            "EnforcementScheduleArn",
            value=self.enforcement_schedule.rule_arn,
            description="Enforcement EventBridge schedule rule ARN",
        )

        # Monitoring outputs
        CfnOutput(
            self,
            "AlertTopicArn",
            value=self.alert_topic.topic_arn,
            description="SNS topic ARN for CloudWatch alerts",
        )

        CfnOutput(
            self,
            "DashboardName",
            value=self.dashboard.dashboard_name,
            description="CloudWatch dashboard name",
        )

        CfnOutput(
            self,
            "CompositeAlarmName",
            value=self.composite_alarm.alarm_name,
            description="Composite alarm name for overall system health",
        )

        # Production enforcement outputs
        CfnOutput(
            self,
            "EnforcementExecuteRuleName",
            value=self.enforcement_execute_schedule.rule_name,
            description="EventBridge Rule name for production enforcement (manually enable via Console after validation)",
        )

        CfnOutput(
            self,
            "ProductionEnablementChecklist",
            value=(
                "BEFORE enabling production enforcement: "
                "1. Verify dry-run logs show correct enable/disable decisions | "
                "2. Check CloudWatch dashboard shows metrics | "
                "3. Subscribe to SNS alerts and test delivery | "
                "4. Query audit table for recent dry-run records | "
                "5. Enable EventBridge Rule in AWS Console | "
                f"Rule: {self.enforcement_execute_schedule.rule_name}"
            ),
            description="Validation steps before enabling production enforcement",
        )

        CfnOutput(
            self,
            "PlanArtifactBucketName",
            value=self.plan_artifact_bucket.bucket_name,
            description="S3 bucket for plan artifacts (proposed_changes.json)",
        )
