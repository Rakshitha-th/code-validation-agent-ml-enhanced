
import type { WorkflowRunResponse } from "../types/finding";
import type { AnalysisCheckOptions } from "../api/configuration";

interface AgentStatusPanelProps {
  workflowStep: string;
  workflowStatus: string;
  progressMessage?: string;
  preview?: boolean;
  result?: WorkflowRunResponse | null;
  analysisChecks?: AnalysisCheckOptions;
}

const AGENTS = [
  ["analysis", "Code Analysis Agent"],
  ["security", "Security Agent"],
  ["quality", "Code Quality Agent"],
  ["resource", "Bug & Resource Agent"],
  ["repair", "Fix & Regeneration Agent"],
  ["validation", "Validation Agent"],
  ["report", "Report Agent"],
];

type AgentCardStatus =
  | "completed"
  | "running"
  | "pending"
  | "failed"
  | "blocked"
  | "needs review"
  | "skipped"
  | "not reported";

interface AgentCheckFlags {
  readonly codeQuality?: boolean;
  readonly code_quality?: boolean;
  readonly security?: boolean;
  readonly bugResource?: boolean;
  readonly bug_resource?: boolean;
  readonly complexity?: boolean;
  readonly automatedRepair?: boolean;
  readonly automated_repair?: boolean;
  readonly finalValidation?: boolean;
  readonly final_validation?: boolean;
  readonly aiReportRecommendations?: boolean;
  readonly ai_report_recommendations?: boolean;
}

const STATUS_SYMBOL: Record<AgentCardStatus, string> = {
  completed: "✓",
  running: "●",
  pending: "○",
  failed: "!",
  blocked: "!",
  "needs review": "~",
  skipped: "–",
  "not reported": "-",
};

const STAGE_AGENTS: Record<string, string[]> = {
  understand: ["analysis"],
  analyze: ["security", "quality", "resource"],
  report_recommend: ["report"],
  reanalyze: ["quality"],
  repair: ["repair"],
  validate: ["validation"],
};

const AGENT_ERRORS: Record<string, string[]> = {
  analysis: ["analysis", "understand"],
  security: ["security"],
  quality: ["quality"],
  resource: ["bug_resource", "resource"],
  repair: ["repair"],
  validation: ["validation"],
  report: ["report_recommend"],
};

const STAGE_ORDER = [
  "understand",
  "analyze",
  "aggregate",
  "explain_prioritize",
  "report_recommend",
  "repair",
  "validate",
  "reanalyze",
  "compare",
  "finalize",
];

function textField(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function isAgentDisabled(
  key: string,
  checks?: AgentCheckFlags,
): boolean {
  if (!checks) return false;

  if (key === "quality") {
    return !(checks.codeQuality ?? checks.code_quality) && !checks.complexity;
  }

  const enabledByAgent: Record<string, boolean | undefined> = {
    security: checks.security,
    resource: checks.bugResource ?? checks.bug_resource,
    repair: checks.automatedRepair ?? checks.automated_repair,
    validation: checks.finalValidation ?? checks.final_validation,
    report:
      checks.aiReportRecommendations ??
      checks.ai_report_recommendations,
  };

  return enabledByAgent[key] === false;
}

function stageStatus(value: unknown): AgentCardStatus | undefined {
  const status = textField(value);

  if (status === "skipped") return "skipped";
  if (status === "failed") return "failed";
  if (status === "blocked") return "blocked";
  if (status === "needs_review") return "needs review";

  if (["completed", "passed", "partial"].includes(status ?? "")) {
    return "completed";
  }

  return undefined;
}

function reportedValidationStatus(
  result: WorkflowRunResponse,
): AgentCardStatus {
  const report = result.final_report;
  const validation =
    result.validation_results ?? report.validation_results ?? {};

  const status = textField(validation.validator_status);
  const passed = validation.validator_passed;

  if (status === "blocked") return "blocked";
  if (status === "error") return "failed";
  if (status === "needs_review") return "needs review";
  if (status === "passed" || passed === true) return "completed";
  if (passed === false) return "failed";

  return "not reported";
}

function reportedAgentStatus(
  key: string,
  result: WorkflowRunResponse,
): AgentCardStatus {
  const report = result.final_report;

  if (isAgentDisabled(key, report.analysis_checks)) {
    return "skipped";
  }

  // Use the Validation Agent's own result, not the combined workflow result.
  if (key === "validation") {
    return reportedValidationStatus(result);
  }

  const errors = [
    ...(report.agent_errors ?? []),
    ...(report.workflow_summary?.agent_failures ?? []),
  ];

  if (
    errors.some((item) =>
      AGENT_ERRORS[key].some((name) =>
        item.agent.toLowerCase().includes(name),
      ),
    )
  ) {
    return "failed";
  }

  const stages = report.stage_history ?? [];
  const analysisStage = [...stages]
    .reverse()
    .find((stage) => stage.stage === "analyze");

  const agentKey = key === "resource" ? "bug_resource" : key;

  const analysisStatus = stageStatus(
    (analysisStage?.agents as Record<string, unknown> | undefined)?.[
      agentKey
    ],
  );

  if (analysisStatus) return analysisStatus;

  const latest = [...stages]
    .reverse()
    .find((stage) =>
      STAGE_AGENTS[textField(stage.stage) ?? ""]?.includes(key),
    );

  return stageStatus(latest?.status) ?? "not reported";
}

function agentFailureReason(
  key: string,
  result?: WorkflowRunResponse | null,
): string | undefined {
  if (!result) return undefined;

  const report = result.final_report;

  const errors = [
    ...(report.agent_errors ?? []),
    ...(report.workflow_summary?.agent_failures ?? []),
  ];

  const agentError = errors.find((item) =>
    AGENT_ERRORS[key].some((name) =>
      item.agent.toLowerCase().includes(name),
    ),
  );

  if (agentError?.error) return agentError.error;

  if (key === "repair") {
    const attempts = [
      ...(report.repair_attempts ?? result.repair_attempts ?? []),
    ].reverse();

    const latestError = attempts
      .map((item) => textField(item.error))
      .find(Boolean);

    if (latestError) return latestError;
  }

  if (key === "validation") {
    const validation =
      result.validation_results ?? report.validation_results ?? {};

    const findings = Array.isArray(validation.findings)
      ? (validation.findings as Record<string, unknown>[])
      : [];

    const firstFinding = findings[0];

    return (
      textField(validation.error) ??
      textField(firstFinding?.description) ??
      textField(firstFinding?.issue)
    );
  }

  if (key === "report") {
    return textField(report.ai_recommendations_status?.message);
  }

  const stage = [...(report.stage_history ?? [])]
    .reverse()
    .find((item) =>
      STAGE_AGENTS[textField(item.stage) ?? ""]?.includes(key),
    );

  return textField(stage?.error) ?? textField(stage?.message);
}

function liveAgentStatus(
  key: string,
  step: string,
  status: string,
  checks?: AgentCheckFlags,
): AgentCardStatus {
  if (isAgentDisabled(key, checks)) return "skipped";

  const assignedStages = Object.entries(STAGE_AGENTS)
    .filter(([, agentKeys]) => agentKeys.includes(key))
    .map(([stage]) => stage);

  if (status === "failed") {
    return assignedStages.includes(step) ? "failed" : "pending";
  }

  if (!["working", "submitted", "queued"].includes(status)) {
    return "pending";
  }

  const rank = STAGE_ORDER.indexOf(step);

  if (rank < 0) return "not reported";
  if (step === "reanalyze" && key === "quality") return "running";

  const assignedRank = Math.min(
    ...assignedStages.map((stage) => STAGE_ORDER.indexOf(stage)),
  );

  if (assignedRank < rank) return "completed";
  if (assignedStages.includes(step)) return "running";

  return "pending";
}

function getAgentCardStatus(
  preview: boolean,
  result: WorkflowRunResponse | null | undefined,
  key: string,
  step: string,
  status: string,
  checks?: AnalysisCheckOptions,
): AgentCardStatus {
  if (preview) {
    return liveAgentStatus(key, step, status, checks) === "skipped"
      ? "skipped"
      : "pending";
  }

  if (result) return reportedAgentStatus(key, result);

  return liveAgentStatus(key, step, status, checks);
}

export function AgentStatusPanel({
  workflowStep,
  workflowStatus,
  progressMessage,
  preview = false,
  result,
  analysisChecks,
}: Readonly<AgentStatusPanelProps>) {
  return (
    <div className="agent-panel">
      <div className="agent-panel__header">
        <span className="kicker">
          A2A / workflow view · {workflowStatus}
        </span>
        <span className="agent-panel__pulse">
          <i />
          {progressMessage ?? "Waiting for workflow"}
        </span>
      </div>

      <div className="agent-grid">
        {AGENTS.map(([key, label], index) => {
          const agentKey = String(key);

          const status = getAgentCardStatus(
            preview,
            result,
            agentKey,
            workflowStep,
            workflowStatus,
            analysisChecks,
          );

          const reason = agentFailureReason(agentKey, result);

          return (
            <div
              className={`agent-card agent-card--${status.replace(" ", "-")}`}
              key={agentKey}
            >
              <span className="agent-card__index">
                0{index + 1}
              </span>

              <span>
                <strong>{label}</strong>
                <small>{status}</small>
                {reason && (
                  <small className="agent-card__detail" title={reason}>
                    {reason}
                  </small>
                )}
              </span>

              <b>{STATUS_SYMBOL[status]}</b>
            </div>
          );
        })}
      </div>

      {result && (
        <div
          className={`workflow-outcome workflow-outcome--${result.validation_status}`}
        >
          <span>{result.validation_status.toUpperCase()}</span>
          <small>
            Validation outcome · {result.retry_count ?? "Not available"} repair retries
          </small>
        </div>
      )}
    </div>
  );
}
