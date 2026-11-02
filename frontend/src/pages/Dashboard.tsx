import { useEffect, useMemo, useState } from "react";
import {
  getOverview,
  getOverviewAgentHealth,
  type OverviewAgentHealth,
  type OverviewLanguage,
  type OverviewResponse,
  type OverviewRun,
} from "../api/overview";

interface DashboardPageProps {
  onStart: () => void;
  onOpenResults: (runId: string) => void;
  onOpenHistory: () => void;
  onOpenProjects: () => void;
  canStartAnalysis: boolean;
}

const AGENTS = [
  { key: "orchestrator", name: "Orchestrator", icon: "↗" },
  { key: "code_quality", name: "Code quality", icon: "◈" },
  { key: "security", name: "Security", icon: "◇" },
  { key: "bug_resource", name: "Bug & resource", icon: "⌁" },
  { key: "repair", name: "Repair", icon: "✦" },
  { key: "validation", name: "Validation", icon: "✓" },
  { key: "sandbox", name: "Sandbox", icon: "▣" },
  { key: "report_recommendation", name: "Report", icon: "▤" },
] as const;

const SEVERITIES = [
  { key: "critical", label: "Critical", color: "var(--sev-critical)" },
  { key: "high", label: "High", color: "var(--sev-high)" },
  { key: "medium", label: "Medium", color: "var(--sev-medium)" },
  { key: "low", label: "Low", color: "var(--sev-low)" },
  { key: "info", label: "Info", color: "var(--sev-info)" },
] as const;

function percentage(numerator: number, denominator: number): string {
  return denominator > 0 ? `${Math.round((numerator / denominator) * 100)}%` : "—";
}

function dateLabel(value: string | null): string {
  if (!value) return "Time unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString();
}

function agentStatus(health: OverviewAgentHealth | null, service: string): string {
  const status = health?.services[service];
  if (!status) return health ? "Unknown" : "Loading";
  if (status.healthy) return "Online";
  if (status.status === "not_configured") return "Not configured";
  return "Unavailable";
}

function statusClass(value: string): string {
  return value.toLowerCase().replace(/[_ ]/g, "-");
}

function SeverityChart({ summary }: Readonly<{ summary: Record<string, number> }>) {
  const total = SEVERITIES.reduce((sum, item) => sum + (summary[item.key] ?? 0), 0);
  let offset = 0;
  const segments = SEVERITIES.map((item) => {
    const count = summary[item.key] ?? 0;
    const start = offset;
    offset += total > 0 ? (count / total) * 100 : 0;
    return `${item.color} ${start}% ${offset}%`;
  });
  const chartStyle = {
    background: total
      ? `conic-gradient(${segments.join(", ")})`
      : "conic-gradient(var(--border) 0% 100%)",
  };

  return (
    <div className="overview-severity">
      <div className="overview-severity__chart" style={chartStyle} aria-label={`${total} findings by severity`}>
        <div><strong>{total}</strong><span>latest run</span></div>
      </div>
      <ul className="overview-severity__legend">
        {SEVERITIES.map((item) => (
          <li key={item.key}>
            <span><i style={{ background: item.color }} />{item.label}</span>
            <strong>{summary[item.key] ?? 0}</strong>
          </li>
        ))}
      </ul>
    </div>
  );
}

function LanguageChart({
  languages,
  findings,
}: Readonly<{ languages: OverviewLanguage[]; findings: Record<string, number> }>) {
  const usedLanguages = languages.filter((language) => (findings[language.key] ?? 0) > 0);
  const max = Math.max(1, ...usedLanguages.map((language) => findings[language.key] ?? 0));
  if (!usedLanguages.length) {
    return <p className="overview-empty-note">No findings were recorded for the latest run.</p>;
  }

  return (
    <div className="overview-language-chart">
      {usedLanguages.map((language) => {
        const count = findings[language.key] ?? 0;
        return (
          <div className="overview-language-chart__item" key={language.key} title={`${language.label}: ${count}`}>
            <strong>{count}</strong>
            <span className="overview-language-chart__track">
              <i style={{ height: `${Math.max(8, (count / max) * 100)}%` }} />
            </span>
            <small>{language.label}</small>
          </div>
        );
      })}
    </div>
  );
}

function RecentRuns({
  runs,
  onOpenResults,
}: Readonly<{ runs: OverviewRun[]; onOpenResults: (runId: string) => void }>) {
  if (!runs.length) {
    return (
      <div className="overview-empty">
        <strong>No analyses yet</strong>
        <span>Once a project is analyzed, its backend-recorded run and validation status will appear here.</span>
      </div>
    );
  }

  return (
    <div className="overview-run-list">
      {runs.map((run) => (
        <button className="overview-run" key={run.run_id} onClick={() => onOpenResults(run.run_id)}>
          <span className="overview-run__icon" aria-hidden="true">⌘</span>
          <span className="overview-run__project">
            <strong>{run.project_name}</strong>
            <small>{run.finding_count} findings · {run.retry_count} retries · validation {run.validation_status}</small>
          </span>
          <span className="overview-run__language">{run.language ?? "Language unknown"}</span>
          <span className={`overview-status overview-status--${statusClass(run.status)}`}>{run.status}</span>
          <small className="overview-run__date">{dateLabel(run.started_at)}</small>
          <span className="overview-run__arrow" aria-hidden="true">›</span>
        </button>
      ))}
    </div>
  );
}

export function DashboardPage({
  onStart,
  onOpenResults,
  onOpenHistory,
  onOpenProjects,
  canStartAnalysis,
}: Readonly<DashboardPageProps>) {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [health, setHealth] = useState<OverviewAgentHealth | null>(null);
  const [overviewError, setOverviewError] = useState("");
  const [healthError, setHealthError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setOverviewError("");
    setHealthError("");
    void getOverview(controller.signal)
      .then(setOverview)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setOverviewError(error instanceof Error ? error.message : "Workspace metrics could not be loaded.");
        }
      });
    void getOverviewAgentHealth(controller.signal)
      .then(setHealth)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setHealthError(error instanceof Error ? error.message : "Service health could not be loaded.");
        }
      });
    return () => controller.abort();
  }, [refreshKey]);

  const connectedAgents = useMemo(
    () => Object.values(health?.services ?? {}).filter((service) => service.healthy).length,
    [health],
  );
  const metrics = overview?.metrics;

  return (
    <section className="page dashboard-page overview-page">
      <div className="overview-hero">
        <div>
          <div className="kicker">Workspace overview <span>Live backend data</span></div>
          <h1>Ship code<br /><em>with evidence.</em></h1>
          <p>One controlled, multi-language workflow for understanding projects, reviewing risk, and validating every repair candidate.</p>
        </div>
        {canStartAnalysis && (
          <button className="btn btn--primary overview-hero__cta" onClick={onStart}>
            <span aria-hidden="true">＋</span> New analysis
          </button>
        )}
      </div>

      {overviewError && (
        <div className="overview-error" role="alert">
          <span>{overviewError}</span>
          <button className="btn btn--secondary" onClick={() => setRefreshKey((key) => key + 1)}>Retry</button>
        </div>
      )}

      <div className="overview-metrics">
        <button className="overview-metric overview-metric--violet" onClick={onOpenProjects}>
          <span className="overview-metric__top"><i>▣</i><small>{metrics ? `+${metrics.runs_today} today` : "Backend"}</small></span>
          <span className="overview-metric__label">Projects</span>
          <strong>{metrics ? metrics.project_count : "—"}</strong>
          <small>{metrics ? `${metrics.run_count} analyses recorded` : "Loading project totals"}</small>
          <b>View projects <span aria-hidden="true">→</span></b>
        </button>
        <div className="overview-metric overview-metric--red">
          <span className="overview-metric__top"><i>◇</i><small>Latest analysis</small></span>
          <span className="overview-metric__label">Security findings</span>
          <strong>{metrics ? metrics.latest_security_findings : "—"}</strong>
          <small>{metrics ? `${metrics.latest_high_security_findings} high or critical` : "Based on latest run"}</small>
          <button onClick={onOpenHistory}>View analysis history <span aria-hidden="true">→</span></button>
        </div>
        <div className="overview-metric overview-metric--green">
          <span className="overview-metric__top"><i>↻</i><small>All recorded runs</small></span>
          <span className="overview-metric__label">Repair success rate</span>
          <strong>{metrics ? percentage(metrics.repair_successes, metrics.repair_attempted) : "—"}</strong>
          <small>{metrics ? `${metrics.repair_successes} / ${metrics.repair_attempted} repair attempts succeeded` : "Awaiting backend data"}</small>
          <button onClick={onOpenHistory}>View repair history <span aria-hidden="true">→</span></button>
        </div>
        <div className="overview-metric overview-metric--blue">
          <span className="overview-metric__top"><i>✓</i><small>All recorded runs</small></span>
          <span className="overview-metric__label">Validation pass rate</span>
          <strong>{metrics ? percentage(metrics.validation_passed, metrics.validation_counted) : "—"}</strong>
          <small>{metrics ? `${metrics.validation_passed} / ${metrics.validation_counted} validations passed` : "Awaiting backend data"}</small>
          <button onClick={onOpenHistory}>View validation history <span aria-hidden="true">→</span></button>
        </div>
      </div>

      <div className="overview-content-grid">
        <section className="dashboard-panel overview-panel overview-panel--runs">
          <div className="overview-panel__heading">
            <div><span className="kicker">Workspace activity</span><h2>Latest analyses</h2></div>
            <button className="btn btn--link" onClick={onOpenHistory}>View all <span aria-hidden="true">→</span></button>
          </div>
          {overview ? (
            <RecentRuns runs={overview.recent_runs} onOpenResults={onOpenResults} />
          ) : !overviewError ? <p className="overview-loading">Loading recent analyses…</p> : null}
        </section>

        <section className="dashboard-panel overview-panel">
          <div className="overview-panel__heading">
            <div><span className="kicker">Latest run · severity</span><h2>Findings by severity</h2></div>
            <button className="btn btn--link" onClick={onOpenHistory}>Details <span aria-hidden="true">→</span></button>
          </div>
          {overview ? <SeverityChart summary={overview.latest_findings_by_severity} /> : <p className="overview-loading">Loading findings…</p>}
        </section>

        <section className="dashboard-panel overview-panel">
          <div className="overview-panel__heading">
            <div><span className="kicker">Latest run · source language</span><h2>Findings by language</h2></div>
            <button className="btn btn--link" onClick={onOpenHistory}>Details <span aria-hidden="true">→</span></button>
          </div>
          {overview ? <LanguageChart languages={overview.languages} findings={overview.latest_findings_by_language} /> : <p className="overview-loading">Loading language data…</p>}
        </section>

        <section className="dashboard-panel overview-panel overview-panel--languages">
          <div className="overview-panel__heading">
            <div><span className="kicker">Backend language registry</span><h2>Language support</h2></div>
            <span className="overview-panel__count">{overview ? `${overview.languages.length} registered` : "Loading"}</span>
          </div>
          {overview && overview.languages.length > 0 ? (
            <div className="overview-language-grid">
              {overview.languages.map((language) => (
                <article className="overview-language" key={language.key}>
                  <span className="overview-language__icon" aria-hidden="true">{language.label.slice(0, 2)}</span>
                  <strong>{language.label}</strong>
                  <small>{language.project_count} {language.project_count === 1 ? "project" : "projects"}</small>
                </article>
              ))}
            </div>
          ) : overview ? <p className="overview-empty-note">No language adapters are currently registered.</p> : <p className="overview-loading">Loading registered languages…</p>}
        </section>

        <section className="dashboard-panel overview-panel overview-panel--agents">
          <div className="overview-panel__heading">
            <div><span className="kicker">Live health checks</span><h2>Agent operating system</h2></div>
            <span className={`overview-health-summary${healthError ? " overview-health-summary--error" : ""}`}>
              {healthError ? "Health unavailable" : health ? `${connectedAgents}/${health.configured} online` : "Checking"}
            </span>
          </div>
          {healthError && <p className="overview-empty-note" role="status">{healthError}</p>}
          <div className="overview-agent-grid">
            {AGENTS.map((agent) => {
              const status = agentStatus(health, agent.key);
              return (
                <div className="overview-agent" key={agent.key}>
                  <span className="overview-agent__icon" aria-hidden="true">{agent.icon}</span>
                  <span><strong>{agent.name}</strong><small className={`overview-agent__status overview-agent__status--${statusClass(status)}`}>{status}</small></span>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </section>
  );
}
