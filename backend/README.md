# Backend layout

All backend application components now live under this directory:

- `gateway/` — public API and persistence boundary.
- `services/` — orchestrator and specialized agent services.
- `shared/` — shared Python request/response schemas.
- `app/` — legacy repair prototype retained for compatibility; it is not
  started by Docker Compose.
- `evaluation/` — JSONL evaluation suites and the reusable evaluation CLI.

The repository-root `docker-compose.yml` keeps its build context at the
repository root so service Dockerfiles can copy both their implementation and
`backend/shared/`. Use `docker compose config --quiet` to verify the Compose
configuration after changing backend paths.

For the evaluation runner's case format and execution command, see the
dataset-evaluation section in the root README.
