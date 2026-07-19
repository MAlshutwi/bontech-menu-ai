"""
app/schemas.py - Pydantic v2 request/response models and validation.
"""
from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
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
    model_config = ConfigDict(extra="forbid")

    restaurant_id: StrictInt = Field(..., ge=1)
    customer_id: Optional[StrictInt] = Field(None, ge=1)
    cart_item_ids: List[StrictInt] = Field(default_factory=list)
    top_k: StrictInt = Field(5, ge=1, le=50)
    include_types: Optional[
        List[Literal["cross_sell", "similar_alternative", "popular", "time_based"]]
    ] = Field(None, min_length=1, max_length=4)
    context: Optional[RequestContext] = None

    @field_validator("cart_item_ids")
    @classmethod
    def recommendation_cart_ids_must_be_positive(cls, value):
        if any(item_id < 1 for item_id in value):
            raise ValueError("cart_item_ids must contain positive integers")
        return value

    @field_validator("include_types")
    @classmethod
    def include_types_must_be_unique(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("include_types must not contain duplicates")
        return value


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
    customer_id: Optional[StrictInt] = Field(None, ge=1)
    cart_item_ids: List[StrictInt]
    last_added_item_id: Optional[StrictInt] = Field(None, ge=1)
    previous_top_item_id: Optional[StrictInt] = Field(None, ge=1)
    limit: StrictInt = Field(5, ge=1, le=50)
    context: Optional[Dict[str, Any]] = None

    @field_validator("cart_item_ids")
    @classmethod
    def cart_item_ids_must_be_positive(cls, value):
        for item_id in value:
            if int(item_id) < 1:
                raise ValueError("cart_item_ids must contain positive integers")
        return value


class WidgetModelProvenance(BaseModel):
    model_key: str
    label_ar: str = ""
    context_key: Optional[str] = None
    role: Literal["selected", "supporting"] = "supporting"
    source: Optional[str] = None
    validated: bool = False
    validation_metric: Optional[str] = None
    validation_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    validation_trials: int = Field(0, ge=0)
    validation_scope: Optional[str] = None
    validation_source: Optional[str] = None
    evaluation_version: Optional[str] = None
    time_period_key: Optional[str] = None
    time_period_ar: Optional[str] = None
    unavailable_reason: Optional[str] = None


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
    category_id: Optional[int] = None
    recommendation_context: str = "popular"
    type_label_ar: str = "الأكثر طلبًا"
    model_key: str = "popularity"
    model_label_ar: str = "الأكثر طلبًا"
    score_label_ar: str = "قوة الطلب"
    rank: int = Field(1, ge=1)
    compatibility_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    probability_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    confidence_band_ar: str = "استكشافي"
    model_agreement_count: int = Field(1, ge=1)
    meets_threshold: bool = False
    is_available: bool = True
    availability_reason: Optional[str] = None
    selected_model: Optional[WidgetModelProvenance] = None
    supporting_models: List[WidgetModelProvenance] = Field(default_factory=list)
    contributing_models: List[WidgetModelProvenance] = Field(default_factory=list)
    source_labels_ar: List[str] = Field(default_factory=list)
    model_accuracy_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    accuracy_metric: Optional[str] = None
    model_accuracy_metric: Optional[str] = None
    accuracy_validated: bool = False
    time_period_key: Optional[str] = None
    time_period_ar: Optional[str] = None


class WidgetRecommendationSections(BaseModel):
    based_on_last_item: List[WidgetRecommendationItem] = Field(default_factory=list)
    based_on_cart: List[WidgetRecommendationItem] = Field(default_factory=list)
    similar_alternatives: List[WidgetRecommendationItem] = Field(default_factory=list)
    popular: List[WidgetRecommendationItem] = Field(default_factory=list)
    time_context: List[WidgetRecommendationItem] = Field(default_factory=list)
    current_trend: List[WidgetRecommendationItem] = Field(default_factory=list)


class WidgetRecommendationModelGroup(BaseModel):
    context_key: Literal["full_cart", "last_item", "popularity", "current_trend", "user"]
    model_key: str
    label_ar: str
    description_ar: str = ""
    available: bool = False
    threshold_fallback_used: bool = False
    suggestions: List[WidgetRecommendationItem] = Field(default_factory=list)
    selected: bool = False
    validated: bool = False
    validation_metric: Optional[str] = None
    validation_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    validation_trials: int = Field(0, ge=0)
    validation_scope: Optional[str] = None
    evaluation_version: Optional[str] = None
    status: Literal["available", "unavailable", "fallback"] = "unavailable"
    why_ar: str = ""
    unavailable_reason: Optional[str] = None
    selected_model: Optional[WidgetModelProvenance] = None
    future_ready: bool = False
    data_as_of: Optional[str] = None
    latest_order_at: Optional[str] = None
    freshness_status: Optional[Literal["fresh", "stale", "no_data"]] = None


class WidgetRecommendationResponse(BaseModel):
    request_id: str
    model_version: str
    restaurant_id: int
    customer_id: Optional[int] = None
    cart_item_ids: List[int]
    last_added_item_id: Optional[int] = None
    fallback_used: bool
    latency_ms: float
    sections: WidgetRecommendationSections
    top_recommendations: List[WidgetRecommendationItem] = Field(default_factory=list)
    models: List[WidgetRecommendationModelGroup] = Field(default_factory=list)
    default_model_key: str = "ensemble"
    default_context_key: Literal["full_cart", "last_item", "popularity", "current_trend", "user"] = "popularity"
    available_model_keys: List[str] = Field(default_factory=list)
    available_context_keys: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    disabled_reason: Optional[str] = None
    threshold_percent: float = 70.0
    threshold_fallback_used: bool = False
    selected_model: Optional[WidgetModelProvenance] = None
    supporting_models: List[WidgetModelProvenance] = Field(default_factory=list)
    selection_policy: Optional[str] = None
    time_period_key: Optional[str] = None
    time_period_ar: Optional[str] = None
    unavailable_models: List[WidgetModelProvenance] = Field(default_factory=list)


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
    database_ready: bool = True
    readiness_checked_at: Optional[str] = None


class EventType(str, Enum):
    shown = "shown"
    clicked = "clicked"
    added_to_cart = "added_to_cart"
    dismissed = "dismissed"
    purchased = "purchased"


class RecommendationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    restaurant_id: StrictInt = Field(..., ge=1)
    recommended_item_id: StrictInt = Field(..., ge=1)
    source: str = Field(..., min_length=1, max_length=64)
    # Request/session identity.
    request_id: Optional[str] = Field(None, max_length=64)
    session_id: Optional[str] = Field(None, max_length=64)
    customer_id: Optional[StrictInt] = Field(None, ge=1)
    order_id: Optional[StrictInt] = Field(None, ge=1)
    cart_item_ids: List[StrictInt] = Field(default_factory=list, max_length=50)
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

    @field_validator("cart_item_ids")
    @classmethod
    def event_cart_ids_must_be_positive_and_unique(cls, value):
        if any(item_id < 1 for item_id in value):
            raise ValueError("cart_item_ids must contain positive integers")
        if len(value) != len(set(value)):
            raise ValueError("cart_item_ids must not contain duplicates")
        return value


class EventAck(BaseModel):
    status: str = "ok"
    stored: int = 1
