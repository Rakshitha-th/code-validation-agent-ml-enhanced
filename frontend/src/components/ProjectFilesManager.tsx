import { useEffect, useState } from "react";
import { deleteProjectFile, getProjectFiles, renameProjectFile, saveProjectFile, validateProjectFilePath } from "../api/files";
import { CodeEditor } from "./CodeEditor";
import type { ProjectFile } from "../api/types";
import { usePermission } from "../auth/permissions";

interface ProjectFilesManagerProps { readonly projectId: string; }

export function ProjectFilesManager({ projectId }: Readonly<ProjectFilesManagerProps>) {
  const canEdit = usePermission("editProject");
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [draft, setDraft] = useState("");
  const [newPath, setNewPath] = useState("");
  const [renamePath, setRenamePath] = useState("");
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const activeFile = files.find((file) => file.path === selectedPath);

  useEffect(() => {
    const controller = new AbortController();
    void getProjectFiles(projectId, controller.signal).then(({ files: items }) => {
      setFiles(items); setSelectedPath(items[0]?.path ?? ""); setDraft(items[0]?.content ?? "");
    }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Project files could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId]);

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); Reflect.set(event, "returnValue", ""); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function selectFile(path: string) {
    if (dirty && !window.confirm("Discard unsaved changes to the current file?")) return;
    const file = files.find((item) => item.path === path);
    if (file) { setSelectedPath(path); setDraft(file.content); setDirty(false); setError(""); }
  }

  async function createFile() {
    try {
      const path = validateProjectFilePath(newPath);
      if (files.some((file) => file.path === path)) throw new Error("A file with that path already exists.");
      setBusy(true); setError("");
      const file = { path, content: "" };
      await saveProjectFile(projectId, file);
      setFiles((items) => [...items, file]); setSelectedPath(path); setDraft(""); setNewPath(""); setDirty(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "File could not be created."); }
    finally { setBusy(false); }
  }

  async function saveFile() {
    if (!activeFile) return;
    setBusy(true); setError("");
    try {
      await saveProjectFile(projectId, { path: activeFile.path, content: draft });
      setFiles((items) => items.map((item) => item.path === activeFile.path ? { ...item, content: draft } : item)); setDirty(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "File could not be saved."); }
    finally { setBusy(false); }
  }

  async function renameFile() {
    if (!activeFile) return;
    try {
      const path = validateProjectFilePath(renamePath);
      if (files.some((file) => file.path === path)) throw new Error("A file with that path already exists.");
      setBusy(true); setError(""); await renameProjectFile(projectId, activeFile.path, path);
      setFiles((items) => items.map((item) => item.path === activeFile.path ? { ...item, path } : item)); setSelectedPath(path); setRenamePath(""); setDirty(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "File could not be renamed."); }
    finally { setBusy(false); }
  }

  async function removeFile() {
    if (!activeFile || !window.confirm(`Delete ${activeFile.path}? This cannot be undone.`)) return;
    setBusy(true); setError("");
    try {
      await deleteProjectFile(projectId, activeFile.path);
      const remaining = files.filter((file) => file.path !== activeFile.path);
      setFiles(remaining); setSelectedPath(remaining[0]?.path ?? ""); setDraft(remaining[0]?.content ?? ""); setDirty(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "File could not be deleted."); }
    finally { setBusy(false); }
  }

  return <section className="project-files-manager"><div className="section-label"><h2>File editor</h2><span>{dirty ? "Unsaved changes" : `${files.length} files`}</span></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{loading && <p>Loading file contents...</p>}
    {!loading && <div className="project-file-workspace"><nav aria-label="Project files">{files.map((file) => <button key={file.path} className="btn" aria-current={selectedPath === file.path ? "page" : undefined} onClick={() => selectFile(file.path)}>{file.path}</button>)}</nav>
      <div className="project-file-editor">{canEdit && <><label>New Python file<input value={newPath} onChange={(event) => setNewPath(event.target.value)} placeholder="src/module.py" /></label><button className="btn" disabled={busy || !newPath.trim()} onClick={() => void createFile()}>Create</button></>}
        {activeFile ? <><CodeEditor path={activeFile.path} value={draft} readOnly={!canEdit} onChange={(value) => { setDraft(value); setDirty(true); }} height="360px" />
          {canEdit && <div className="project-file-actions"><button className="btn btn--primary" disabled={busy || !dirty} onClick={() => void saveFile()}>{busy ? "Saving..." : "Save"}</button><label>Rename file<input value={renamePath} onChange={(event) => setRenamePath(event.target.value)} placeholder={activeFile.path} /></label><button className="btn" disabled={busy || !renamePath.trim()} onClick={() => void renameFile()}>Rename</button><button className="btn btn--danger" disabled={busy} onClick={() => void removeFile()}>Delete file</button></div>}
        </> : <p>No Python files are available.</p>}
      </div>
    </div>}
  </section>;
}