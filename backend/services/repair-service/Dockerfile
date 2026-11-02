FROM python:3.12-slim

WORKDIR /service

RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jdk-headless \
    gcc \
    g++ \
    golang-go \
    mono-mcs \
    nodejs \
    node-typescript \
    && rm -rf /var/lib/apt/lists/*

COPY backend/services/repair-service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/services/repair-service/app ./app
COPY backend/shared ./shared

RUN useradd --create-home --shell /usr/sbin/nologin appuser && chown -R appuser:appuser /service
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]