import { Fragment, useEffect, useState } from "react";
import { getFindingStatusHistory, updateFindingStatus, type FindingStatusHistoryEntry } from "../api/findings";
import { usePermission } from "../auth/permissions";
import type { Finding, FindingStatus, Severity } from "../types/finding";
import { isComplexityFinding, isErrorFinding, isStyleFinding, isWarningFinding, safeEvidence } from "../analysis/model";

interface FindingsTableProps {
  findings: Finding[];
  onSelect?: (finding: Finding) => void;
}

const SEVERITY_ORDER: Record<Severity, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const SEVERITY_FILTERS: Array<"all" | Severity> = ["all", "critical", "high", "medium", "low", "info"];
const CATEGORY_FILTERS = ["all", "code_quality", "security", "bug", "resource", "errors", "warnings", "style", "complexity"] as const;
const CATEGORY_LABELS: Record<(typeof CATEGORY_FILTERS)[number], string> = {
  all: "All areas", code_quality: "Code quality", security: "Security", bug: "Bugs", resource: "Resources",
  errors: "Errors", warnings: "Warnings", style: "Style", complexity: "Complexity",
};
type FindingSort = "severity" | "file" | "line";

export function FindingsTable({ findings, onSelect }: Readonly<FindingsTableProps>) {
  const [severity, setSeverity] = useState<"all" | Severity>("all");
  const [category, setCategory] = useState<(typeof CATEGORY_FILTERS)[number]>("all");
  const [tool, setTool] = useState("all");
  const [sort, setSort] = useState<FindingSort>("severity");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [currentFindings, setCurrentFindings] = useState(findings);
  const [selected, setSelected] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [statusHistory, setStatusHistory] = useState<FindingStatusHistoryEntry[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [statusError, setStatusError] = useState("");
  const [busy, setBusy] = useState(false);
  const canChangeStatus = usePermission("changeFindingStatus");
  const statusOptions: FindingStatus[] = ["Open", "In repair", "Needs human review", "False positive", "Won't fix"];
  useEffect(() => setCurrentFindings(findings), [findings]);
  useEffect(() => {
    if (!expanded) { setStatusHistory([]); setHistoryError(""); return; }
    const controller = new AbortController();
    void getFindingStatusHistory(expanded, controller.signal).then((items) => { setStatusHistory(items); setHistoryError(""); }).catch((error: unknown) => { if (!controller.signal.aborted) { setStatusHistory([]); setHistoryError(error instanceof Error ? error.message : "Status history is unavailable."); } });
    return () => controller.abort();
  }, [expanded]);
  const query = search.trim().toLowerCase();
  const sorted = [...currentFindings]
    .filter((finding) => severity === "all" || finding.severity === severity)
    .filter((finding) => categoryMatches(finding, category))
    .filter((finding) => tool === "all" || finding.tool === tool)
    .filter(
      (finding) =>
        !query ||
        finding.file.toLowerCase().includes(query) ||
        finding.issue.toLowerCase().includes(query) ||
        finding.description.toLowerCase().includes(query),
    )
    .sort((a, b) => compareFindings(a, b, sort));

  async function changeStatuses(ids: string[], status: FindingStatus) {
    if (status === "Fixed (validated)") return;
    setBusy(true);
    setStatusError("");
    try {
      const results = await Promise.all(ids.map((id) => updateFindingStatus(id, status, reason.trim() || undefined)));
      setCurrentFindings((items) => items.map((item) => ids.includes(item.finding_id) ? { ...item, status } : item));
      if (expanded && ids.includes(expanded)) {
        setStatusHistory((items) => [...items, ...results]);
        setHistoryError("");
      }
      setSelected([]);
      setReason("");
    } catch (error) {
      setStatusError(error instanceof Error ? error.message : "Finding status could not be updated.");
    } finally { setBusy(false); }
  }

  return (
    <div className="findings-table__container">
      <div className="findings-table__controls">
        <div className="findings-table__filter-groups">
        <fieldset className="findings-table__filters">
          <legend className="visually-hidden">Filter by severity</legend>
          {SEVERITY_FILTERS.map((filter) => (
            <button
              key={filter}
              className={`btn ${severity === filter ? "btn--primary" : ""}`}
              onClick={() => setSeverity(filter)}
            >
              {filter[0].toUpperCase() + filter.slice(1)}
            </button>
          ))}
        </fieldset>
        <fieldset className="findings-table__filters">
          <legend className="visually-hidden">Filter by category</legend>
          {CATEGORY_FILTERS.map((filter) => <button key={filter} className={`btn ${category === filter ? "btn--primary" : ""}`} onClick={() => setCategory(filter)}>{CATEGORY_LABELS[filter]}</button>)}
        </fieldset>
        <select className="findings-table__tool-select" value={tool} onChange={(event) => setTool(event.target.value)} aria-label="Filter by tool"><option value="all">All tools</option><option value="ruff">Ruff</option><option value="pylint">Pylint</option><option value="radon">Radon</option></select>
        <select className="findings-table__tool-select" value={sort} onChange={(event) => setSort(event.target.value as FindingSort)} aria-label="Sort findings"><option value="severity">Sort: severity</option><option value="file">Sort: file</option><option value="line">Sort: line</option></select>
        </div>
        <input
          type="search"
          value={search}
          placeholder="Search files or issues"
          aria-label="Search files or issues"
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {sorted.length === 0 ? (
        <p className="findings-table__empty">No findings match these filters.</p>
      ) : (
        <>
        <BulkStatusControls visible={canChangeStatus} selectedCount={selected.length} disabled={busy} options={statusOptions} reason={reason} onReasonChange={setReason} onStatusChange={(status) => void changeStatuses(selected, status)} />
        {statusError && <p role="alert" className="inline-error">{statusError}</p>}
        <table className="findings-table">
          <thead>
            <tr>
              {canChangeStatus && <th><span className="visually-hidden">Select</span></th>}
              <th>Severity</th>
              <th>File</th>
              <th>Line</th>
              <th>Issue</th>
              <th>Category</th>
              <th>Tool</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((finding) => (
              <Fragment key={finding.finding_id}>
                <tr className={`findings-table__row findings-table__row--${finding.severity}`}>
                  {canChangeStatus && <td><input type="checkbox" aria-label={`Select ${finding.issue}`} checked={selected.includes(finding.finding_id)} onChange={(event) => setSelected((items) => event.target.checked ? [...items, finding.finding_id] : items.filter((id) => id !== finding.finding_id))} /></td>}
                  <td>{finding.severity}</td>
                  <td>{safeEvidence(finding.file)}</td>
                  <td>{finding.line_number ?? "-"}</td>
                  <td><button className="btn btn--link" aria-expanded={expanded === finding.finding_id} onClick={() => { setExpanded(expanded === finding.finding_id ? null : finding.finding_id); onSelect?.(finding); }}>{safeEvidence(finding.issue)}</button></td>
                  <td>{finding.subcategory ?? finding.category}</td>
                  <td>{safeEvidence(finding.tool ?? finding.evidence?.match(/^\[([^\]]+)/)?.[1] ?? "-")}</td>
                  <td><span className="status-pill status-pill--unknown">{finding.status}</span></td>
                </tr>
                {expanded === finding.finding_id && (
                  <tr key={`${finding.finding_id}-details`} className="findings-table__details">
                    <td colSpan={canChangeStatus ? 8 : 7}>
                      <strong>Description:</strong> {safeEvidence(finding.description)}
                      {finding.recommendation && (
                        <p>
                          <strong>Recommendation:</strong> {safeEvidence(finding.recommendation)}
                        </p>
                      )}
                      {finding.evidence && <p><strong>Evidence:</strong> {safeEvidence(finding.evidence)}</p>}
                      {canChangeStatus && <FindingStatusControl finding={finding} disabled={busy} reason={reason} options={statusOptions} onReasonChange={setReason} onStatusChange={(status) => void changeStatuses([finding.finding_id], status)} />}
                      <section className="finding-status-history"><strong>Status history</strong><StatusHistoryContent entries={statusHistory} error={historyError} /></section>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        </>
      )}
    </div>
  );

}

function categoryMatches(finding: Finding, category: (typeof CATEGORY_FILTERS)[number]): boolean {
  if (category === "all") return true;
  if (category === "errors") return isErrorFinding(finding);
  if (category === "warnings") return isWarningFinding(finding);
  if (category === "style") return isStyleFinding(finding);
  if (category === "complexity") return isComplexityFinding(finding);
  return finding.category === category;
}

function compareFindings(first: Finding, second: Finding, sort: FindingSort): number {
  if (sort === "file") return first.file.localeCompare(second.file) || SEVERITY_ORDER[first.severity] - SEVERITY_ORDER[second.severity];
  if (sort === "line") return (first.line_number ?? Number.MAX_SAFE_INTEGER) - (second.line_number ?? Number.MAX_SAFE_INTEGER);
  return SEVERITY_ORDER[first.severity] - SEVERITY_ORDER[second.severity];
}

interface StatusHistoryContentProps {
  readonly entries: FindingStatusHistoryEntry[];
  readonly error: string;
}

function StatusHistoryContent({ entries, error }: Readonly<StatusHistoryContentProps>) {
  if (error) return <output>{error}</output>;
  if (entries.length === 0) return <p>No status changes recorded.</p>;
  return <ul>{entries.map((entry, index) => <li key={`${entry.changed_at}-${index}`}>{entry.status} · {safeEvidence(entry.changed_by)} · {new Date(entry.changed_at).toLocaleString()}{entry.comment ? ` · ${safeEvidence(entry.comment)}` : ""}</li>)}</ul>;
}

interface FindingStatusControlProps {
  readonly finding: Finding;
  readonly disabled: boolean;
  readonly reason: string;
  readonly options: FindingStatus[];
  readonly onReasonChange: (value: string) => void;
  readonly onStatusChange: (status: FindingStatus) => void;
}

function FindingStatusControl({ finding, disabled, reason, options, onReasonChange, onStatusChange }: Readonly<FindingStatusControlProps>) {
  if (finding.status === "Fixed (validated)" || finding.status === "repaired") return <output>Fixed status is controlled by a passing validation.</output>;
  return <fieldset className="finding-status-editor"><legend>Finding status</legend><label>Update status<select value={options.includes(finding.status as FindingStatus) ? finding.status : "Open"} disabled={disabled} onChange={(event) => onStatusChange(event.target.value as FindingStatus)}>{options.map((status) => <option key={status}>{status}</option>)}</select></label><label>Optional reason<input value={reason} onChange={(event) => onReasonChange(event.target.value)} /></label><span>Fixed (validated) is assigned only by a passing validation.</span></fieldset>;
}

interface BulkStatusControlsProps {
  readonly visible: boolean;
  readonly selectedCount: number;
  readonly disabled: boolean;
  readonly options: FindingStatus[];
  readonly reason: string;
  readonly onReasonChange: (value: string) => void;
  readonly onStatusChange: (status: FindingStatus) => void;
}

function BulkStatusControls({ visible, selectedCount, disabled, options, reason, onReasonChange, onStatusChange }: Readonly<BulkStatusControlsProps>) {
  if (!visible) {
    return null;
  }
  function handleStatusChange(event: React.ChangeEvent<HTMLSelectElement>) {
    const select = event.currentTarget;
    onStatusChange(select.value as FindingStatus);
    select.value = "";
  }
  return <div className="finding-bulk-controls"><label>Status for selected<select id="bulk-finding-status" defaultValue="" disabled={selectedCount === 0 || disabled} onChange={handleStatusChange}><option value="" disabled>Choose status</option>{options.map((status) => <option key={status}>{status}</option>)}</select></label><label>Optional reason<input value={reason} onChange={(event) => onReasonChange(event.target.value)} /></label><span>{selectedCount} selected</span></div>;
}
