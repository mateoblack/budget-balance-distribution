"""Pydantic V2 models for configuration entities."""
from typing import Optional, Literal
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import Self
import re


class AccountConfig(BaseModel):
    """Configuration for a single AWS account."""

    account_id: str = Field(..., description="AWS account ID (exactly 12 digits)")
    account_name: Optional[str] = Field(None, max_length=256, description="Human-readable account name")
    group_memberships: list[str] = Field(..., description="List of spending group IDs this account belongs to")
    active: bool = Field(default=True, description="Whether account participates in discount distribution")
    created_at: str = Field(..., description="ISO 8601 timestamp of creation")
    updated_at: str = Field(..., description="ISO 8601 timestamp of last update")

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, v: str) -> str:
        """Validate account_id is exactly 12 digits."""
        if not v:
            raise ValueError("account_id cannot be empty")
        if not v.isdigit():
            raise ValueError("account_id must contain only digits")
        if len(v) != 12:
            raise ValueError("account_id must be exactly 12 digits")
        return v

    @model_validator(mode="after")
    def validate_group_memberships(self) -> Self:
        """Validate account has at least one group membership."""
        if not self.group_memberships or len(self.group_memberships) == 0:
            raise ValueError("account must have at least one group membership")
        return self

    def to_dynamodb_item(self) -> dict:
        """Serialize to DynamoDB item format with PK/SK keys."""
        return {
            "PK": f"ACCOUNT#{self.account_id}",
            "SK": "METADATA",
            "account_id": self.account_id,
            "account_name": self.account_name,
            "group_memberships": self.group_memberships,
            "active": self.active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "entity_type": "ACCOUNT",
        }

    @classmethod
    def from_dynamodb_item(cls, item: dict) -> Self:
        """Deserialize from DynamoDB item format."""
        return cls(
            account_id=item["account_id"],
            account_name=item.get("account_name"),
            group_memberships=item["group_memberships"],
            active=item.get("active", True),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
        )


class SpendingGroup(BaseModel):
    """Configuration for a spending group."""

    group_id: str = Field(..., description="Group identifier (lowercase alphanumeric with hyphens)")
    name: str = Field(..., min_length=1, max_length=256, description="Human-readable group name")
    description: Optional[str] = Field(None, description="Optional group description")
    total_budget: Decimal = Field(..., description="Monthly budget in USD (must be positive)")
    active: bool = Field(default=True, description="Whether group is active")
    created_at: str = Field(..., description="ISO 8601 timestamp of creation")
    updated_at: str = Field(..., description="ISO 8601 timestamp of last update")

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, v: str) -> str:
        """Validate group_id pattern."""
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("group_id must contain only lowercase letters, numbers, and hyphens")
        if len(v) < 3 or len(v) > 64:
            raise ValueError("group_id must be between 3 and 64 characters")
        if v.startswith("-") or v.endswith("-"):
            raise ValueError("group_id cannot start or end with a hyphen")
        return v

    @field_validator("total_budget")
    @classmethod
    def validate_total_budget(cls, v: Decimal) -> Decimal:
        """Validate total_budget is positive."""
        if v <= 0:
            raise ValueError("total_budget must be greater than 0")
        return v

    def to_dynamodb_item(self) -> dict:
        """Serialize to DynamoDB item format with PK/SK keys."""
        return {
            "PK": f"GROUP#{self.group_id}",
            "SK": "METADATA",
            "group_id": self.group_id,
            "name": self.name,
            "description": self.description,
            "total_budget": str(self.total_budget),  # Convert Decimal to string for DynamoDB
            "active": self.active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "entity_type": "GROUP",
        }

    @classmethod
    def from_dynamodb_item(cls, item: dict) -> Self:
        """Deserialize from DynamoDB item format."""
        return cls(
            group_id=item["group_id"],
            name=item["name"],
            description=item.get("description"),
            total_budget=Decimal(item["total_budget"]),  # Convert string back to Decimal
            active=item.get("active", True),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
        )


class ThresholdConfig(BaseModel):
    """Configuration for a threshold applied to a spending group."""

    threshold_id: str = Field(..., description="Unique threshold identifier")
    group_id: str = Field(..., description="Spending group this threshold applies to")
    threshold_type: Literal["absolute", "percentage", "fair_share"] = Field(..., description="Type of threshold")
    absolute_amount: Optional[Decimal] = Field(None, description="Fixed dollar amount (for absolute type)")
    percentage_value: Optional[Decimal] = Field(None, description="Percentage of group budget (for percentage type)")
    re_enable_threshold_pct: Optional[Decimal] = Field(
        None,
        description=(
            "Re-enable threshold as % of fair share (default: same as threshold). "
            "If set lower than threshold, creates a hysteresis band to prevent oscillation. "
            "Example: threshold=120%, re_enable=80% means disable at 120% and only re-enable at 80%."
        )
    )
    fairness_metric: Literal["combined", "ri_only", "sp_only"] = Field(
        default="combined",
        description=(
            "Which discount metric to use for fairness comparison. "
            "'combined' = total RI+SP benefit (default, current behavior). "
            "'sp_only' = Savings Plans benefit only (per-account attribution available). "
            "'ri_only' = Reserved Instance benefit only (per-account attribution approximate)."
        )
    )
    reenablement_strategy: Literal["calendar", "consumption"] = Field(
        default="calendar",
        description=(
            "Re-enablement strategy for accounts that exceed their threshold. "
            "'calendar' (default) keeps accounts disabled for the remainder of the billing "
            "month they were disabled in, matching the monthly fair-share allocation model. "
            "'consumption' re-enables based solely on the re_enable_threshold comparison "
            "(original behavior — accounts can re-enter the pool mid-month if daily spend dips)."
        )
    )
    created_at: str = Field(..., description="ISO 8601 timestamp of creation")
    updated_at: str = Field(..., description="ISO 8601 timestamp of last update")

    @field_validator("absolute_amount")
    @classmethod
    def validate_absolute_amount(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        """Validate absolute_amount is positive when provided."""
        if v is not None and v <= 0:
            raise ValueError("absolute_amount must be greater than 0")
        return v

    @field_validator("percentage_value")
    @classmethod
    def validate_percentage_value(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        """Validate percentage_value is between 0 and 100 when provided."""
        if v is not None:
            if v < 0 or v > 100:
                raise ValueError("percentage_value must be between 0 and 100")
        return v

    @field_validator("re_enable_threshold_pct")
    @classmethod
    def validate_re_enable_threshold_pct(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        """Validate re_enable_threshold_pct is positive when provided."""
        if v is not None and v <= 0:
            raise ValueError("re_enable_threshold_pct must be greater than 0")
        return v

    @model_validator(mode="after")
    def validate_threshold_type_fields(self) -> Self:
        """Validate threshold_type matches populated fields."""
        if self.threshold_type == "absolute":
            if self.absolute_amount is None:
                raise ValueError("absolute threshold requires absolute_amount")
        elif self.threshold_type == "percentage":
            if self.percentage_value is None:
                raise ValueError("percentage threshold requires percentage_value")
        elif self.threshold_type == "fair_share":
            # Fair share should not have extra fields, but we allow them to be None
            pass
        return self

    def to_dynamodb_item(self) -> dict:
        """Serialize to DynamoDB item format with PK/SK keys."""
        item = {
            "PK": f"THRESHOLD#{self.threshold_id}",
            "SK": f"GROUP#{self.group_id}",
            "threshold_id": self.threshold_id,
            "group_id": self.group_id,
            "threshold_type": self.threshold_type,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "entity_type": "THRESHOLD",
        }

        # Only include optional fields if they are set
        if self.absolute_amount is not None:
            item["absolute_amount"] = str(self.absolute_amount)
        if self.percentage_value is not None:
            item["percentage_value"] = str(self.percentage_value)
        if self.re_enable_threshold_pct is not None:
            item["re_enable_threshold_pct"] = str(self.re_enable_threshold_pct)
        if self.fairness_metric != "combined":
            item["fairness_metric"] = self.fairness_metric
        if self.reenablement_strategy != "calendar":
            item["reenablement_strategy"] = self.reenablement_strategy

        return item

    @classmethod
    def from_dynamodb_item(cls, item: dict) -> Self:
        """Deserialize from DynamoDB item format."""
        return cls(
            threshold_id=item["threshold_id"],
            group_id=item["group_id"],
            threshold_type=item["threshold_type"],
            absolute_amount=Decimal(item["absolute_amount"]) if "absolute_amount" in item else None,
            percentage_value=Decimal(item["percentage_value"]) if "percentage_value" in item else None,
            re_enable_threshold_pct=Decimal(item["re_enable_threshold_pct"]) if "re_enable_threshold_pct" in item else None,
            fairness_metric=item.get("fairness_metric", "combined"),
            reenablement_strategy=item.get("reenablement_strategy", "calendar"),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
        )
