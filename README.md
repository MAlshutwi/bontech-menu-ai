# BonTech Menu AI

FastAPI recommendation service with a live PostgreSQL restaurant menu and a Lovable-ready React frontend.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/MAlshutwi/bontech-menu-ai)

## Render

This repository includes `render.yaml` and `Dockerfile`.

Render prompts for these secrets during Blueprint creation:

- `DB_HOST`
- `DB_NAME`
- `DB_USER`
- `DB_PASS`
- `API_KEY` (optional for the current demo)

The model is packaged at `ToCoun/Final/bontech_recommendation_model_v1_1_0.joblib`.

Health endpoint: `/health`

Live restaurant menu: `/api/menu/restaurants`

Lovable frontend source: `ToCoun/LovableMenuAI`
