# Shared contracts

These schemas are the integration contract between all three members' services.

- `finding.py` — the common `Finding` shape every analysis agent (Security, Code
  Quality, Bug/Resource) must return. This is what lets the Orchestrator's
  Finding Aggregator merge results from services built independently.
- `workflow.py` — request/response shapes for analysis, repair, and validation
  calls between the orchestrator and downstream services.
- `validation.py` — validation result contract (used by Shifa's validation
  service and consumed by the orchestrator).
- `project.py` — project metadata shape used by the gateway/frontend.

Do not change these without agreement from the other two members — every
service imports from here (`from shared.schemas.finding import Finding`).
