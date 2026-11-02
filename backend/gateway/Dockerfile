FROM python:3.11-slim

WORKDIR /service

COPY backend/gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/gateway/app ./app
COPY backend/gateway/alembic ./alembic
COPY backend/gateway/alembic.ini ./alembic.ini
COPY backend/shared ./shared

ENV PYTHONPATH=/service

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]