import type { ProjectFile, WorkflowRunResponse } from "../types/finding";
import { recordOf, safeEvidence, textOf } from "./model";

export type RepairComparisonStatus = "pending" | "not-run" | "failed" | "completed" | "unavailable";

function latestStage(result: WorkflowRunResponse, stageName: string): Record<string, unknown> {
  const report = recordOf(result.final_report);
  const stages = result.stage_history ?? (Array.isArray(report.stage_history) ? report.stage_history as Record<string, unknown>[] : []);
  return [...stages].reverse().find((item) => item.stage === stageName) ?? {};
}

export function repairComparisonState(result: WorkflowRunResponse | null): { status: RepairComparisonStatus; error?: string } {
  if (!result) return { status: "pending" };

  const report = recordOf(result.final_report);
  const checks = recordOf(report.analysis_checks);
  const stage = latestStage(result, "repair");
  const attempts = result.repair_attempts ?? (Array.isArray(report.repair_attempts) ? report.repair_attempts as Record<string, unknown>[] : []);
  const lastAttempt = attempts.length > 0 ? attempts[attempts.length - 1] : undefined;
  const error = textOf(lastAttempt?.error);
  const stageStatus = textOf(stage.status)?.toLowerCase().replace(/[ -]/g, "_");
  const outcome = (
    textOf(lastAttempt?.repair_status)
    ?? textOf(lastAttempt?.outcome)
    ?? textOf(report.repair_status)
    ?? textOf(recordOf(report.workflow_summary).repair_status)
  )?.toLowerCase().replace(/[ -]/g, "_");

  if (
    checks.automated_repair === false
    || ["skipped", "not_run", "not_attempted"].includes(stageStatus ?? "")
    || ["not_run", "not_attempted"].includes(outcome ?? "")
  ) return { status: "not-run" };
  if (stageStatus === "running" || stageStatus === "pending") return { status: "pending" };
  if (stageStatus === "failed" || outcome === "failed" || outcome === "error") {
    return { status: "failed", ...(error ? { error: safeEvidence(error) } : {}) };
  }
  if (lastAttempt || stageStatus === "completed" || stageStatus === "partial") {
    return { status: "completed", ...(error ? { error: safeEvidence(error) } : {}) };
  }
  return { status: "unavailable" };
}

export function repairedProjectFiles(result: WorkflowRunResponse | null): ProjectFile[] {
  if (!result) return [];
  const report = recordOf(result.final_report);
  const source = recordOf(report.final_code);
  return Object.entries(source)
    .filter((entry): entry is [string, string] => typeof entry[1] === "string")
    .map(([path, content]) => ({ path, content }));
}
