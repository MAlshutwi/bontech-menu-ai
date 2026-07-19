FROM node:22.22-alpine3.23@sha256:968df39aedcea65eeb078fb336ed7191baf48f972b4479711397108be0966920 AS menu_frontend

WORKDIR /frontend
COPY ToCoun/LovableMenuAI/package.json ToCoun/LovableMenuAI/package-lock.json ./
RUN npm ci
COPY ToCoun/LovableMenuAI/ ./
RUN npm run build

FROM python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --system --gid 10001 bontech \
    && useradd --system --uid 10001 --gid bontech --home-dir /app --shell /usr/sbin/nologin bontech

COPY --chown=bontech:bontech . .
COPY --from=menu_frontend --chown=bontech:bontech /frontend/dist /app/ToCoun/LovableMenuAI/dist

USER bontech

EXPOSE 7860

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
