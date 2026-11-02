import { FileUpload } from "../components/FileUpload";
import { useEffect, useState } from "react";
import { CodeEditor } from "../components/CodeEditor";
import type { ProjectFile, QualityTool, RunHistoryEntry, SandboxTestCase } from "../types/finding";
import { DEFAULT_ANALYSIS_CHECKS, type AnalysisCheckOptions } from "../api/configuration";
import { detectProjectLanguage, projectLanguageForPath, SUPPORTED_LANGUAGE_OPTIONS, validateSourcePath } from "../api/files";
import { apiClient } from "../api/client";

interface UploadPageProps {
  onStart: (files: ProjectFile[], tools: QualityTool[], projectName?: string, existingProjectId?: string, checks?: AnalysisCheckOptions, sandboxStdin?: string, language?: string, projectType?: "dotnet" | "single_file", testCases?: SandboxTestCase[]) => Promise<void>;
  history: RunHistoryEntry[];
  onOpenHistory: (entry: RunHistoryEntry) => void;
  initialFiles?: ProjectFile[];
  initialProjectName?: string;
  existingProjectId?: string;
  projectLoadError?: string;
  runInProgress?: boolean;
}

const TOOL_OPTIONS: QualityTool[] = ["ruff", "pylint", "radon", "mypy"];
const LANGUAGE_OPTIONS = SUPPORTED_LANGUAGE_OPTIONS;
const QUICK_CODE_TEMPLATES: Readonly<Record<string, string>> = {
  python: "def example(value):\n    return value\n",
  javascript: "function example(value) {\n  return value;\n}\n",
  typescript: "function example(value: string): string {\n  return value;\n}\n",
  java: "class Example {\n  static String example(String value) {\n    return value;\n  }\n}\n",
  c: "#include <stdio.h>\n\nint main(void) {\n    return 0;\n}\n",
  cpp: "#include <iostream>\n\nint main() {\n    return 0;\n}\n",
  csharp: "class Example {\n    static string Run(string value) => value;\n}\n",
  go: "package main\n\nfunc example(value string) string {\n\treturn value\n}\n",
};
const TOOL_DESCRIPTION: Record<QualityTool, string> = {
  ruff: "Fast Python linting and style analysis",
  pylint: "Python code quality and maintainability analysis",
  radon: "Python complexity analysis",
  mypy: "Python static type checking",
};

interface AgentHealth {
  readonly status: string;
  readonly healthy: boolean;
}

const CHECK_OPTIONS: Array<{ key: keyof AnalysisCheckOptions; title: string; description: string; services: string[] }> = [
  { key: "codeQuality", title: "Code Quality", description: "Run language-aware code quality checks.", services: ["code_quality"] },
  { key: "security", title: "Security", description: "Run security scanners against the submitted source.", services: ["security"] },
  { key: "bugResource", title: "Bug & Resource Analysis", description: "Detect common bug and resource-use risks.", services: ["bug_resource"] },
  { key: "complexity", title: "Complexity Analysis", description: "Identify source complexity hotspots.", services: ["code_quality"] },
  { key: "automatedRepair", title: "Automated Repair", description: "Generate a repair candidate with the configured Ollama model, then validate it.", services: ["repair"] },
  { key: "finalValidation", title: "Final Validation", description: "Run the sandbox and validation checks on the submitted or repaired code.", services: ["validation", "sandbox"] },
  { key: "aiReportRecommendations", title: "AI Report & Recommendations", description: "Send source code to the configured Ollama model for an additional review and proposed fixes.", services: ["report_recommendation"] },
];

function describeAgentAvailability(
  services: Record<string, AgentHealth> | null,
  requiredServices: string[],
  description: string,
): string {
  if (!services) return "Checking agent availability...";
  const required = requiredServices.map((name) => services[name]);
  if (required.every((service) => service?.healthy)) return description;
  if (required.some((service) => service?.status === "not_configured")) return "Not available in this deployment.";
  if (required.some((service) => !service)) return "Agent availability could not be checked.";
  return "Service is currently unavailable.";
}

function buildCheckOptions(services: Record<string, AgentHealth> | null) {
  return CHECK_OPTIONS.map((option) => {
    const required = option.services.map((name) => services?.[name]);
    return {
      ...option,
      available: required.length > 0 && required.every((service) => service?.healthy === true),
      description: describeAgentAvailability(services, option.services, option.description),
    };
  });
}

function checksForAvailableServices(services: Record<string, AgentHealth>): AnalysisCheckOptions {
  const isHealthy = (name: string) => services[name]?.healthy === true;
  return {
    codeQuality: isHealthy("code_quality"),
    security: isHealthy("security"),
    bugResource: isHealthy("bug_resource"),
    complexity: isHealthy("code_quality"),
    automatedRepair: isHealthy("repair"),
    finalValidation: isHealthy("validation") && isHealthy("sandbox"),
    aiReportRecommendations: isHealthy("report_recommendation"),
  };
}

function checkMarker(selected: boolean, available: boolean): string {
  if (selected) return "✓";
  if (!available) return "–";
  return "";
}

function parseSandboxTestCases(value: string): SandboxTestCase[] {
  if (!value.trim()) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("Test cases must be valid JSON.");
  }
  if (!Array.isArray(parsed) || parsed.length > 10) {
    throw new Error("Test cases must be a JSON array with no more than 10 entries.");
  }
  return parsed.map((item, index) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      throw new Error(`Test case ${index + 1} must be an object.`);
    }
    const record = item as Record<string, unknown>;
    if (record.input !== undefined && typeof record.input !== "string") {
      throw new Error(`Test case ${index + 1} input must be a string.`);
    }
    if (record.expected !== undefined && record.expected !== null && typeof record.expected !== "string") {
      throw new Error(`Test case ${index + 1} expected output must be a string or null.`);
    }
    return {
      input: typeof record.input === "string" ? record.input : "",
      ...(typeof record.expected === "string" || record.expected === null ? { expected: record.expected } : {}),
    };
  });
}

function qualityToolStatus(configured: boolean, selected: boolean): string {
  if (configured) return "Configured";
  if (selected) return "Disabled by check";
  return "Not selected";
}

function AnalysisCheckCards({ options, checks, onChange }: Readonly<{
  options: ReturnType<typeof buildCheckOptions>;
  checks: AnalysisCheckOptions;
  onChange: <K extends keyof AnalysisCheckOptions>(key: K, value: AnalysisCheckOptions[K]) => void;
}>) {
  return <div className="analysis-check-grid">{options.map(({ key, title, description, available }) => <label className={`analysis-check-card${checks[key] ? " analysis-check-card--selected" : ""}${available ? "" : " analysis-check-card--unavailable"}`} key={key}><input type="checkbox" checked={checks[key]} disabled={!available} onChange={(event) => onChange(key, event.target.checked)} /><span className="analysis-check-card__mark" aria-hidden="true">{checkMarker(checks[key], available)}</span><span><strong>{title}</strong><small>{description}</small>{!available && <b>Unavailable</b>}</span></label>)}</div>;
}

function AnalysisCheckWarnings({ checks, activeChecks }: Readonly<{ checks: AnalysisCheckOptions; activeChecks: AnalysisCheckOptions }>) {
  return <>
    {checks.codeQuality && !activeChecks.codeQuality && <output className="inline-error">Select Ruff or Pylint to run Python Code Quality analysis.</output>}
    {checks.complexity && !activeChecks.complexity && <output className="inline-error">Select Radon to run Python Complexity Analysis.</output>}
  </>;
}

function QualityToolCards({ tools, selectedTools, onChange }: Readonly<{
  tools: QualityTool[];
  selectedTools: QualityTool[];
  onChange: (tool: QualityTool, selected: boolean) => void;
}>) {
  return <div className="quality-tool-grid">{TOOL_OPTIONS.map((tool) => {
    const configured = selectedTools.includes(tool);
    const selected = tools.includes(tool);
    const className = selected ? "quality-tool-card quality-tool-card--selected" : "quality-tool-card";
    return <label key={tool} className={className}><input type="checkbox" checked={selected} onChange={(event) => onChange(tool, event.target.checked)} /><span className="quality-tool-card__mark" aria-hidden="true">{selected ? "✓" : ""}</span><span><strong>{tool[0].toUpperCase() + tool.slice(1)}</strong><small>{TOOL_DESCRIPTION[tool]}</small></span><b>{qualityToolStatus(configured, selected)}</b></label>;
  })}</div>;
}

function UploadInputMethod({ inputMode, onInputModeChange, onFilesReady, code, onCodeChange, filename, onFilenameChange, validPath }: Readonly<{
  inputMode: "files" | "code";
  onInputModeChange: (mode: "files" | "code") => void;
  onFilesReady: (files: ProjectFile[]) => void;
  code: string;
  onCodeChange: (value: string) => void;
  filename: string;
  onFilenameChange: (value: string) => void;
  validPath: string;
}>) {
  return <>
    <fieldset className="input-tabs" aria-label="Code input method">
      <button type="button" aria-pressed={inputMode === "files"} className={inputMode === "files" ? "input-tab input-tab--active" : "input-tab"} onClick={() => onInputModeChange("files")}>Project files <small>UPLOAD</small></button>
      <button type="button" aria-pressed={inputMode === "code"} className={inputMode === "code" ? "input-tab input-tab--active" : "input-tab"} onClick={() => onInputModeChange("code")}>Quick code <small>PASTE</small></button>
    </fieldset>
    {inputMode === "files" ? <FileUpload onFilesReady={onFilesReady} /> : <div className="code-entry">
      <div className="code-entry__bar"><span className="window-dots"><i /><i /><i /></span><label className="code-entry__filename">Filename<input aria-label="Filename for pasted source code" value={filename} maxLength={180} onChange={(event) => onFilenameChange(event.target.value)} /></label><span className="code-entry__meta">{validPath ? (projectLanguageForPath(validPath) ?? "Supported source") : "Select source extension"}</span></div>
      <CodeEditor path={validPath || "pasted-code"} value={code} onChange={onCodeChange} height="250px" />
      {!validPath && <p className="inline-error" role="alert">Enter a relative source filename with a supported extension. Paths cannot be absolute or contain '..'.</p>}
    </div>}
  </>;
}

function AnalysisSummary({ checks, tools }: Readonly<{ checks: AnalysisCheckOptions; tools: QualityTool[] }>) {
  const checkLabels: Array<[boolean, string]> = [
    [checks.codeQuality, "Code Quality"],
    [checks.security, "Security"],
    [checks.bugResource, "Bugs & Resources"],
    [checks.complexity, "Complexity"],
    [checks.automatedRepair, "Repair"],
    [checks.finalValidation, "Validation"],
    [checks.aiReportRecommendations, "AI Report & Recommendations"],
  ];
  const selectedChecks = checkLabels.filter(([selected]) => selected).map(([, label]) => label).join(" · ") || "No checks selected";
  const selectedTools = tools.map((tool) => tool[0].toUpperCase() + tool.slice(1)).join(" · ") || "None selected";
  return <section className="validation-summary" aria-label="Analysis summary">
    <div><p className="kicker">READY TO RUN</p><h2>Analysis Summary</h2></div>
    <div><span>Checks</span><p>{selectedChecks}</p></div>
    <div><span>Tools</span><p>{selectedTools}</p></div>
    <div><span>Execution</span><p>Backend-managed; per-analysis sandbox limits unavailable</p></div>
  </section>;
}

function StartAnalysisControls({ disabled, submitting, runInProgress, error, onStart }: Readonly<{
  disabled: boolean;
  submitting: boolean;
  runInProgress: boolean;
  error: string;
  onStart: () => void;
}>) {
  return <>
    <button className="btn btn--primary" disabled={disabled} onClick={onStart}>{submitting ? "Analysis running..." : "Start Analysis"} {!submitting && <span>→</span>}</button>
    {(submitting || runInProgress) && <p className="analysis-pulse" role="status" aria-live="polite">Running the selected analysis checks...</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </>;
}

function RecentRuns({ history, onOpenHistory }: Readonly<{ history: RunHistoryEntry[]; onOpenHistory: (entry: RunHistoryEntry) => void }>) {
  if (history.length === 0) return null;
  return <section className="past-runs">
    <div className="section-label"><h2>Recent runs</h2><span>{history.length} saved locally</span></div>
    <ul className="history-list">{history.map((entry) => <li key={`${entry.project_id}-${entry.timestamp}`}>
      <button className="btn btn--link" onClick={() => onOpenHistory(entry)}>
        <span className="history-list__icon">↗</span><span><strong>{entry.project_id}</strong><small>{new Date(entry.timestamp).toLocaleString()}</small></span><b className={`status-pill status-pill--${entry.validation_status}`}>{entry.validation_status}</b>
      </button>
    </li>)}</ul>
  </section>;
}

export function UploadPage({ onStart, history, onOpenHistory, initialFiles = [], initialProjectName, existingProjectId, projectLoadError, runInProgress = false }: Readonly<UploadPageProps>) {
  const [files, setFiles] = useState<ProjectFile[]>(initialFiles);
  const [projectName, setProjectName] = useState(initialProjectName ?? "Code project");
  const [checks, setChecks] = useState(DEFAULT_ANALYSIS_CHECKS);
  const [tools, setTools] = useState<QualityTool[]>(TOOL_OPTIONS);
  const [inputMode, setInputMode] = useState<"files" | "code">("files");
  const [code, setCode] = useState("");
  const [pastedFilename, setPastedFilename] = useState("pasted-code");
  const [languageChoice, setLanguageChoice] = useState("auto");
  const [projectType, setProjectType] = useState<"dotnet" | "single_file">("single_file");
  const [sandboxStdin, setSandboxStdin] = useState("");
  const [testCasesJson, setTestCasesJson] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [agentServices, setAgentServices] = useState<Record<string, AgentHealth> | null>(null);
  const [agentAvailabilityError, setAgentAvailabilityError] = useState("");
  const checkOptions = buildCheckOptions(agentServices);
  const selectedFiles = inputMode === "code" ? [{ path: pastedFilename, content: code }] : files;
  let detectedLanguage: string | null = null;
  let languageSelectionValid = true;
  try {
    detectedLanguage = detectProjectLanguage(selectedFiles.map((file) => file.path));
  } catch {
    languageSelectionValid = false;
  }
  const isPythonProject = detectedLanguage === "python";
  const selectedLanguage = languageChoice === "auto" ? detectedLanguage : languageChoice;
  const languageMatchesFiles = !detectedLanguage || languageChoice === "auto" || languageChoice === detectedLanguage;
  const hasDotnetProject = selectedFiles.some((file) => file.path.toLowerCase().endsWith(".csproj"));
  const projectTypeValid = projectType !== "dotnet" || (
    inputMode === "files" && hasDotnetProject && selectedLanguage === "csharp"
  );
  const activeChecks: AnalysisCheckOptions = {
    ...checks,
    codeQuality: checks.codeQuality && (!isPythonProject || tools.some((tool) => tool === "ruff" || tool === "pylint")),
    complexity: checks.complexity && (!isPythonProject || tools.includes("radon")),
  };
  const selectedTools = isPythonProject
    ? tools.filter((tool) => tool === "radon" ? activeChecks.complexity : activeChecks.codeQuality)
    : [];
  const anyCheckSelected = Object.values(activeChecks).some(Boolean);
  let validPastedPath = "";
  try {
    validPastedPath = validateSourcePath(pastedFilename);
  } catch {
    validPastedPath = "";
  }

  useEffect(() => {
    if (initialFiles.length) { setFiles(initialFiles); setInputMode("files"); }
    if (initialProjectName) setProjectName(initialProjectName);
  }, [initialFiles, initialProjectName]);

  useEffect(() => {
    const controller = new AbortController();
    void apiClient.get<{ services?: Record<string, AgentHealth> }>("/api/system/agents", controller.signal)
      .then((payload) => {
        if (controller.signal.aborted) return;
        const services = payload.services ?? {};
        setAgentServices(services);
        setChecks(checksForAvailableServices(services));
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setAgentServices({});
        setChecks(checksForAvailableServices({}));
        setAgentAvailabilityError("Agent availability could not be checked. Refresh the page or check the service connection before starting analysis.");
      });
    return () => controller.abort();
  }, []);

  async function start() {
    if (runInProgress) {
      setSubmitError("A validation run is already in progress. Wait for it to finish before starting another.");
      return;
    }
    if (submitting) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      const testCases = parseSandboxTestCases(testCasesJson);
      await onStart(inputMode === "code" ? [{ path: validPastedPath, content: code }] : files, selectedTools, projectName.trim(), existingProjectId, activeChecks, sandboxStdin, selectedLanguage ?? undefined, projectType, testCases);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Validation could not be started.");
    } finally {
      setSubmitting(false);
    }
  }

  function setCheck<K extends keyof AnalysisCheckOptions>(key: K, value: AnalysisCheckOptions[K]) {
    setChecks((current) => ({ ...current, [key]: value }));
    if (key === "complexity" && value) setTools((current) => current.includes("radon") ? current : [...current, "radon"]);
    if (key === "codeQuality" && value) setTools((current) => current.some((tool) => tool === "ruff" || tool === "pylint") ? current : [...current, "ruff"]);
  }

  function setTool(tool: QualityTool, selected: boolean) {
    const nextTools = selected ? [...new Set([...tools, tool])] : tools.filter((item) => item !== tool);
    setTools(nextTools);
    if (tool === "radon") setChecks((current) => ({ ...current, complexity: selected }));
    if (tool === "ruff" || tool === "pylint") setChecks((current) => ({ ...current, codeQuality: nextTools.some((item) => item === "ruff" || item === "pylint") }));
  }

  return (
    <section className="page page--upload">
      <div className="page-heading">
        <div>
          <div className="kicker">New analysis <span>01 / 04</span></div>
          <h1>Choose checks.<br /><em>Start analysis.</em></h1>
          <p>Select the checks for your {detectedLanguage ?? "supported-language"} project.</p>
        </div>
        <div className="heading-stamp"><span>{detectedLanguage?.slice(0, 2).toUpperCase() ?? "SRC"}</span><strong>{detectedLanguage ?? "Source"}<br />analysis</strong></div>
      </div>

      <div className="project-input-meta"><label className="project-name-field">Project name<input value={projectName} maxLength={120} readOnly={Boolean(existingProjectId)} onChange={(event) => setProjectName(event.target.value)} /></label></div>
      <label className="project-name-field">Programming language
        <select value={languageChoice} onChange={(event) => {
          const choice = event.target.value;
          setLanguageChoice(choice);
          if (inputMode === "code" && choice !== "auto") {
            const previousLanguage = projectLanguageForPath(pastedFilename);
            const option = LANGUAGE_OPTIONS.find((language) => language.value === choice);
            const base = pastedFilename.replace(/\.[^.\\/]+$/, "") || "pasted-code";
            if (option) setPastedFilename(`${base}${option.extension}`);
            if (!code.trim() || (previousLanguage && code.trim() === QUICK_CODE_TEMPLATES[previousLanguage]?.trim())) {
              setCode(QUICK_CODE_TEMPLATES[choice] ?? "");
            }
          }
        }}>
          <option value="auto">{inputMode === "code" ? "Choose a language for pasted code" : "Detect from source files"}</option>
          {LANGUAGE_OPTIONS.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
        </select>
        <small>Detection is automatic by default. The selection must match the uploaded source files.</small>
      </label>
      <label className="project-name-field">Project type
        <select value={projectType} onChange={(event) => setProjectType(event.target.value as "dotnet" | "single_file")}>
          <option value="single_file">Source files / single-file project</option>
          <option value="dotnet">C# .NET SDK project (.csproj)</option>
        </select>
        <small>Select this only for a C# project containing a .csproj file; .NET builds may be unavailable under current sandbox restrictions.</small>
      </label>
      {projectLoadError && <p className="inline-error" role="alert">{projectLoadError}</p>}
      <UploadInputMethod inputMode={inputMode} onInputModeChange={setInputMode} onFilesReady={setFiles} code={code} onCodeChange={setCode} filename={pastedFilename} onFilenameChange={setPastedFilename} validPath={validPastedPath} />
      {selectedFiles.length > 0 && (!languageSelectionValid || !detectedLanguage || !languageMatchesFiles) && <p className="inline-error" role="alert">Choose supported source files from one language per analysis, and make sure the selected language matches their file extensions.</p>}
      {projectType === "dotnet" && !projectTypeValid && <p className="inline-error" role="alert">A .NET project requires C# source files and a .csproj file in the uploaded project.</p>}

      <section className="analysis-checks" aria-labelledby="analysis-checks-title">
        <div className="analysis-config-heading"><div><p className="kicker">MAIN CONFIGURATION</p><h2 id="analysis-checks-title">Analysis Checks</h2><p>All available checks are selected automatically. Clear any checks you want to skip.</p></div></div>
        <AnalysisCheckCards options={checkOptions} checks={checks} onChange={setCheck} />
        {agentAvailabilityError && <output className="inline-error" role="alert">{agentAvailabilityError}</output>}
        <AnalysisCheckWarnings checks={checks} activeChecks={activeChecks} />
      </section>

      {isPythonProject && <section className="code-quality-tools" aria-labelledby="quality-tools-title">
        <div className="analysis-config-heading"><div><p className="kicker">QUALITY EVIDENCE</p><h2 id="quality-tools-title">Code Quality Tools</h2><p>Choose which tools support the selected checks. Complexity analysis requires Radon.</p></div></div>
        <QualityToolCards tools={tools} selectedTools={selectedTools} onChange={setTool} />
      </section>}

      <details className="advanced-settings">
        <summary>Advanced Settings</summary>
        <p className="analysis-capability-note">Per-analysis sandbox timeout, memory, CPU, and network settings are not supported by the connected backend.</p>
        <label className="sandbox-stdin-field">Sandbox test input
          <textarea value={sandboxStdin} maxLength={100000} rows={4} onChange={(event) => setSandboxStdin(event.target.value)} aria-describedby="sandbox-stdin-help" />
          <small id="sandbox-stdin-help">Provide standard input for the program, one response per line. Leave empty if the program does not request input.</small>
        </label>
        <label className="sandbox-stdin-field">Expected-output test cases (JSON)
          <textarea value={testCasesJson} maxLength={1_000_000} rows={6} onChange={(event) => setTestCasesJson(event.target.value)} aria-describedby="sandbox-test-cases-help" placeholder={'[{"input":"your input","expected":"the exact output"}]'} />
          <small id="sandbox-test-cases-help">Each case runs separately. Provide expected output to compare it; omit expected to record execution output without claiming it matched.</small>
        </label>
      </details>

      <AnalysisSummary checks={activeChecks} tools={selectedTools} />
      <StartAnalysisControls disabled={agentServices === null || submitting || runInProgress || (inputMode === "files" && files.length === 0) || (inputMode === "code" && (!code.trim() || !validPastedPath)) || (selectedFiles.length > 0 && (!languageSelectionValid || !detectedLanguage || !languageMatchesFiles)) || !projectTypeValid || !anyCheckSelected || (checks.codeQuality && !activeChecks.codeQuality) || (checks.complexity && !activeChecks.complexity) || !projectName.trim() || Boolean(projectLoadError) || Boolean(agentAvailabilityError)} submitting={submitting} runInProgress={runInProgress} error={submitError} onStart={() => void start()} />
      <RecentRuns history={history} onOpenHistory={onOpenHistory} />
    </section>
  );
}
