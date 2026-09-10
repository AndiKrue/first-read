FROM node:20-slim AS web-build
WORKDIR /app/web
COPY web/package.json ./
RUN npm install
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    FIRST_READ_OUTPUT_DIR=/tmp/first-read
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY samples/ ./samples/
RUN pip install --no-cache-dir -e .
COPY --from=web-build /app/web/dist ./web/dist
CMD ["sh", "-c", "exec uvicorn first_read.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
