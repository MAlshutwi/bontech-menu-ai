FROM node:22-alpine AS menu_frontend

WORKDIR /frontend
COPY ToCoun/LovableMenuAI/package.json ToCoun/LovableMenuAI/package-lock.json ./
RUN npm ci
COPY ToCoun/LovableMenuAI/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=menu_frontend /frontend/dist /app/ToCoun/LovableMenuAI/dist

EXPOSE 7860

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
