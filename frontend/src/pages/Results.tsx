import { useState } from "react";
import { Link } from "react-router-dom";
import { AnalysisReport } from "../analysis/AnalysisReport";
import { FindingsTable } from "../components/FindingsTable";
import { SandboxOutput, ValidationResult } from "../components/ValidationResult";
import { CodeEditor } from "../components/CodeEditor";
import type { Finding, ProjectFile, WorkflowRunResponse } from "../types/finding";
import { RequirePermission } from "../components/RequirePermission";
import { TraceabilityTab } from "../analysis/TraceabilityTab";
import { RepairActions } from "../components/RepairActions";
import { ComplianceNotice } from "../components/ComplianceNotice";

interface ResultsPageProps {
  originalFiles: ProjectFile[];
  result: WorkflowRunResponse;
  completedAt?: string;
  onRerun: () => void;
}

const REPORT_EXPORT_STYLES = `
*{box-sizing:border-box}body{margin:0;padding:32px;background:#f4f7fd;color:#182235;font:14px/1.55 Arial,Helvetica,sans-serif}.analysis-report{max-width:1080px;margin:auto;background:#fff;padding:32px}.report-cover,.report-section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.report-cover{border-bottom:3px solid #5b4ce2;padding-bottom:20px}.report-cover h2,.report-section h2{margin:4px 0 10px}.report-project-id,.report-eyebrow{color:#65728a}.report-eyebrow{font-size:10px;letter-spacing:.14em}.report-status,.report-severity-pill{display:inline-block;border-radius:999px;padding:6px 10px;background:#eef0f5}.report-status--passed,.report-status--completed{color:#166534;background:#e8f6ec}.report-status--failed{color:#991b1b;background:#fdecec}.report-status--progress{color:#854d0e;background:#fff4dc}.report-section{margin:24px 0;border:1px solid #dfe5ef;border-radius:8px;padding:18px;break-inside:auto}.report-section h2{font-size:20px}.report-meta-grid,.report-metric-grid,.report-severity-grid,.report-resource-grid,.report-comparison-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.report-meta,.report-metric,.report-severity-card,.report-priority-card,.report-comparison-column{border:1px solid #dfe5ef;border-radius:7px;padding:12px;background:#f8f9fc;overflow-wrap:anywhere}.report-meta dt,.report-metric span,.report-severity-card span{color:#65728a;font-size:11px}.report-meta dd{margin:5px 0 0;font-weight:700}.report-meta small{color:#65728a}.report-metric strong,.report-severity-card strong{display:block;margin-top:5px;font-size:19px}.report-explanation--failed{border-left:4px solid #b42318}.report-explanation__lead{font-weight:700}.report-pipeline{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;padding:0;list-style:none}.report-pipeline__stage{display:grid;grid-template-columns:34px 1fr;gap:3px 8px;border:1px solid #dfe5ef;border-radius:7px;padding:10px}.report-pipeline__stage strong,.report-pipeline__stage small{grid-column:2}.report-pipeline__stage--completed strong{color:#166534}.report-pipeline__stage--failed strong{color:#991b1b}.report-table{width:100%;border-collapse:collapse}.report-table th,.report-table td{text-align:left;padding:9px;border-bottom:1px solid #e4e8f0}.report-finding,.report-file-detail,.report-attempt-list>li{border:1px solid #dfe5ef;border-radius:7px;margin:10px 0;padding:12px;break-inside:avoid}.report-finding summary{display:flex;flex-wrap:wrap;align-items:center;gap:8px;cursor:pointer}.report-finding__meta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.report-finding__body p,.report-availability{overflow-wrap:anywhere}.report-code-block{margin:10px 0;background:#111827;color:#e2e8f0;padding:10px;overflow:auto}.report-code-block>p{margin:0 0 8px;color:#b8c4da}.report-code-line{display:flex;gap:12px;white-space:pre}.report-code-line--highlight{background:#4338ca}.report-code-line b{min-width:28px;color:#94a3b8;text-align:right}.report-code-line code{color:inherit}.report-text-button{border:0;background:none;color:#4338ca;text-decoration:underline}.report-priority-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.report-priority-card h3{margin-top:0}.report-attempt-list{padding-left:20px}.report-attempt-list dl{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.report-comparison-column p{display:flex;justify-content:space-between}.report-recommendation{border-top:3px solid #5b4ce2}.report-footnote{color:#65728a;font-size:11px}@media(max-width:680px){body{padding:10px}.analysis-report{padding:14px}.report-meta-grid,.report-metric-grid,.report-severity-grid,.report-resource-grid,.report-comparison-grid,.report-pipeline,.report-priority-grid,.report-finding__meta,.report-attempt-list dl{grid-template-columns:repeat(2,minmax(0,1fr))}}@media print{@page{size:auto;margin:14mm}body{padding:0;background:#fff}.analysis-report{max-width:none;padding:0}.report-section,.report-meta,.report-metric,.report-severity-card,.report-finding,.report-file-detail,.report-attempt-list>li{box-shadow:none;break-inside:avoid}.report-view-file{display:none!important}.report-section-heading p{font-size:10px}}
`;
const REPORT_EXPORT_FINDING_STYLES = `
.report-finding{padding:0}.report-finding__header{display:grid;gap:8px;padding:12px}.report-finding__badges{display:flex;flex-wrap:wrap;align-items:center;gap:7px}.report-priority-pill,.report-finding__category,.report-finding__status{border:1px solid #dfe5ef;border-radius:4px;padding:3px 6px;color:#526078;background:#f8f9fc;font-size:9px}.report-priority-pill{font-family:monospace;font-weight:700}.report-priority-pill--critical{color:#991b1b;background:#fdecec}.report-priority-pill--high{color:#9a3412;background:#fff0e8}.report-priority-pill--medium{color:#854d0e;background:#fff4dc}.report-priority-pill--low,.report-priority-pill--info{color:#075985;background:#e7f4fb}.report-finding__header h3{margin:0;font-size:13px;overflow-wrap:anywhere}.report-finding__location{color:#65728a;font:9px monospace;overflow-wrap:anywhere}.report-finding__status{width:fit-content;font-weight:700}.report-finding__status--fixed{color:#166534;background:#e8f6ec}.report-finding__status--unresolved{color:#854d0e;background:#fff4dc}.report-finding__body{border-top:1px solid #dfe5ef;padding:12px}.report-finding__body>p{color:#526078;font-size:11px;line-height:1.55;overflow-wrap:anywhere}.report-finding__description{margin:0 0 10px}.report-finding__recommendation{display:grid;gap:3px;margin:0;border-left:3px solid #5b4ce2;padding:8px 10px;background:#f8f9fc}.report-finding__details{margin-top:10px;color:#526078;font-size:10px}.report-finding__details>summary{width:fit-content;color:#4338ca;cursor:pointer;font-weight:600}.report-finding__details[open]>summary{margin-bottom:10px}
`;

export function ResultsPage({ originalFiles, result, completedAt, onRerun }: Readonly<ResultsPageProps>) {
  const [selectedFinding, setSelectedFinding] = useState<Finding | null>(null);
  const [inspectedFilePath, setInspectedFilePath] = useState<string | null>(null);
  const availableFiles = originalFiles.length > 0 ? originalFiles : result.project_files ?? [];
  const repairedFiles: ProjectFile[] = Object.entries(result.final_report.final_code ?? {}).map(([path, content]) => ({ path, content }));
  const inspectedFile = availableFiles.find((file) => file.path === inspectedFilePath);

  function downloadReportDocument() {
    const reportElement = document.getElementById("analysis-report");
    if (!reportElement) return;
    const escapedProjectId = result.project_id.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[character] ?? character);
    const reportHtml = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Analysis report - ${escapedProjectId}</title><style>${REPORT_EXPORT_STYLES}${REPORT_EXPORT_FINDING_STYLES}</style></head><body>${reportElement.outerHTML}</body></html>`;
    const blob = new Blob([reportHtml], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const filename = result.project_id.replace(/[^a-zA-Z0-9_-]+/g, "-") || "project";
    link.href = url;
    link.download = `${filename}-analysis-report.html`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <section className="page page--results">
      <div className="report-toolbar report-interactive">
        <div className="results-actions">
          <Link className="btn btn--link" to={`/analysis/${encodeURIComponent(result.project_id)}`}>Analysis evidence</Link>
          <RequirePermission capability="createAnalysis"><button className="btn btn--primary" onClick={onRerun}>Re-run analysis</button></RequirePermission>
          <button className="btn" onClick={() => window.print()}>Print / Save as PDF</button>
          <button className="btn" onClick={downloadReportDocument}>Download HTML report</button>
          </div>
      </div>
      <ComplianceNotice />
      <AnalysisReport result={result} sourceFiles={availableFiles} completedAt={completedAt} onViewFile={setInspectedFilePath} />
      <SandboxOutput report={result.final_report} />
      {selectedFinding && <section className="result-section finding-inspector report-interactive"><div className="section-label"><h2>Selected finding</h2><span>{selectedFinding.tool ?? "Tool not available"} · line {selectedFinding.line_number ?? "Not available"}</span></div><div className="finding-inspector__summary"><strong>{selectedFinding.issue}</strong><span>{selectedFinding.description}</span></div>{availableFiles.find((file) => file.path === selectedFinding.file) && <CodeEditor key={`${selectedFinding.finding_id}-${selectedFinding.line_number}`} path={selectedFinding.file} value={availableFiles.find((file) => file.path === selectedFinding.file)?.content ?? ""} readOnly highlightLine={selectedFinding.line_number ?? undefined} height="300px" />}</section>}
      {inspectedFile && <section className="result-section finding-inspector report-interactive"><div className="section-label"><h2>Source file</h2><span>{inspectedFile.path}</span></div><CodeEditor key={inspectedFile.path} path={inspectedFile.path} value={inspectedFile.content} readOnly height="400px" /></section>}
      <details className="report-advanced-evidence report-interactive"><summary>Advanced validation and trace evidence</summary><ValidationResult report={result.final_report} sandboxConfiguration={result.analysis_configuration} /><TraceabilityTab result={result} /></details>
      <section className="result-section report-interactive"><h2>Finding workflow controls</h2><FindingsTable findings={result.findings ?? []} onSelect={setSelectedFinding} /></section>
      {repairedFiles.length > 0 && <div className="report-interactive"><RepairActions originalFiles={availableFiles} repairedFiles={repairedFiles} result={result} /></div>}
    </section>
  );
}
