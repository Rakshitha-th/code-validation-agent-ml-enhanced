import { useEffect, useMemo, useState } from "react";
import { DiffEditor } from "@monaco-editor/react";
import { projectLanguageForPath } from "../api/files";
import type { ProjectFile } from "../types/finding";
import type { RepairComparisonStatus } from "../analysis/codeComparison";

interface CodeComparisonProps {
  readonly original: ProjectFile[];
  readonly repaired: ProjectFile[];
  readonly status?: RepairComparisonStatus;
  readonly error?: string;
}

function safeDownloadName(path: string): string {
  const filename = path.replace(/\\/g, "/").split("/").pop() ?? "repaired.py";
  const safeName = [...filename]
    .filter((character) => {
      const code = character.charCodeAt(0);
      return code >= 32 && code !== 127;
    })
    .join("")
    .replace(/[<>:"/\\|?*]/g, "_");
  return safeName || "repaired.py";
}

function downloadFile(file: ProjectFile): void {
  const url = URL.createObjectURL(new Blob([file.content], { type: "text/plain;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = safeDownloadName(file.path);
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function CodeComparison({ original, repaired, status = "completed", error }: Readonly<CodeComparisonProps>) {
  const [selectedPath, setSelectedPath] = useState(original[0]?.path ?? "");
  const [copyMessage, setCopyMessage] = useState("");
  const [sideBySide, setSideBySide] = useState(() => typeof window === "undefined" || window.innerWidth >= 720);
  const originalByPath = useMemo(() => new Map(original.map((file) => [file.path, file])), [original]);
  const repairedByPath = useMemo(() => new Map(repaired.map((file) => [file.path, file])), [repaired]);
  const paths = original.map((file) => file.path);
  const selectedOriginal = originalByPath.get(selectedPath);
  const selectedRepaired = repairedByPath.get(selectedPath);
  const changedPaths = paths.filter((path) => {
    const before = originalByPath.get(path)?.content;
    const after = repairedByPath.get(path)?.content;
    return before !== undefined && after !== undefined && before !== after;
  });
  const unchanged = selectedOriginal && selectedRepaired && selectedOriginal.content === selectedRepaired.content;

  useEffect(() => {
    const updateLayout = () => setSideBySide(window.innerWidth >= 720);
    window.addEventListener("resize", updateLayout);
    return () => window.removeEventListener("resize", updateLayout);
  }, []);

  useEffect(() => {
    if (!original.some((file) => file.path === selectedPath)) {
      setSelectedPath(original[0]?.path ?? "");
    }
  }, [original, selectedPath]);

  if (status === "pending") return <p className="code-comparison__notice" role="status">The repair stage is still running. Original and repaired code will appear when it finishes.</p>;
  if (status === "not-run") return <p className="code-comparison__notice">No repair was performed for this run. The original source is preserved; there is no repaired version to compare.</p>;
  if (status === "unavailable") return <p className="code-comparison__notice" role="status">Repair output is unavailable for this run. The backend did not return a repair attempt or stage result.</p>;
  if (status === "failed" && changedPaths.length === 0) {
    return <p className="code-comparison__notice code-comparison__notice--error" role="alert">{error ? `Repair failed: ${error}` : "Repair failed. No repaired source is available; the original code was preserved."}</p>;
  }
  if (original.length === 0) return <p className="code-comparison__notice">Original source files were not returned for this run.</p>;
  if (!selectedOriginal || !selectedRepaired) {
    return <p className="code-comparison__notice code-comparison__notice--error" role="alert">A matching original or repaired source file is missing, so a reliable comparison cannot be shown.</p>;
  }

  async function copyRepaired(): Promise<void> {
    if (!selectedRepaired) return;
    try {
      await navigator.clipboard.writeText(selectedRepaired.content);
      setCopyMessage(`${selectedPath} copied.`);
    } catch {
      setCopyMessage("The repaired code could not be copied.");
    }
  }

  return <section className="code-comparison-view" aria-label="Original and repaired code comparison">
    <div className="code-comparison-view__toolbar">
      <label>File
        <select value={selectedPath} onChange={(event) => setSelectedPath(event.target.value)}>
          {paths.map((path) => <option key={path} value={path}>{path}</option>)}
        </select>
      </label>
      <span className={unchanged ? "code-comparison-view__state" : "code-comparison-view__state code-comparison-view__state--changed"}>
        {unchanged ? "Unchanged" : changedPaths.includes(selectedPath) ? "Changed" : "No repair output"}
      </span>
      <button className="btn" type="button" disabled={unchanged} onClick={() => void copyRepaired()}>Copy repaired code</button>
      <button className="btn" type="button" disabled={unchanged} onClick={() => downloadFile(selectedRepaired)}>Download repaired file</button>
    </div>
    {status === "failed" && <p className="code-comparison__notice code-comparison__notice--error" role="alert">{error ? `Repair reported an error: ${error}` : "Repair reported a failure; review the attempt details before using this candidate."}</p>}
    {sideBySide
      ? <div className="code-comparison__labels"><strong>Original</strong><strong>Repaired</strong></div>
      : <p className="code-comparison__labels code-comparison__labels--inline">Original and repaired code · inline diff</p>}
    <DiffEditor
      key={selectedPath}
      height="460px"
      original={selectedOriginal.content}
      modified={selectedRepaired.content}
      language={projectLanguageForPath(selectedPath) ?? "plaintext"}
      theme="vs-dark"
      options={{ readOnly: true, renderSideBySide: sideBySide, originalEditable: false, minimap: { enabled: false }, lineNumbers: "on", scrollBeyondLastLine: false }}
    />
    {copyMessage && <output className="code-comparison__copy-message" aria-live="polite">{copyMessage}</output>}
  </section>;
}
