import type { PersistedHistoryItem } from "../api/types";
import { RequirePermission } from "../components/RequirePermission";

interface RepairHistoryPageProps {
  history: PersistedHistoryItem[];
  loading: boolean;
  error: string;
  onRetry: () => void;
  onOpen: (runId: string) => void;
  onNewAnalysis: () => void;
}

export function RepairHistoryPage({ history, loading, error, onRetry, onOpen, onNewAnalysis }: Readonly<RepairHistoryPageProps>) {
  return <section className="page review-page"><div className="kicker">Analysis history <span>Database records</span></div><h1>Every run,<br /><em>traceable.</em></h1><p className="review-page__intro">Persisted runs for projects you own.</p>{error && <div className="inline-error" role="alert"><p>{error}</p><button className="btn" onClick={onRetry}>Retry loading history</button></div>}{loading ? <p aria-live="polite">Loading analysis history…</p> : history.length === 0 ? <div className="empty-state"><strong>No analysis runs yet.</strong><span>Start an analysis to create a persisted history record.</span><RequirePermission capability="createAnalysis"><button className="btn btn--primary" onClick={onNewAnalysis}>Start new analysis →</button></RequirePermission></div> : <div className="history-table">{history.map((entry) => <button className="history-row" key={entry.run_id} onClick={() => onOpen(entry.run_id)}><span className="history-row__id">↗</span><span><strong>{entry.project_name}</strong><small>{entry.run_id} · {entry.started_at ? new Date(entry.started_at).toLocaleString() : "Start time unavailable"}</small></span><span className="history-row__issues">{entry.finding_count} findings</span><b className={`status-pill status-pill--${entry.validation_status}`}>{entry.validation_status}</b></button>)}</div>}</section>;
}