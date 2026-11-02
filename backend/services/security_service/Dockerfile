FROM golang:1.23-alpine AS gitleaks-build

RUN go install github.com/zricethezav/gitleaks/v8@v8.21.2

FROM golang:1.23-bookworm AS go-security-tools

RUN GOBIN=/out go install golang.org/x/vuln/cmd/govulncheck@v1.1.4

FROM python:3.12-slim

WORKDIR /service

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm clang \
    && rm -rf /var/lib/apt/lists/*

COPY backend/services/security_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=gitleaks-build /go/bin/gitleaks /usr/local/bin/gitleaks
COPY --from=go-security-tools /out/govulncheck /usr/local/bin/govulncheck
COPY backend/services/security_service/app ./app
COPY backend/shared ./shared

ENV PYTHONPATH=/service

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]