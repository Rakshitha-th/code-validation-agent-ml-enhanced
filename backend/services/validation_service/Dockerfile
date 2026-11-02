FROM python:3.12-slim

WORKDIR /service

COPY backend/services/validation_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/services/validation_service/app ./app
COPY backend/shared ./shared

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]