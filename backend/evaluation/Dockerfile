FROM python:3.12-slim

WORKDIR /workspace

COPY backend/__init__.py ./backend/__init__.py
COPY backend/shared ./backend/shared
COPY backend/evaluation ./backend/evaluation

CMD ["python", "-m", "backend.evaluation.runner"]
