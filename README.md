# Coding validation agent — Sreya's portion

This checkout contains the Orchestrator, Code Quality, Frontend, and the
available teammate agent implementations integrated through HTTP adapters:

- **Sreya** — Orchestrator + Code Quality + Frontend *(this folder)*
- **Rakshitha** — Fix & Regeneration + Security + Database & Reports
- **Shifa** — Validation + Bug & Resource + Docker Sandbox

## What's here

```text
coding-validation-agent/
├── backend/
│   ├── app/                      Legacy repair prototype (not used by Compose)
│   ├── evaluation/               JSONL datasets and shared evaluation CLI
│   ├── gateway/                  API entry point (FastAPI)
│   ├── services/
│   │   ├── orchestrator-service/ LangGraph workflow (FastAPI)
│   │   ├── code-quality-service/ Python linters + generic source checks (FastAPI)
│   │   ├── security_service/     Bandit, Semgrep, and Gitleaks (FastAPI)
│   │   ├── validation_service/   Python static validation checks (FastAPI)
│   │   ├── report-recommendation-service/ Ollama or Gemini report and recommendations
│   │   ├── repair-service/       Fix & Regeneration microservice (FastAPI)
│   │   └── sandbox_service/      Isolated execution API
│   └── shared/schemas/           Common backend contracts
├── frontend/                     React + TypeScript + Vite UI
└── docker-compose.yml             Runs frontend, Gateway, PostgreSQL,
                                    Orchestrator, Code Quality, Security,
                                    Validation, and Repair
```

The backend application code is grouped under `backend/`; the frontend and
Compose entry point remain at the repository root. The Orchestrator is the only
component that selects and invokes agents in the
workflow. Its `repair` Compose dependency runs the Fix & Regeneration
microservice from `backend/services/repair-service`; `backend/app/` is not
used by Compose. The Orchestrator adapts Security, Validation, Repair, Sandbox, and
Report/Recommendation APIs to its workflow contracts. It owns workflow order,
opt-in checks, progress, and result aggregation; report generation runs in its
own internal service. The optional Ollama stage is explicitly selected per
analysis; selected source files are sent to the configured Ollama model, and
generated code suggestions are review-only.
Bug & Resource analysis remains unavailable unless its service URL is
configured. The sandbox is enabled in Compose and requires access to the Docker
socket, so deploy it only with an appropriately isolated Docker host.

## Running it locally

### Option A — Docker Compose (recommended)

```bash
# Create a local .env with your deployment settings and secrets.
docker compose up --build -d
```

- Set `POSTGRES_PASSWORD` to the password for your PostgreSQL deployment.
- Configure `SMTP_HOST`, `SMTP_FROM_EMAIL`, and any required SMTP credentials in
  `.env` to enable password reset email delivery. Keep `.env` private and do
  not commit it.
- Frontend: http://localhost:5174
- Gateway: http://localhost:8000 (docs at /docs)
- Orchestrator: http://localhost:8001 (internal gateway target)
- Code quality service: http://localhost:8002 (docs at /docs)
- Security, Validation, Repair, and Report/Recommendation: internal Compose
   services; not published to host ports
- PostgreSQL: internal Compose service; data persists in `postgres-data`

The Repair and Report/Recommendation agents can call Ollama on the Docker host at port `11434` by default.
Set `OLLAMA_HOST_PORT` in `.env` if your host Ollama uses another port, and
`OLLAMA_MODEL` to select an installed model. The default `qwen2.5-coder:3b`
may produce weaker repairs than a larger code-capable model. To avoid host-port
collisions when another stack is running, override `GATEWAY_PORT`,
`ORCHESTRATOR_PORT`, `QUALITY_PORT`, or `FRONTEND_PORT` in `.env`.

By default, the Repair and Report/Recommendation services use Ollama. Make sure
Ollama is running on the Docker host with the configured `OLLAMA_MODEL`
installed. The report/recommendation service can optionally use Gemini by setting
`REPORT_RECOMMENDATION_PROVIDER=gemini`, `GEMINI_API_KEY`, and `GEMINI_MODEL`;
Ollama remains the default provider.
`OLLAMA_HOST_PORT` defaults to `11434`; `OLLAMA_REQUEST_TIMEOUT_SECONDS`
defaults to 600 seconds to allow local models time to generate code and reports.
Repair requests use schema-constrained JSON and language-specific instructions.
Model-reported resolved findings are advisory only: analyzers, validation, and
the workflow's post-repair re-analysis determine the repair outcome.
The report option is unchecked by default; project source is sent to the
configured AI provider only when recommendations are enabled for an analysis.
The existing HTML report can be printed or saved as PDF from the results page.

The workflow accepts relative source files in Python, JavaScript, TypeScript,
Java, C, C++, Go, and C#. The upload screen detects language by extension and
allows an explicit language selection; mismatches are rejected. A .NET SDK
project can be selected when its C# sources and `.csproj` file are included.
Bug/resource analysis includes Python AST checks, Tree-sitter checks for empty
exception handlers and unconditional loops in supported non-Python languages,
whole-file read checks for Java, JavaScript/TypeScript, C#, and Go, and basic
matching-allocation checks for C/C++. These are targeted heuristics, not
exhaustive language-specific bug or resource analyzers; language support remains
partial until broader analyzer coverage and end-to-end validation are
established.
One analysis may contain one language; JavaScript and TypeScript may be
combined, as may C and C++ headers/sources.

Python quality analysis includes Ruff, Pylint, Radon, and MyPy. Other languages
use language-specific analyzers in the sandbox: ESLint for JavaScript,
typescript-eslint plus the TypeScript compiler, Checkstyle/PMD/SpotBugs for
Java, Cppcheck/Clang-Tidy/Clang Static Analyzer for C and C++, golangci-lint
plus `go vet`, and .NET SDK Roslyn analyzers for C#. Security checks use
Bandit, Semgrep, and Gitleaks, with Clang Static Analyzer for C/C++,
`npm audit` for projects containing a package lockfile, and govulncheck for
Go modules. The dependency vulnerability scans require access to their
configured public vulnerability registries; uploaded source is not sent to
those registries, but dependency manifests are used for their queries.
Non-Python bug/resource checks remain heuristic.

The additional analyzers improve language coverage but do not by themselves
make every project type fully supported. External build dependencies are not
restored in the network-disabled execution sandbox, framework-specific checks
are limited, and uploaded project test suites are not automatically discovered
or run. The language registry therefore continues to mark the non-Python
adapters as partial until project-level build/test support and end-to-end
validation are available.

The Validation agent applies Python AST/Flake8 checks to Python sources and
dispatches JavaScript, TypeScript, Java, C, C++, Go, and C# projects to the
isolated sandbox's language-specific compile-only validation plans. It accepts
project file maps, returns compiler diagnostics as findings, and reports
unavailable validators and timeouts as failures rather than passing them.
Compose configures this connection automatically; when running services
manually, set `VALIDATION_SANDBOX_SERVICE_URL` to the sandbox API URL.
These non-Python checks establish compile/syntax validity, not complete
project correctness or test-suite coverage. Project test suites are not
automatically discovered or run.

Repair candidates are checked before being returned: Python uses AST parsing
and Ruff checks; JavaScript, TypeScript, Java, C, C++, Go, and C# use their
corresponding compiler or syntax checker. These checks do not prove that a
quality or security finding was fixed. Model-reported resolved IDs are advisory;
the orchestrator's post-repair analyzer results determine the final finding
counts and repair status. A candidate with incomplete analyzer results is
reported as `needs_review`, not as a successful repair. The final report keeps
repair status separate from sandbox/validation status. Repair statuses are
`success`, `partial`, `failed`, `needs_review`, `not_run`, and `running`.
The finding comparison uses rule ID, file/location, and normalized description;
remaining or newly introduced findings prevent a complete-success status.
Repeated changed candidates are not retried, while unchanged candidates may
receive a bounded retry with explicit feedback to try a different strategy.
Candidates that introduce critical/high security findings are rejected.

The sandbox image installs the compilers and analyzers. Submitted source is
checked in isolated containers with network disabled, resource limits, a
read-only project mount, and restricted privileges. A .NET project that needs
uncached NuGet packages will report a restore/build failure. In the current
hardened non-root container, the .NET SDK may also be unable to initialize
NuGet's first-run mutex; this is reported as unavailable rather than passed.
Standalone C# validation uses Mono; C# projects use the .NET SDK analyzers.
Non-Python final validation uses language-specific compiler/checker results in
compile-only mode; runtime execution is separate from validation.

The Gateway applies the versioned Alembic migrations at startup. Compose supplies
the PostgreSQL host, port, database, user, and password separately using
`POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`; SQLAlchemy builds the
connection URL so passwords containing URL-reserved characters work correctly.
Do not commit `.env`. Back up the PostgreSQL `postgres-data` volume before
upgrades or maintenance; do not use `docker compose down -v` when retaining data.

Gateway authentication uses Argon2 password hashes and opaque, HttpOnly
database-backed session cookies. Sign-up, login, logout, authenticated password
changes, and password-reset links are supported. Reset links are random,
single-use, stored only as token hashes, and expire after
`AUTH_RESET_TOKEN_MINUTES` (30 by default). Password reset email requires
`SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM_EMAIL`, and optionally `SMTP_USERNAME`,
`SMTP_PASSWORD`, `SMTP_USE_TLS`, or `SMTP_USE_SSL`; set `FRONTEND_PUBLIC_URL`
to the public frontend origin. Without SMTP configuration, reset requests
return an explicit service-unavailable error rather than pretending an email
was sent. Login, signup, reset-request, and reset-completion endpoints are
throttled in each Gateway process; multi-replica deployments should also apply
shared edge-level rate limiting.

The code-quality service accepts `POST /analyze` with a `project_id`, relative
supported source files, and an optional subset of `ruff`, `pylint`, and `radon`
for Python. It returns the shared findings contract used by the orchestrator.
Inputs are bounded by `QUALITY_MAX_FILES` and `QUALITY_MAX_FILE_SIZE_BYTES`;
analyzer failures are surfaced while preserving results from other analyzers.

### Option B — run each service manually

```bash
# Code quality service
cd backend/services/code-quality-service
pip install -r requirements.txt
PYTHONPATH=../.. uvicorn app.main:app --reload --port 8002

# Fix & Regeneration service (in another terminal)
cd backend/services/repair-service
pip install -r requirements.txt
OLLAMA_BASE_URL=http://localhost:11434/api/generate OLLAMA_MODEL=qwen2.5-coder:3b \
   uvicorn app.main:app --reload --port 8004

# Report/recommendation service (in another terminal)
cd backend/services/report-recommendation-service
pip install -r requirements.txt
OLLAMA_BASE_URL=http://localhost:11434/api/generate OLLAMA_MODEL=qwen2.5-coder:3b \
   uvicorn app.main:app --reload --port 8003

# Orchestrator (in a third terminal)
cd backend/services/orchestrator-service
pip install -r requirements.txt
ORCH_QUALITY_SERVICE_URL=http://localhost:8002 \
ORCH_REPAIR_SERVICE_URL=http://localhost:8004 \
ORCH_REPORT_RECOMMENDATION_SERVICE_URL=http://localhost:8003 PYTHONPATH=../.. \
  uvicorn app.main:app --reload --port 8001

# Gateway (in a fourth terminal)
cd backend/gateway
pip install -r requirements.txt
GATEWAY_ORCHESTRATOR_URL=http://localhost:8001 \
GATEWAY_DATABASE_URL=sqlite+aiosqlite:///./gateway.db PYTHONPATH=.. \
   uvicorn app.main:app --reload --port 8000

# Frontend (in a fifth terminal)
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

`PYTHONPATH=../..` (pointing at `backend/`, two levels up from each service
folder) is needed because services import `shared.schemas.*` from
`backend/shared/`.

## Running tests

```bash
cd backend/services/code-quality-service && PYTHONPATH=../.. pytest
cd backend/services/orchestrator-service && PYTHONPATH=../.. pytest
cd backend/gateway && PYTHONPATH=.. pytest
python -m pytest backend/evaluation/tests -q
```

### Dataset-based agent evaluation

Evaluation datasets are JSONL files in `backend/evaluation/datasets/`. Cases
identify supported source code and a target (`validation`, `analysis`, `repair`,
`security`, `bug_resource`, or `workflow`) and declare expected statuses,
finding codes, optional `expected_findings` rule/severity pairs, and required
workflow agents. The runner calls the existing service APIs rather than
duplicating their validation, analysis, repair, security, bug/resource, or
orchestration logic. JSON reports include per-case actual and expected values,
assertion failures, durations, and aggregate pass rates.
These JSONL files are evaluation fixtures, not executable production rule
configuration. Production checks remain defined by each service's configured
analyzers and implementation; evaluation expectations assert their returned
results.

The Bug/Resource service also loads versioned per-language rule catalogs from
`backend/shared/validation_rules/`. These catalogs currently configure the
Python AST checks and targeted Tree-sitter checks for non-Python languages,
including whole-file reads and unmatched C/C++ heap allocations. They do not
represent exhaustive language validation or prove a program free of logic and
resource bugs; native compiler and analyzer results remain separate checks.

Run all bundled datasets against the Compose services:

```powershell
docker compose --profile evaluation run --build --rm evaluation
```

The full run includes repair and the all-agent workflow dataset. It requires
healthy services and an available configured Ollama model. To run a single
suite and save its report:

```powershell
docker compose --profile evaluation run --build --rm evaluation `
  python -m backend.evaluation.runner `
  --dataset backend/evaluation/datasets/python_cases.jsonl `
  --report backend/evaluation/reports/python-report.json
```

The case contract supports `id`, `language`, `target`, `filename`, `code`,
optional analyzer `tools`, repair `findings`, workflow `checks`, and target-
specific expected statuses, finding codes, rule/severity pairs, resolved
finding IDs, finding count bounds, and required agents. Security and
Bug/Resource cases call their respective service APIs directly. When running
the evaluation CLI outside Compose, configure `EVAL_SECURITY_URL` and
`EVAL_BUG_RESOURCE_URL` if those services use non-default host ports. Its JSON
report includes aggregate, per-language, per-agent, and per-workflow-stage
results. Skipped, disabled, not-run, and not-applicable agents/stages are
counted separately and excluded from executed pass-rate denominators.
Non-Python validation runs the language-specific
compiler/checker in compile-only mode; it does not install project dependencies
or run a full project test suite.

## Code map and useful commands

Run these commands from the repository root to understand where the main
application code lives and how it is used.

```powershell
Set-Location D:\projects\coding-validation-agent-sreya\coding-validation-agent
```

### List application source files

```powershell
Get-ChildItem .\backend\gateway\app, .\backend\services, .\backend\shared, .\frontend\src -Recurse -File |
   Where-Object { $_.Extension -in ".py", ".ts", ".tsx", ".css" } |
   Select-Object -ExpandProperty FullName
```

### Find Python entry points

```powershell
Get-ChildItem . -Recurse -Filter main.py -File |
   Where-Object { $_.FullName -notmatch "\\.venv\\|site-packages" } |
   Select-Object -ExpandProperty FullName
```

The main Python entry points are:

```text
backend/gateway/app/main.py                                  Gateway API
backend/services/orchestrator-service/app/main.py            LangGraph orchestrator
backend/services/code-quality-service/app/main.py             Code Quality API
```

### Find all API routes

```powershell
rg "@(app|router)\.(get|post|put|delete)|APIRouter" gateway services -g "*.py"
```

### Find orchestrator workflow usage

```powershell
rg "workflow|StateGraph|add_node|add_edge|should_retry|ainvoke|astream" `
   backend/services/orchestrator-service/app -g "*.py"
```

### Find all agent/service clients

```powershell
Get-ChildItem .\backend\services\orchestrator-service\app\services -File |
   Select-Object Name, FullName
```

```powershell
rg "from app\.services|security_client|quality_client|bug_resource_client|repair_client|sandbox_client|validation_client" `
   backend/services/orchestrator-service/app -g "*.py"
```

### Find Code Quality tools and their usage

```powershell
Get-ChildItem .\backend\services\code-quality-service\app\analyzers -File |
   Select-Object Name, FullName
```

```powershell
rg "run_ruff|run_pylint|run_radon|ruff|pylint|radon|normalize" `
   backend/services/code-quality-service backend/services/orchestrator-service -g "*.py"
```

Current tool ownership:

```text
backend/services/code-quality-service/app/analyzers/ruff_analyzer.py
backend/services/code-quality-service/app/analyzers/pylint_analyzer.py
backend/services/code-quality-service/app/analyzers/radon_analyzer.py
backend/services/code-quality-service/app/parsers/result_normalizer.py
```

### Find frontend pages, components, and API calls

```powershell
Get-ChildItem .\frontend\src -Recurse -File |
   Where-Object { $_.Extension -in ".tsx", ".ts" } |
   Select-Object -ExpandProperty FullName
```

```powershell
rg "api\.|fetch\(|EventSource|UploadPage|AnalysisPage|ResultsPage|FindingsReviewPage|RepairHistoryPage" `
   frontend/src -g "*.ts" -g "*.tsx"
```

### Run the automated checks

```powershell
Set-Location .\backend\services\orchestrator-service
$env:PYTHONPATH = "../.."
.\.venv\Scripts\python.exe -m pytest -q

Set-Location ..\code-quality-service
$env:PYTHONPATH = "../.."
.\.venv\Scripts\python.exe -m pytest -q

Set-Location ..\..\..\frontend
npm run build
```

### Run the complete Docker application

```powershell
Set-Location D:\projects\coding-validation-agent-sreya\coding-validation-agent-frontend

$env:GATEWAY_PORT = "8010"
$env:ORCHESTRATOR_PORT = "8011"
$env:QUALITY_PORT = "8012"
$env:FRONTEND_PORT = "5174"
$env:FRONTEND_API_BASE_URL = "http://localhost:8010"

docker compose config --quiet
docker compose up --build -d
docker compose ps
```

Check the running services:

```powershell
Invoke-RestMethod http://localhost:8010/health
Invoke-RestMethod http://localhost:8011/health
Invoke-RestMethod http://localhost:8012/health
```

Open the application and API documentation:

```text
Frontend:      http://localhost:5174
Gateway docs:  http://localhost:8010/docs
Orchestrator:  http://localhost:8011/docs
Quality docs:  http://localhost:8012/docs
```

View service logs:

```powershell
docker compose logs -f orchestrator
docker compose logs -f code-quality
docker compose logs -f gateway
```

Stop the Docker application:

```powershell
docker compose down
```

## Person 1 verification runbook (Windows PowerShell)

From the repository root, use separate terminals for the services:

```powershell
# Terminal 1: code-quality service
Set-Location .\backend\services\code-quality-service
$env:PYTHONPATH = "../.."
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8002

# Terminal 2: orchestrator / manager agent
Set-Location .\backend\services\orchestrator-service
$env:PYTHONPATH = "../.."
$env:ORCH_QUALITY_SERVICE_URL = "http://localhost:8002"
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8001

# Terminal 3: gateway
Set-Location .\backend\gateway
$env:PYTHONPATH = ".."
$env:GATEWAY_ORCHESTRATOR_URL = "http://localhost:8001"
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000

# Terminal 4: frontend
Set-Location .\frontend
npm install
$env:VITE_API_BASE_URL = "http://localhost:8000"
npm run dev
```

Open `http://localhost:5174`. Docker Compose starts the configured gateway,
orchestrator, analysis agents, repair, validation, report, and sandbox
services. The report and repair stages require the configured Ollama model.

Useful checks after the services start:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health

Set-Location .\backend\services\orchestrator-service
$env:PYTHONPATH = "../.."
python -m pytest -q

Set-Location ..\code-quality-service
$env:PYTHONPATH = "../.."
python -m pytest -q

Set-Location ..\..\..\frontend
npm run build
```

The manager status endpoint is available at
`GET http://localhost:8000/analysis/status/{project_id}` while a run is
queued or running, and after the final report is produced.

## A2A orchestrator integration

The orchestrator exposes an A2A-compatible task boundary in addition to its
existing gateway workflow APIs:

- `GET /.well-known/agent-card.json` discovers the orchestrator skills.
- `POST /a2a/v1/tasks` accepts `workflow.start`, `workflow.status`, and
   `agent.result` messages.

The A2A `workflow.start` task is translated into the existing
`StartAnalysisRequest` and runs through the same LangGraph workflow, so A2A
does not create a second orchestration path. Specialized agents can submit
normalized results with `agent.result`; their internal implementations remain
independent of the orchestrator.

## Validation and report semantics

The final validation status describes acceptance, not whether the workflow
request itself completed: `passed` means required execution and validation
checks passed; `failed` means a required check found a blocker; `needs_review`
means checks completed with findings for human review; `error` means a required
service or execution step failed; `blocked` means a prerequisite prevented a
check from running; and `not_run` means final validation was disabled or
skipped. An absent result is never treated as a pass.

When post-repair re-analysis completes, the report uses its quality summary
and remaining findings for final metrics and priorities. Original findings
remain in the comparison as resolved, unchanged, new, or unverified; scores
that were not returned are displayed as unavailable rather than estimated.
The Analysis report and its PDF export use the same report data.

## SRS coverage and current boundaries

Implemented in this repository: React/Monaco intake and review, Code Quality
Agent, AST-based Code Analysis, LangGraph coordination, shared finding
aggregation, bounded repair/validation routing, stage/error history,
repair-attempt records, post-repair reruns of enabled analyzers, per-finding
comparison (with incomplete analyzer results marked partial), report generation,
status/SSE progress, and Gateway PostgreSQL persistence for projects, source
files, analysis runs, findings, tool results, workflow events, repairs,
validation results, reports, Argon2-authenticated accounts, database-backed
sessions, password recovery, and project ownership. The Orchestrator retains
its JSON run store as an internal fallback.

The Security, Bug & Resource, Fix & Regeneration, Docker Sandbox, and
Validation services are represented by HTTP adapters because their teammate
services are not present in this repository. The orchestrator will preserve
their failure evidence and will never mark a run passed when sandbox or
validation evidence is unavailable. PostgreSQL now backs Gateway project and
run data; versioned schema migrations, authentication/authorization, and
ownership enforcement are implemented by the Gateway. No LLM provider is configured, so
LLM-based explanations and repairs remain downstream integration points rather
than being fabricated locally.

## How this integrates with Rakshitha and Shifa's work

The orchestrator never imports another agent's code directly — it only
calls HTTP clients in `backend/services/orchestrator-service/app/services/`:

| Client file | Calls | Owned by |
|---|---|---|
| `quality_client.py` | code-quality-service | Sreya (done) |
| `security_client.py` | security-service | Rakshitha |
| `repair_client.py` | repair-service | Rakshitha |
| `bug_resource_client.py` | bug-resource-service | Shifa |
| `sandbox_client.py` | sandbox-service | Shifa |
| `validation_client.py` | validation-service | Shifa |

Until those services exist, every placeholder client fails **gracefully**
(returns an empty findings list / a failed validation, never raises) so
the full LangGraph workflow can already be run and demoed end-to-end.
Once a teammate's service is ready, add its URL as an env var (see
`backend/services/orchestrator-service/.env.example`) and uncomment its block in
`docker-compose.yml` — no orchestrator code needs to change, because the
integration boundary is already the shared `Finding` contract in
`backend/shared/schemas/`.

## Next steps toward the final app

1. Rakshitha and Shifa add their six services under `backend/services/`, each
   with its own `Dockerfile` and a `/analyze`, `/repair`, `/execute`, or
   `/validate` endpoint matching the shapes in `backend/shared/schemas/workflow.py`.
2. Add a `backend/gateway/` API-entry-point service in front of all of them
   (per the original architecture doc) so the frontend talks to one
   URL instead of the orchestrator directly.
3. Uncomment the corresponding services in `docker-compose.yml`.
4. Add `tests/integration/test_end_to_end.py` once all six agents exist.
