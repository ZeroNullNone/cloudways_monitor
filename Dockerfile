FROM node:22-alpine AS frontend

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY cloudways_monitor ./cloudways_monitor
RUN pip install --no-cache-dir .

COPY --from=frontend /app/frontend/dist ./frontend/dist

EXPOSE 8000

CMD ["uvicorn", "cloudways_monitor.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
