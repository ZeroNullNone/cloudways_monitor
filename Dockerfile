FROM node:22-alpine AS frontend

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --loglevel=info
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Finish the frontend before pip runs to limit concurrent work on small hosts.
COPY --from=frontend /app/frontend/dist ./frontend/dist

COPY pyproject.toml ./
COPY cloudways_monitor ./cloudways_monitor
RUN pip install --no-cache-dir --verbose .

EXPOSE 8083

CMD ["uvicorn", "cloudways_monitor.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8083"]
