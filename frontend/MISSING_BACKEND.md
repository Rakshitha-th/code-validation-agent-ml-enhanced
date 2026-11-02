# Frontend backend contract gaps

The frontend's mock implementations run only when `VITE_USE_MOCKS=true`. In normal mode these operations call the documented route and surface a safe unavailable error; they do not silently report success.

## Confirmed routes and contract mismatches

- `POST /api/projects` exists and accepts `{ name, files: [{ path, content }], source_code?, project_context? }`; it returns `{ project_id, name, file_count, created_at }`. Project data is kept in gateway process memory.
- `POST /projects/upload` exists as multipart form data (`name`, repeated `files`), but returns metadata only and does not retain file contents. It is not the SRS path `/api/projects/upload` and cannot support analysis of the uploaded contents. Required improvement: persist or store uploaded contents and return `{ project_id, name, file_count, created_at, files_persisted: true }`.
- `POST /analysis/run` exists and accepts `{ project_id, files: [{ path, content }], project_context?, tools? }`; it proxies the orchestrator response. The SRS example `/api/analysis/start` does not exist.
- `GET /analysis/stream/{project_id}` exists as SSE. `GET /analysis/status/{project_id}` and `GET /analysis/{project_id}/findings` exist. There is no documented stable event schema/version or cancellation event.
- `GET /api/analysis/{run_id}/status` and `GET /api/analysis/{run_id}/results` exist. Run-to-project mapping is in process memory; persist this mapping for restart-safe IDs.
- `POST /api/agents/code-quality/analyze` exists. `GET /results/{project_id}` exists for a completed workflow report.

## Missing endpoints needed by SRS UI

| Method and path | Request shape | Expected response shape | Needed for |
|---|---|---|---|
| `POST /api/auth/login` | `{ email, password }` | `{ access_token, token_type, user: { id, email, full_name, role } }` | Login and role guard |
| `GET /api/auth/me` | Bearer token | `{ id, email, full_name, role }` | Session restore / current user |
| `POST /api/auth/logout` | Bearer token | `204` or `{ success: true }` | Logout/revocation |
| `POST /api/auth/register` | `{ full_name, email, password }` | `{ user, access_token, token_type }` | Optional signup UI |
| `POST /api/projects/upload` | Multipart `name`, repeated files or archive | `{ project_id, name, file_count, created_at, files_persisted: true }` | SRS upload path with retained files |
| `GET /api/projects` | Query pagination/filter values | `{ items: Project[], total, page }` | Project management / dashboard |
| `GET /api/projects/{project_id}` | None | `{ project_id, name, description, files: [{ path, size }] }` | Project metadata and file tree |
| `PATCH /api/projects/{project_id}` | `{ name?, description? }` | Updated `Project` | Edit project metadata |
| `DELETE /api/projects/{project_id}` | None | `204` | Remove project |
| `POST /api/analysis/{run_id}/cancel` | None | `{ run_id, status: "cancelled" }` | Cancel workflow |
| `POST /api/repair` | `{ project_id, finding_ids?, mode, max_iterations }` | `{ attempts: RepairAttempt[], candidate_files: ProjectFile[] }` | Targeted/full repair UI |
| `GET /api/analysis/{run_id}/repairs` | None | `{ attempts: RepairAttempt[] }` | Repair timeline/history |
| `POST /api/validation` | `{ project_id, files, tests? }` | `ValidationResult` including sandbox evidence | Standalone validation UI |
| `GET /api/validation/{run_id}` | None | `ValidationResult` | Validation page reload |
| `GET /api/report/{run_id}` | None | `{ project_id, summary, findings, repairs, validation, recommendations }` | SRS report route |
| `GET /api/files?project_id={id}` | Query `project_id` | `{ project_id, files: [{ path, content }] }` | File preview and reload |
| `POST /api/files` | `{ project_id, files: [{ path, content }] }` | `{ project_id, files }` | Save/remove/replace files |
| `GET /api/history` | Optional project/date/status/severity/page filters | `{ items: HistoryItem[], total }` | Persistent history and comparison |
| `GET /api/history/compare?before={runId}&after={runId}` | Query run IDs | `{ fixed: Finding[], new: Finding[], remaining: Finding[] }` | Compare runs |
| `GET /api/admin/users` | Bearer token with Administrator role | `{ items: User[] }` | Admin user/role list |
| `PATCH /api/admin/users/{user_id}` | `{ role: Role }` | Updated `User` | Role management |
| `GET /api/admin/config` / `PATCH /api/admin/config` | Config object for update | `{ default_max_iterations, enabled_tools, llm_provider }` | Platform settings |
| `GET /api/admin/health` | Administrator token | `{ status, latency_ms, agent_failures, tool_failures }` | Monitoring cards |
| `GET /api/admin/audit` | Filter/pagination query | `{ items: AuditEvent[], total }` | Audit log viewer |

The gateway also needs authorization middleware and persistent storage before authenticated, role-protected, cross-session UI features can be production-ready.

## Additional Phase A contracts

The existing project list/detail rows above need to include the fields consumed by the new UI: `GET /api/projects` returns paginated `{ items, total, page, page_size }`, with each item carrying `owner`, `status`, and `last_run`; `GET /api/projects/{project_id}` returns `{ project_id, name, description, owner, status, created_at, file_count, files: [{ path, size, last_modified }], run_history: HistoryItem[] }`. The backend must enforce ownership on reads, updates, and deletes.

| Method and path | Request shape | Expected response shape | Needed for |
|---|---|---|---|
| `PATCH /api/findings/{finding_id}/status` | `{ status: "Open" | "In repair" | "Needs human review" | "False positive" | "Won't fix", comment? }` | `{ status, changed_by, changed_at, comment? }` | Finding workflow; `Fixed (validated)` is reserved for PASS validation automation |
| `GET /api/findings/{finding_id}/status-history` | None | `{ items: [{ status, changed_by, changed_at, comment? }] }` | Status audit trail |
| `POST /api/admin/users` | `{ full_name, email, role: "Developer" | "Reviewer" | "Administrator" }` | `{ id, full_name, email, role, active }` | Admin user creation |

`PATCH /api/admin/users/{user_id}` must support `{ full_name?, email?, role?, active? }` and return the updated user; role and activation changes must be authorized and audited server-side. The existing gateway currently has no auth router, authorization middleware, status persistence, audit trail, or administrative user store, so these screens are production-blocked without backend implementation.

## Additional Phase B evidence gaps

The current workflow report returns a basic `quality_summary`, `tool_results`, `hotspots`, `per_file`, `repair_attempts`, `validation_results`, `sandbox_result`, and `stage_history`. It does not consistently provide exact maintainability index, all-function Radon values, stable execution IDs, runtime resource metrics, raw output for each tool, or a trace URL. Extend `GET /api/analysis/{run_id}/results` (or expose the following evidence endpoint) with these fields; do not synthesize missing measurements in the frontend.

| Method and path | Request shape | Expected response shape | Needed for |
|---|---|---|---|
| `GET /api/analysis/{run_id}/evidence` | None | `{ run_id, code_structure?, quality: { maintainability_index?, cyclomatic: [{ name, file, line, complexity, rank }], by_tool, code_style_count, per_file }, runtime: { peak_memory_mb?, cpu_percent?, duration_ms? }, tool_runs: [{ tool, agent, status, duration_ms?, exit_code?, error?, raw_output? }], validation_id?, repair_attempts: [{ attempt_id, iteration, validation_id? }], trace_url? }` | Stable Structure/Quality/Bug & Resource/Tools/Trace detail payload |

The Structure tab currently parses submitted Python source locally; it does not imply server-side AST evidence. `GET /api/analysis/{run_id}/status` and `/results` should return the stable run ID, and validation/repair records need their own IDs so trace links can identify actual persisted records. `trace_url` must be an approved HTTP(S) URL; tool output must be redacted before being returned, particularly Gitleaks output.

## Additional Phase C contracts

| Method and path | Request shape | Expected response shape | Needed for |
|---|---|---|---|
| `POST /api/projects/{project_id}/apply-repair` | `{ attempt_id, files: [{ path, content }] }` | `{ success, project_id, attempt_id }`; apply only an accepted attempt with PASS validation | Apply a repair after diff preview |
| `POST /api/llm/test-connection` | `{ provider, model, api_key? }` | `{ reachable, message? }`, never echo `api_key` | New analysis and LLM connection test |
| `GET /api/admin/config` | Administrator bearer token | Sandbox limits, network policy, provider/model, `api_key_configured`; never return the API key | Admin settings restore |

File endpoints already listed above need these concrete semantics: `GET /api/files?project_id={id}` returns source contents; `POST /api/files` replaces/saves `{ project_id, files: [{ path, content }] }` atomically and supports create, rename, and delete through the submitted file set. The gateway must reject absolute paths, `..` segments, and non-Python source paths. `PATCH /api/admin/config` must accept validated sandbox timeout (1–600 seconds), memory (64–8192 MB), CPU (0.1–8 cores), network policy, LLM provider/model, and an optional write-only `api_key`; return only a configured boolean for secrets.

The analysis request may carry sandbox/LLM provider options in `project_context`, but the current orchestrator does not establish that it applies them. Validation evidence must echo effective settings and a stable `validation_id`; otherwise the UI labels the values as options sent, not proven sandbox settings. The LLM test endpoint and config update must avoid request-body/token/secret logging and must not persist a key supplied only for a one-off connection test.
