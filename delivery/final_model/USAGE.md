# BonTech Recommendation Model v1.1.0 Usage

## Python

```python
from app.model_loader import load_model

model = load_model("delivery/final_model/bontech_recommendation_model_v1_1_0.joblib")

result = model.recommend(
    restaurant_id=277,
    cart_item_ids=[4648],
    last_added_item_id=4648,
    limit=5,
)
```

## API

`POST /api/v1/recommendations`

```json
{
  "restaurant_id": 277,
  "cart_item_ids": [4648],
  "last_added_item_id": 4648,
  "limit": 5
}
```

## Widget

The widget sends `restaurant_id` and `cart_item_ids` to the API. It does not load
or read the model file directly.

Widget files are in `delivery/final_model/widget/`.

## Status

DB blocker remains OPEN. Production Not Ready.
