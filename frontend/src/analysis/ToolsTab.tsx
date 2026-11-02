import type { QualitySummary, WorkflowRunResponse } from "../types/finding";
import { recordOf, safeEvidence, textOf } from "./model";

interface ToolsTabProps { readonly result: WorkflowRunResponse | null; }
const TOOL_RUNS = ["Semgrep", "Bandit", "Gitleaks", "Ruff", "Pylint", "Radon", "Pytest", "Coverage"];

function safeTraceUrl(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return undefined;
    if (url.username || url.password || /token|key|secret|auth|signature/i.test(url.hash)) return undefined;
    for (const [key] of url.searchParams) if (/token|key|secret|auth|signature/i.test(key)) return undefined;
    return url.href;
  } catch { return undefined; }
}

interface ToolRun {
  readonly name: string;
  readonly status: string;
  readonly agent: string;
  readonly duration?: number;
  readonly exitCode?: number | null;
  readonly error?: string;
  readonly output?: string;
}

function buildToolRuns(result: WorkflowRunResponse | null): ToolRun[] {
  const summary = result?.final_report.quality_summary as (QualitySummary & { readonly tools?: Record<string, unknown> }) | undefined;
  return TOOL_RUNS.map((name) => {
    const key = name.toLowerCase();
    const run = summary?.tool_runs?.find((item) => item.tool.toLowerCase() === key);
    const data = recordOf(summary?.tool_results?.[key] ?? summary?.tools?.[key] ?? run);
    const unavailable = data.available === false || data.status === "unavailable" || data.status === "skipped";
    return {
      name,
      status: unavailable ? "Tool unavailable/skipped" : safeEvidence(textOf(data.status) ?? "Tool unavailable/skipped"),
      agent: safeEvidence(textOf(data.agent) ?? (key === "ruff" || key === "pylint" || key === "radon" ? "Code Quality Agent" : "Not reported")),
      duration: typeof data.duration_ms === "number" ? data.duration_ms : undefined,
      exitCode: typeof data.exit_code === "number" ? data.exit_code : null,
      error: textOf(data.error),
      output: textOf(data.raw_output) ?? textOf(data.output),
    };
  });
}

export function ToolsTab({ result }: Readonly<ToolsTabProps>) {
  const runs = buildToolRuns(result);
  const report = recordOf(result?.final_report);
  const traceUrl = textOf(recordOf(report.workflow_summary).trace_url) ?? textOf(report.trace_url);
  const safeTrace = safeTraceUrl(traceUrl);
  return <section className="analysis-view">
    <div className="section-label"><h2>Tool execution</h2>{safeTrace ? <a href={safeTrace} target="_blank" rel="noopener noreferrer">Open trace ↗</a> : <span>Trace URL not provided</span>}</div>
    {!result && <p className="empty-state">Tool evidence is not available until this run completes.</p>}
    <div className="table-scroll"><table className="findings-table"><thead><tr><th>Tool</th><th>Agent</th><th>Status</th><th>Duration</th><th>Exit code</th><th>Failure context</th></tr></thead><tbody>{runs.map((run) => <tr key={run.name}><td>{run.name}</td><td>{run.agent}</td><td>{run.status}</td><td>{run.duration === undefined ? "-" : `${run.duration} ms`}</td><td>{run.exitCode ?? "-"}</td><td>{run.error ? safeEvidence(run.error) : "-"}<details><summary>Raw output</summary><pre>{run.output ? safeEvidence(run.output) : "No raw output returned."}</pre></details></td></tr>)}</tbody></table></div>
  </section>;
}