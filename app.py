#!/usr/bin/env python3
"""
Budget Balance Distribution - CDK Application Entry Point

This application creates the infrastructure stack for Budget Balance Distribution,
which enforces fair discount allocation across AWS Organizations.
"""
import aws_cdk as cdk
from stacks.infrastructure_stack import InfrastructureStack


app = cdk.App()

# Read configuration from cdk.json context
account_id = app.node.try_get_context("account_id")
region = app.node.try_get_context("region")

# Validate required context values
if not account_id or account_id == "REPLACE_WITH_ACCOUNT_ID":
    raise ValueError(
        "account_id must be set in cdk.json context. "
        "Replace 'REPLACE_WITH_ACCOUNT_ID' with your AWS account ID."
    )

if not region:
    raise ValueError("region must be set in cdk.json context")

# Create the infrastructure stack
InfrastructureStack(
    app,
    "BudgetInfrastructureStack",
    description="Budget Balance Distribution infrastructure (DynamoDB + IAM)",
    env=cdk.Environment(account=account_id, region=region),
)

app.synth()
