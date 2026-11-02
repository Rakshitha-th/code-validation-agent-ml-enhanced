import type { QualityTool, WorkflowRunResponse } from "../types/finding";

interface ToolStatusPanelProps {
  readonly tools: QualityTool[];
  readonly result?: WorkflowRunResponse | null;
  readonly running?: boolean;
}

function getToolResult(result: WorkflowRunResponse, tool: QualityTool): { status: string; findings?: number } {
  const summary = result.quality_summary ?? result.final_report.quality_summary;
  const run = summary?.tool_runs?.find((item) => item.tool.toLowerCase() === tool);
  const toolResult = summary?.tool_results?.[tool];
  const status = run?.status ?? toolResult?.status;
  if (!status || run?.available === false || toolResult?.available === false || ["skipped", "unavailable", "not_run"].includes(status.toLowerCase())) return { status: "Not analyzed" };
  const findings = run?.finding_count ?? toolResult?.finding_count;
  const label = status.toLowerCase() === "completed" ? "Completed" : status.toLowerCase() === "failed" ? "Failed" : status;
  return { status: findings === undefined ? label : `${label} · ${findings} finding${findings === 1 ? "" : "s"}`, findings };
}

export function ToolStatusPanel({ tools, result, running = false }: Readonly<ToolStatusPanelProps>) {
  if (tools.length === 0) return <section className="tool-status"><div className="section-label"><h2>Code quality tools</h2><span>Not selected for this run</span></div></section>;
  return <section className="tool-status"><div className="section-label"><h2>Code quality tools</h2><span>{result ? "Execution results" : running ? "Quality analysis in progress" : "Configured for this run"}</span></div><div className="tool-status__grid">{tools.map((tool) => {
    const actual = result ? getToolResult(result, tool) : undefined;
    const status = actual?.status ?? (running ? "Running" : "Configured");
    const statusClass = status.startsWith("Completed") ? "tool-status__ok" : status === "Failed" || status === "Not analyzed" ? "tool-status__failed" : "tool-status__pending";
    return <div className="tool-status__item" key={tool}><i className={statusClass} /><strong>{tool[0].toUpperCase() + tool.slice(1)}</strong><small>{status}</small></div>;
  })}</div></section>;
}