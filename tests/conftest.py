"""
Pytest configuration for CDK infrastructure tests.

Provides a session-scoped autouse fixture that patches PythonFunction
with a mock that uses inline code, bypassing Docker bundling. This allows
CDK assertions tests to run without Docker available.
"""
import pytest
from unittest.mock import patch
import aws_cdk as cdk
from aws_cdk import aws_lambda as lambda_


class MockPythonFunction(lambda_.Function):
    """
    Drop-in replacement for PythonFunction that uses inline code to avoid
    Docker-based bundling. Preserves all Lambda properties (runtime, timeout,
    memory, environment, role) so CDK assertion tests can validate them.
    """

    def __init__(self, scope, id, *, entry, index="index.py", handler="handler",
                 runtime, bundling=None, **kwargs):
        super().__init__(
            scope,
            id,
            runtime=runtime,
            handler=f"{index.replace('.py', '')}.{handler}",
            code=lambda_.Code.from_inline("def handler(event, context): pass"),
            **{k: v for k, v in kwargs.items() if k != "bundling"},
        )


@pytest.fixture(autouse=True, scope="session")
def patch_python_function():
    """
    Session-scoped autouse fixture that replaces PythonFunction with
    MockPythonFunction for all infrastructure stack tests.

    This fixture is necessary when Docker is not available (CI environments,
    local development without Docker running). The mock preserves all Lambda
    configuration properties so CDK assertions remain valid.
    """
    with patch("stacks.infrastructure_stack.PythonFunction", MockPythonFunction):
        yield
