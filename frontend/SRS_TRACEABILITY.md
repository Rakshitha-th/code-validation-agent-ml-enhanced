# Frontend SRS Traceability

This is an implementation index based on the feature statements supplied for this task. The repository does not contain the formal FR-01..FR-20 definitions, so the labels below are descriptive mappings, not replacements for the approved SRS wording. Backend limitations are tracked in [MISSING_BACKEND.md](MISSING_BACKEND.md).

| Requirement | Frontend page/component |
|---|---|
| FR-01 Python project input | `pages/Upload.tsx`, `components/FileUpload.tsx` |
| FR-02 Projects and project files | `pages/Projects.tsx`, `components/ProjectFilesManager.tsx`, `api/projects.ts`, `api/files.ts` |
| FR-03 Analysis run and live progress | `App.tsx` (`handleStart`, `AnalysisRoute`), `pages/Analysis.tsx`, `components/AnalysisProgress.tsx` |
| FR-04 Code structure | `analysis/StructureTab.tsx`, `analysis/model.ts`, `components/CodeEditor.tsx` |
| FR-05 Security findings and tool evidence | `components/FindingsTable.tsx`, `analysis/ToolsTab.tsx` |
| FR-06 Quality metrics | `analysis/QualityTab.tsx`, `components/QualityOverview.tsx` |
| FR-07 Bug and resource evidence | `analysis/BugResourceTab.tsx`, `analysis/model.ts` |
| FR-08 Finding review and status workflow | `pages/FindingsReview.tsx`, `components/FindingsTable.tsx`, `api/findings.ts` |
| FR-09 Repair candidate and application | `components/RepairActions.tsx`, `components/CodeComparison.tsx`, `api/repair.ts` |
| FR-10 Validation evidence | `components/ValidationResult.tsx`, `pages/Results.tsx` |
| FR-11 Report view and export | `pages/Results.tsx`, `analysis/TraceabilityTab.tsx` |
| FR-12 Finding-to-report traceability | `analysis/TraceabilityTab.tsx` |
| FR-13 Run and repair history | `pages/RepairHistory.tsx`, `pages/Projects.tsx` |
| FR-14 Workspace dashboard | `pages/Dashboard.tsx` |
| FR-15 Tool execution and observability | `analysis/ToolsTab.tsx`, `components/ToolStatusPanel.tsx`, `components/AgentStatusPanel.tsx` |
| FR-16 Administrator user management | `pages/Admin.tsx`, `api/admin.ts` |
| FR-17 Compliance and safe rendering | `components/ComplianceNotice.tsx`, `analysis/model.ts`, `pages/Upload.tsx` |
| FR-18 Session lifecycle | `context/AuthContext.tsx`, `api/client.ts`, `components/RequireAuth.tsx` |
| FR-19 Sandbox and LLM configuration | `components/ConfigurationPanel.tsx`, `pages/AdminSettings.tsx`, `api/configuration.ts` |
| FR-20 Role-based access | `auth/permissions.ts`, `components/RequirePermission.tsx`, `components/RequireAuth.tsx` |
| SRS §4 Roles | `auth/permissions.ts`, `pages/Admin.tsx`, `components/RequirePermission.tsx` |
| SRS §8 Session and access security | `context/AuthContext.tsx`, `api/client.ts`, `components/RequireAuth.tsx` |
| SRS §9 Sandbox and LLM settings | `components/ConfigurationPanel.tsx`, `components/ValidationResult.tsx` |
| SRS §10 Projects and finding workflow | `pages/Projects.tsx`, `components/ProjectFilesManager.tsx`, `components/FindingsTable.tsx` |
| SRS §14 Tool and evidence reporting | `analysis/ToolsTab.tsx`, `analysis/BugResourceTab.tsx` |
| SRS §15 Observability | `analysis/ToolsTab.tsx`, `components/AgentStatusPanel.tsx` |
