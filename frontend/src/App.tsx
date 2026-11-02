import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Outlet, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AgentStatusPanel } from "./components/AgentStatusPanel";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { RequireAuth } from "./components/RequireAuth";
import { ToolStatusPanel } from "./components/ToolStatusPanel";
import type { ProgressEvent, ProjectFile, QualityTool, RunHistoryEntry, SandboxTestCase, WorkflowRunResponse } from "./types/finding";
import { api } from "./services/api";
import { AgentCenter } from "./pages/AgentCenter";
import { AnalysisPage } from "./pages/Analysis";
import { DashboardPage } from "./pages/Dashboard";
import { FindingsReviewPage } from "./pages/FindingsReview";
import { LoginRoute } from "./pages/LoginRoute";
import { RepairHistoryPage } from "./pages/RepairHistory";
import { ResultsPage } from "./pages/Results";
import { UploadPage } from "./pages/Upload";
import { ProjectDetailPage, ProjectsPage } from "./pages/Projects";
import { usePermission } from "./auth/permissions";
import { AdminUsersPage } from "./pages/Admin";
import { AnalysisTabs } from "./analysis/AnalysisTabs";
import { createProject, getProject } from "./api/projects";
import { getProjectFiles } from "./api/files";
import type { PersistedHistoryItem, ProjectDetail, ProjectFile as ApiProjectFile, Report } from "./api/types";
import { DEFAULT_ANALYSIS_CHECKS, type AnalysisCheckOptions } from "./api/configuration";
import { AdminSettingsPage } from "./pages/AdminSettings";
import { AccountPasswordPage } from "./pages/AccountPassword";
import { useAuth } from "./context/AuthContextStore";
import { mocksEnabled } from "./api/client";
import { getAnalysisHistory, getReport } from "./api/reports";
import "./App.css";

type StartHandler = (files: ProjectFile[], tools: QualityTool[], projectName?: string, existingProjectId?: string, checks?: AnalysisCheckOptions, sandboxStdin?: string, language?: string, projectType?: "dotnet" | "single_file", testCases?: SandboxTestCase[]) => Promise<void>;

interface WorkspaceState {
  readonly history: RunHistoryEntry[];
  readonly result: WorkflowRunResponse | null;
  readonly originalFiles: ProjectFile[];
  readonly activeStepIndex: number;
  readonly progressMessage?: string;
  readonly workflowStep: string;
  readonly workflowStatus: string;
  readonly retryCount: number;
  readonly runInProgress: boolean;
  readonly activeTools: QualityTool[];
  readonly activeChecks: AnalysisCheckOptions;
  readonly errorMessage: string | null;
  readonly handleStart: StartHandler;
  readonly openHistory: (entry: RunHistoryEntry) => void;
  readonly rerun: () => void;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);
const EMPTY_WORKSPACE: WorkspaceState = {
  history: [], result: null, originalFiles: [], activeStepIndex: 0, workflowStep: "", workflowStatus: "idle", retryCount: 0, runInProgress: false, activeTools: [], activeChecks: DEFAULT_ANALYSIS_CHECKS, errorMessage: null,
  handleStart: () => Promise.resolve(), openHistory: () => undefined, rerun: () => undefined,
};

function useWorkspace(): WorkspaceState {
  return useContext(WorkspaceContext) ?? EMPTY_WORKSPACE;
}

export default function App() {
  const navigate = useNavigate();
  const [originalFiles, setOriginalFiles] = useState<ProjectFile[]>([]);
  const [result, setResult] = useState<WorkflowRunResponse | null>(null);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [activeStepIndex, setActiveStepIndex] = useState(0);
  const [progressMessage, setProgressMessage] = useState<string>();
  const [workflowStep, setWorkflowStep] = useState("");
  const [workflowStatus, setWorkflowStatus] = useState("idle");
  const [retryCount, setRetryCount] = useState(0);
  const [runInProgress, setRunInProgress] = useState(false);
  const [activeTools, setActiveTools] = useState<QualityTool[]>([]);
  const [activeChecks, setActiveChecks] = useState<AnalysisCheckOptions>(DEFAULT_ANALYSIS_CHECKS);
  const requestInFlight = useRef(false);
  const lastRunOptions = useRef<{ readonly tools: QualityTool[]; readonly projectName: string; readonly checks: AnalysisCheckOptions; readonly sandboxStdin: string; readonly language?: string; readonly projectType: "dotnet" | "single_file"; readonly testCases: SandboxTestCase[] } | null>(null);
  const [history, setHistory] = useState<RunHistoryEntry[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem("coding-validation-run-history") ?? "[]") as RunHistoryEntry[];
    } catch {
      return [];
    }
  });

  useEffect(() => {
    sessionStorage.setItem("coding-validation-run-history", JSON.stringify(history));
  }, [history]);

  useEffect(() => {
    const clearWorkspace = () => {
      sessionStorage.removeItem("coding-validation-run-history");
      localStorage.removeItem("coding-validation-run-history");
      setHistory([]);
      setOriginalFiles([]);
      setResult(null);
      setActiveProjectId(null);
      setErrorMessage(null);
      setActiveStepIndex(0);
      setProgressMessage(undefined);
      setWorkflowStep("");
      setWorkflowStatus("idle");
      setRetryCount(0);
      setRunInProgress(false);
      setActiveTools([]);
      setActiveChecks(DEFAULT_ANALYSIS_CHECKS);
    };
    const expireSession = (event: Event) => {
      const detail = (event as CustomEvent<{ from?: string }>).detail;
      void navigate("/login", { replace: true, state: { from: detail?.from ?? `${window.location.pathname}${window.location.search}${window.location.hash}`, expired: true } });
    };
    window.addEventListener("coding-validation:clear-user-data", clearWorkspace);
    window.addEventListener("coding-validation:session-expired", expireSession);
    return () => {
      window.removeEventListener("coding-validation:clear-user-data", clearWorkspace);
      window.removeEventListener("coding-validation:session-expired", expireSession);
    };
  }, [navigate]);

  const handleStart = useCallback(async (files: ProjectFile[], tools: QualityTool[], projectName = "Python project", existingProjectId?: string, checks = DEFAULT_ANALYSIS_CHECKS, sandboxStdin = "", language?: string, projectType: "dotnet" | "single_file" = "single_file", testCases: SandboxTestCase[] = []): Promise<void> => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    lastRunOptions.current = { tools, projectName, checks, sandboxStdin, language, projectType, testCases };
    setRunInProgress(true);
    setActiveTools(tools);
    setActiveChecks(checks);
    setOriginalFiles(files);
    setResult(null);
    setErrorMessage(null);
    setActiveStepIndex(0);
    setWorkflowStep("queued");
    setWorkflowStatus("submitted");
    setRetryCount(0);
    setProgressMessage("Starting analysis");
    try {
      const analysisChecks = {
        code_quality: checks.codeQuality,
        security: checks.security,
        bug_resource: checks.bugResource,
        complexity: checks.complexity,
        automated_repair: checks.automatedRepair,
        final_validation: checks.finalValidation,
        ai_report_recommendations: checks.aiReportRecommendations,
      };
      const projectContext = { analysis_checks: analysisChecks, sandbox_stdin: sandboxStdin, test_cases: testCases, ...(language ? { language } : {}), project_type: projectType };
      const projectId = existingProjectId ?? activeProjectId ?? (await createProject({ name: projectName, files, project_context: projectContext })).project_id;
      setActiveProjectId(projectId);
      void navigate(`/analysis/${encodeURIComponent(projectId)}`);
      const response = await api.startAnalysis(
        { project_id: projectId, files, tools, language, project_type: projectType, project_context: projectContext, test_cases: testCases },
        (progress: ProgressEvent) => {
          setWorkflowStep(progress.step);
          if (progress.status) setWorkflowStatus(progress.status);
          if (typeof progress.retry_count === "number") setRetryCount(progress.retry_count);
          const stepIndexes: Record<string, number> = {
            upload: 0, understand: 0, analyze: 1, aggregate: 3, explain_prioritize: 4,
            report_recommend: 5, repair: 6, validate: 7, reanalyze: 8, compare: 9, finalize: 10,
          };
          setActiveStepIndex(stepIndexes[progress.step] ?? 0);
          setProgressMessage(progress.message);
        },
      );
      const completedRun = { ...response, project_name: projectName };
      setResult(completedRun);
      setActiveProjectId(response.project_id);
      if (typeof response.retry_count === "number") setRetryCount(response.retry_count);
      setWorkflowStatus("completed");
      setWorkflowStep("finalize");
      setActiveStepIndex(11);
      setHistory((entries) => [
        { project_id: response.project_id, timestamp: new Date().toISOString(), validation_status: response.validation_status, result: completedRun, originalFiles: files },
        ...entries.filter((entry) => entry.project_id !== response.project_id),
      ]);
      void navigate(`/analysis/${encodeURIComponent(response.project_id)}/results`);
    } catch (error) {
      setWorkflowStatus("failed");
      setErrorMessage(error instanceof Error ? error.message : "The analysis could not be completed.");
    } finally {
      requestInFlight.current = false;
      setRunInProgress(false);
    }
  }, [activeProjectId, navigate]);

  const openHistory = useCallback((entry: RunHistoryEntry): void => {
    setOriginalFiles(entry.originalFiles);
    setResult(entry.result);
    setActiveProjectId(entry.project_id);
    setErrorMessage(null);
    void navigate(`/analysis/${encodeURIComponent(entry.project_id)}/results`);
  }, [navigate]);

  const rerun = useCallback((): void => {
    if (originalFiles.length === 0) return;
    const previous = lastRunOptions.current;
    if (previous) void handleStart(originalFiles, previous.tools, previous.projectName, activeProjectId ?? undefined, previous.checks, previous.sandboxStdin, previous.language, previous.projectType, previous.testCases);
    else void handleStart(originalFiles, ["ruff", "pylint", "radon", "mypy"], "Python project", activeProjectId ?? undefined);
  }, [activeProjectId, handleStart, originalFiles]);

  const value = useMemo<WorkspaceState>(
    () => ({ history, result, originalFiles, activeStepIndex, progressMessage, workflowStep, workflowStatus, retryCount, runInProgress, activeTools, activeChecks, errorMessage, handleStart, openHistory, rerun }),
    [history, result, originalFiles, activeStepIndex, progressMessage, workflowStep, workflowStatus, retryCount, runInProgress, activeTools, activeChecks, errorMessage, handleStart, openHistory, rerun],
  );

  return (
    <ErrorBoundary>
      <WorkspaceContext.Provider value={value}>
        <Routes>
          <Route path="/login" element={<LoginRoute />} />
          <Route element={<RequireAuth><WorkspaceLayout /></RequireAuth>}>
            <Route index element={<DashboardRoute />} />
            <Route path="projects" element={<ProjectsPage />} />
            <Route path="projects/:id" element={<ProjectDetailPage />} />
            <Route path="projects/new" element={<RequireAuth capability="upload"><UploadRoute /></RequireAuth>} />
            <Route path="analysis/:id" element={<AnalysisRoute />} />
            <Route path="analysis/:id/findings" element={<FindingsRoute />} />
            <Route path="analysis/:id/results" element={<ResultsRoute />} />
            <Route path="reports/:id" element={<ResultsRoute />} />
            <Route path="analysis/:id/repair" element={<UnavailablePage title="Repair workspace" />} />
            <Route path="analysis/:id/validation" element={<UnavailablePage title="Validation details" />} />
            <Route path="history" element={<HistoryRoute />} />
            <Route path="account/password" element={<AccountPasswordPage />} />
            <Route path="agents" element={<AgentCenter />} />
            <Route path="admin" element={<RequireAuth capability="adminAccess"><AdminUsersPage /></RequireAuth>} />
            <Route path="admin/settings" element={<RequireAuth capability="adminAccess"><AdminSettingsPage /></RequireAuth>} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </WorkspaceContext.Provider>
    </ErrorBoundary>
  );
}

function WorkspaceLayout() {
  const location = useLocation();
  const { user, logout } = useAuth();
  const canManageUsers = usePermission("adminAccess");
  const canStartAnalysis = usePermission("createAnalysis");
  const isProjectsActive = location.pathname === "/projects"
    || (location.pathname.startsWith("/projects/") && !location.pathname.startsWith("/projects/new"));

  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/" aria-label="Return to dashboard">
          <span className="brand-mark">CV</span>
          <span><strong>Code validation</strong><small>autonomous repair workspace</small></span>
        </Link>
        <div className="topbar__actions">
          {mocksEnabled() && <span className="mock-mode-badge">MOCK MODE</span>}
          <span className="topbar__status"><span className="status-dot" />{user?.email}</span>
          <Link className="btn btn--ghost" to="/account/password">Account</Link>
          <button className="btn btn--ghost" onClick={logout}>Log out</button>
        </div>
      </header>
      <div className="workspace">
        <aside className="sidebar" aria-label="Workspace navigation">
          <div className="sidebar__eyebrow">Workspace</div>
          <NavLink className={({ isActive }) => `sidebar__item${isActive ? " sidebar__item--active" : ""}`} to="/" end><span>00</span> Overview</NavLink>
          <Link className={`sidebar__item${isProjectsActive ? " sidebar__item--active" : ""}`} aria-current={isProjectsActive ? "page" : undefined} to="/projects"><span>00</span> Projects</Link>
          {canStartAnalysis && <NavLink className={({ isActive }) => `sidebar__item${isActive ? " sidebar__item--active" : ""}`} to="/projects/new"><span>01</span> New analysis</NavLink>}
          <NavLink className={({ isActive }) => `sidebar__item${isActive ? " sidebar__item--active" : ""}`} to="/history"><span>02</span> History</NavLink>
          <NavLink className={({ isActive }) => `sidebar__item${isActive ? " sidebar__item--active" : ""}`} to="/agents"><span>03</span> Agent control room</NavLink>
          {canManageUsers && <NavLink className={({ isActive }) => `sidebar__item${isActive ? " sidebar__item--active" : ""}`} to="/admin"><span>04</span> Administration</NavLink>}
          <hr className="sidebar__rule" />
          <div className="sidebar__eyebrow">Pipeline</div>
          <div className="pipeline-mini"><i /><span><strong>Understand</strong><small>Project structure &amp; code analysis</small></span></div>
          <div className="pipeline-mini"><i /><span><strong>Detect issues</strong><small>Structure · quality · risk</small></span></div>
          <div className="pipeline-mini"><i /><span><strong>Repair &amp; validate</strong><small>Candidate → sandbox → evidence</small></span></div>
          <div className="pipeline-mini"><i /><span><strong>Report</strong><small>Findings · fixes · evidence</small></span></div>
        </aside>
        <main className="content"><Outlet /></main>
      </div>
      <nav className="mobile-nav" aria-label="Workspace navigation">
        <NavLink to="/" end>Overview</NavLink>
        <Link className={isProjectsActive ? "active" : undefined} aria-current={isProjectsActive ? "page" : undefined} to="/projects">Projects</Link>
        {canStartAnalysis && <NavLink to="/projects/new">New</NavLink>}
        <NavLink to="/history">History</NavLink>
        <NavLink to="/agents">Agents</NavLink>
        {canManageUsers && <NavLink to="/admin">Admin</NavLink>}
      </nav>
    </div>
  );
}

function DashboardRoute() {
  const navigate = useNavigate();
  const canStartAnalysis = usePermission("createAnalysis");
  return <DashboardPage onStart={() => navigate("/projects/new")} onOpenResults={(runId) => navigate(`/reports/${encodeURIComponent(runId)}`)} onOpenHistory={() => navigate("/history")} onOpenProjects={() => navigate("/projects")} canStartAnalysis={canStartAnalysis} />;
}

function UploadRoute() {
  const { history, handleStart, openHistory, runInProgress } = useWorkspace();
  const [query] = useSearchParams();
  const existingProjectId = query.get("project") ?? undefined;
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [files, setFiles] = useState<ApiProjectFile[]>([]);
  const [projectLoadError, setProjectLoadError] = useState("");

  useEffect(() => {
    if (!existingProjectId) return undefined;
    const controller = new AbortController();
    void Promise.all([getProject(existingProjectId, controller.signal), getProjectFiles(existingProjectId, controller.signal)])
      .then(([metadata, content]) => { setProject(metadata); setFiles(content.files); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setProjectLoadError(error instanceof Error ? error.message : "Project files could not be loaded."); });
    return () => controller.abort();
  }, [existingProjectId]);

  if (existingProjectId && !project && !projectLoadError) return <section className="page"><p>Loading project files...</p></section>;
  return <UploadPage onStart={(inputFiles, tools, projectName, projectId, checks, sandboxStdin, language, projectType, testCases) => handleStart(inputFiles, tools, projectName, projectId, checks, sandboxStdin, language, projectType, testCases)} history={history} onOpenHistory={openHistory} initialFiles={files} initialProjectName={project?.name} existingProjectId={existingProjectId} projectLoadError={projectLoadError} runInProgress={runInProgress} />;
}

function AnalysisRoute() {
  const { activeStepIndex, progressMessage, errorMessage, result, originalFiles, workflowStep, workflowStatus, retryCount, rerun, activeTools, activeChecks } = useWorkspace();
  return errorMessage
    ? <section className="page page--error"><h1>Analysis stopped</h1><p>{errorMessage}</p><p>Last reported repair retries: {retryCount}</p><details><summary>Details</summary><p>Check that the gateway and orchestrator are available, then retry.</p></details>{originalFiles.length > 0 && <button className="btn btn--primary" onClick={rerun}>Retry with these files</button>}<Link className="btn" to="/projects/new">Choose different files</Link></section>
    : <><AnalysisPage activeStepIndex={activeStepIndex} progressMessage={progressMessage} workflowStep={workflowStep} workflowStatus={workflowStatus} retryCount={retryCount} /><AgentStatusPanel progressMessage={progressMessage} workflowStep={workflowStep} workflowStatus={workflowStatus} result={result} analysisChecks={activeChecks} /><ToolStatusPanel tools={activeTools} result={result} running={workflowStatus === "working" || workflowStatus === "submitted"} /><AnalysisTabs result={result} files={originalFiles} /></>;
}

function FindingsRoute() {
  const { result, history } = useWorkspace();
  const navigate = useNavigate();
  const findings = result?.findings ?? history.flatMap((entry) => entry.result.findings);
  return <FindingsReviewPage findings={findings} onNewAnalysis={() => navigate("/projects/new")} />;
}

function ResultsRoute() {
  const { result, originalFiles, rerun, history, handleStart } = useWorkspace();
  const { id } = useParams();
  if (result && (!id || id === result.project_id || id === result.run_id)) {
    const completedAt = history.find((entry) => entry.project_id === result.project_id)?.timestamp;
    return <ResultsPage originalFiles={originalFiles} result={result} completedAt={completedAt} onRerun={rerun} />;
  }
  return <PersistedResultsRoute runOrProjectId={id ?? ""} onRerun={(files, name, projectId, tools, checks, sandboxStdin, testCases) => handleStart(files, tools, name, projectId, checks, sandboxStdin, undefined, "single_file", testCases)} />;
}

function HistoryRoute() {
  const [history, setHistory] = useState<PersistedHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const navigate = useNavigate();

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void getAnalysisHistory(50, controller.signal)
      .then(setHistory)
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "History could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reloadKey]);

  return <RepairHistoryPage history={history} loading={loading} error={error} onRetry={() => setReloadKey((value) => value + 1)} onOpen={(runId) => navigate(`/reports/${encodeURIComponent(runId)}`)} onNewAnalysis={() => navigate("/projects/new")} />;
}

interface PersistedResultsRouteProps {
  readonly runOrProjectId: string;
  readonly onRerun: (files: ProjectFile[], name: string, projectId: string, tools: QualityTool[], checks: AnalysisCheckOptions, sandboxStdin?: string, testCases?: SandboxTestCase[]) => Promise<void>;
}

function PersistedResultsRoute({ runOrProjectId, onRerun }: Readonly<PersistedResultsRouteProps>) {
  const [reportData, setReportData] = useState<Report | null>(null);
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setReportData(null);
    void getReport(runOrProjectId, controller.signal)
      .then(async (report) => {
        if (!report.report) throw new Error(`Report is not ready (run status: ${report.status ?? "unknown"}).`);
        setReportData(report);
        try {
          const fileData = await getProjectFiles(report.project_id, controller.signal);
          setFiles(fileData.files);
        } catch {
          setFiles([]);
        }
      })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Report could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [runOrProjectId, reloadKey]);

  if (loading) return <section className="page" aria-live="polite">Loading persisted report…</section>;
  if (error || !reportData) return <section className="page empty-state"><strong>Report unavailable</strong><span>{error || "The report API returned no report."}</span><button className="btn btn--primary" onClick={() => setReloadKey((value) => value + 1)}>Retry</button><Link className="btn" to="/history">Open history</Link></section>;

  const rawResult = asRecord(reportData.result);
  const finalReport = asRecord(reportData.report);
  const findings = (Array.isArray(rawResult.findings) ? rawResult.findings : finalReport.findings ?? []) as WorkflowRunResponse["findings"];
  const validationStatus = (rawResult.validation_status ?? finalReport.validation_status ?? "unknown") as WorkflowRunResponse["validation_status"];
  const hydrated: WorkflowRunResponse = {
    ...rawResult as unknown as WorkflowRunResponse,
    run_id: reportData.run_id,
    project_id: reportData.project_id,
    project_name: reportData.project_name,
    validation_status: validationStatus,
    retry_count: typeof rawResult.retry_count === "number"
      ? rawResult.retry_count
      : typeof finalReport.retry_count === "number" ? finalReport.retry_count : undefined,
    findings,
    final_report: finalReport as unknown as WorkflowRunResponse["final_report"],
  };
  const checks = finalReport.analysis_checks as Record<string, boolean> | undefined;
  const activeChecks: AnalysisCheckOptions = checks ? {
    codeQuality: Boolean(checks.code_quality),
    security: Boolean(checks.security),
    bugResource: Boolean(checks.bug_resource),
    complexity: Boolean(checks.complexity),
    automatedRepair: Boolean(checks.automated_repair),
    finalValidation: Boolean(checks.final_validation),
    aiReportRecommendations: Boolean(checks.ai_report_recommendations),
  } : DEFAULT_ANALYSIS_CHECKS;
  const qualityTools = (asRecord(finalReport.quality_summary).tool_runs as Array<{ tool?: string }> | undefined)
    ?.map((run) => run.tool)
    .filter((tool): tool is QualityTool => tool === "ruff" || tool === "pylint" || tool === "radon" || tool === "mypy") ?? ["ruff", "pylint", "radon", "mypy"];

  const testCases = Array.isArray(finalReport.test_cases) ? finalReport.test_cases as SandboxTestCase[] : [];
  return <ResultsPage originalFiles={files} result={hydrated} completedAt={reportData.completed_at} onRerun={() => void onRerun(files, reportData.project_name ?? "Python project", reportData.project_id, qualityTools, activeChecks, typeof finalReport.sandbox_stdin === "string" ? finalReport.sandbox_stdin : "", testCases)} />;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

interface UnavailablePageProps { readonly title: string; }
function UnavailablePage({ title }: Readonly<UnavailablePageProps>) {
  return <section className="page empty-state"><h1>{title}</h1><strong>This page is not connected to a backend API yet.</strong><span>It will be implemented in a later delivery phase; no success data is being fabricated.</span></section>;
}

function NotFoundPage() {
  return <section className="page page--error"><h1>Page not found</h1><p>The requested workspace page does not exist.</p><Link className="btn btn--primary" to="/">Return to dashboard</Link></section>;
}
