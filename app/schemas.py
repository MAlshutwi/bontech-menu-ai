"""
app/schemas.py - Pydantic v2 request/response models and validation.
"""
from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class OrderType(str, Enum):
    dine_in = "dine_in"
    takeaway = "takeaway"
    delivery = "delivery"


class RequestContext(BaseModel):
    pos_id: Optional[str] = Field(None, max_length=64)
    branch_id: Optional[int] = Field(None, ge=1)
    order_type: Optional[OrderType] = None
    timestamp: Optional[str] = Field(None, max_length=64)


class RecommendationRequest(BaseModel):
    restaurant_id: int = Field(..., ge=1)
    customer_id: Optional[int] = Field(None, ge=1)
    cart_item_ids: List[int] = Field(default_factory=list)
    top_k: int = Field(5, ge=1, le=50)
    include_types: Optional[List[str]] = None   # Phase 10 rails to return.
    context: Optional[RequestContext] = None


class RecommendationItem(BaseModel):
    item_id: int
    title_ar: str = ""
    title_en: str = ""
    score: float
    reason: str = ""
    source: str
    evidence: Dict[str, Any] = Field(default_factory=dict)


class RecommendationGroup(BaseModel):
    type: str
    title_ar: str = ""
    items: List[RecommendationItem] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    restaurant_id: int
    customer_id: Optional[int] = None
    recommendations: List[RecommendationItem]                       # backward-compat (cross_sell)
    recommendation_groups: List[RecommendationGroup] = Field(default_factory=list)  # Phase 10
    fallback_used: bool
    model_version: str
    experiment_id: Optional[str] = None
    request_id: Optional[str] = None   # End-to-end trace id, also returned in X-Request-Id.


class WidgetRecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restaurant_id: StrictInt = Field(..., ge=1)
    cart_item_ids: List[StrictInt]
    last_added_item_id: Optional[StrictInt] = Field(None, ge=1)
    limit: StrictInt = Field(5, ge=1, le=50)
    context: Optional[Dict[str, Any]] = None

    @field_validator("cart_item_ids")
    @classmethod
    def cart_item_ids_must_be_positive(cls, value):
        for item_id in value:
            if int(item_id) < 1:
                raise ValueError("cart_item_ids must contain positive integers")
        return value


class WidgetRecommendationItem(BaseModel):
    item_id: int
    title_ar: str = ""
    title_en: str = ""
    score: float
    source: str
    recommendation_type: str
    reason: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    addable: bool = True
    disabled_reason: Optional[str] = None


class WidgetRecommendationSections(BaseModel):
    based_on_last_item: List[WidgetRecommendationItem] = Field(default_factory=list)
    based_on_cart: List[WidgetRecommendationItem] = Field(default_factory=list)
    similar_alternatives: List[WidgetRecommendationItem] = Field(default_factory=list)
    popular: List[WidgetRecommendationItem] = Field(default_factory=list)


class WidgetRecommendationResponse(BaseModel):
    request_id: str
    model_version: str
    restaurant_id: int
    cart_item_ids: List[int]
    last_added_item_id: Optional[int] = None
    fallback_used: bool
    latency_ms: float
    sections: WidgetRecommendationSections
    top_recommendations: List[WidgetRecommendationItem] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    disabled_reason: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    model_version: str
    last_trained_at: Optional[str] = None
    model_accuracy_metric: Optional[str] = None
    model_accuracy_percent: Optional[float] = None
    restaurants_with_fbt: int = 0
    restaurants_with_popularity: int = 0
    kill_switch_active: bool = False
    kill_switch_reason: Optional[str] = None
    api_key_required: bool = False


class EventType(str, Enum):
    shown = "shown"
    clicked = "clicked"
    added_to_cart = "added_to_cart"
    dismissed = "dismissed"
    purchased = "purchased"


class RecommendationEvent(BaseModel):
    event_type: EventType
    restaurant_id: int = Field(..., ge=1)
    recommended_item_id: int = Field(..., ge=1)
    source: str = Field(..., max_length=64)
    # Request/session identity.
    request_id: Optional[str] = Field(None, max_length=64)
    session_id: Optional[str] = Field(None, max_length=64)
    customer_id: Optional[int] = Field(None, ge=1)
    order_id: Optional[int] = Field(None, ge=1)
    cart_item_ids: List[int] = Field(default_factory=list, max_length=200)
    recommendation_type: Optional[str] = Field(None, max_length=32)
    experiment_id: Optional[str] = Field(None, max_length=64)
    surface: Optional[str] = Field(None, max_length=32)   # cart / checkout / item_page ...
    rank: Optional[int] = Field(None, ge=0, le=1000)
    score: Optional[float] = None
    reason_code: Optional[str] = Field(None, max_length=64)
    model_version: Optional[str] = Field(None, max_length=32)
    pos_id: Optional[str] = Field(None, max_length=64)
    variant: Optional[str] = Field(None, max_length=64)
    timestamp: Optional[str] = Field(None, max_length=64)


class EventAck(BaseModel):
    status: str = "ok"
    stored: int = 1
