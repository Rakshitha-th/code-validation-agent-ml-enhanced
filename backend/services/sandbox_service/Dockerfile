FROM mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim AS dotnet-sdk

FROM golang:1.23-bookworm AS go-tools

RUN GOBIN=/out go install github.com/golangci/golangci-lint/cmd/golangci-lint@v1.62.2 \
    && GOBIN=/out go install golang.org/x/vuln/cmd/govulncheck@v1.1.4

FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    clang \
    clang-tidy \
    clang-tools \
    default-jdk-headless \
    gcc \
    g++ \
    golang-go \
    mono-mcs \
    mono-runtime \
    libmono-system-data4.0-cil \
    chromium \
    nodejs \
    node-typescript \
    npm \
    curl \
    unzip \
    cppcheck \
    checkstyle \
    libcommons-cli-java \
    && rm -rf /var/lib/apt/lists/*

RUN npm install --global eslint@9.17.0 @typescript-eslint/parser@8.18.0 @typescript-eslint/eslint-plugin@8.18.0 playwright-core@1.56.1

RUN curl --retry 5 --retry-all-errors --retry-delay 2 -fsSL https://github.com/pmd/pmd/releases/download/pmd_releases/7.8.0/pmd-dist-7.8.0-bin.zip -o /tmp/pmd.zip \
    && unzip -q /tmp/pmd.zip -d /opt \
    && ln -s /opt/pmd-bin-7.8.0/bin/pmd /usr/local/bin/pmd \
    && curl --retry 5 --retry-all-errors --retry-delay 2 -fsSL https://github.com/spotbugs/spotbugs/releases/download/4.9.3/spotbugs-4.9.3.zip -o /tmp/spotbugs.zip \
    && unzip -q /tmp/spotbugs.zip -d /opt \
    && ln -s /opt/spotbugs-4.9.3/bin/spotbugs /usr/local/bin/spotbugs \
    && rm -f /tmp/pmd.zip /tmp/spotbugs.zip

COPY --from=go-tools /out/golangci-lint /usr/local/bin/golangci-lint
COPY --from=go-tools /out/govulncheck /usr/local/bin/govulncheck

COPY --from=dotnet-sdk /usr/share/dotnet /usr/share/dotnet
RUN ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet

ENV DOTNET_ROOT=/usr/share/dotnet
ENV NODE_PATH=/usr/local/lib/node_modules

COPY backend/services/sandbox_service/checkstyle.xml /opt/coding-validation-checkstyle.xml

COPY backend/services/sandbox_service/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY backend/services/sandbox_service/app ./app
COPY backend/services/sandbox_service/app/browser_runner.cjs ./browser_runner.cjs
COPY backend/shared ./shared

ENV PYTHONPATH=/app
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium

EXPOSE 8004

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]