# BonTech Menu AI

FastAPI recommendation service with a live PostgreSQL restaurant menu and a Lovable-ready React frontend.

## Render

This repository includes `render.yaml` and `Dockerfile`.

Create a Render Blueprint from this repository and set these secrets:

- `DB_HOST`
- `DB_NAME`
- `DB_USER`
- `DB_PASS`
- `API_KEY` (optional for the current demo)

The model is packaged at `ToCoun/Final/bontech_recommendation_model_v1_1_0.joblib`.

Health endpoint: `/health`
Live restaurant menu: `/api/menu/restaurants`
