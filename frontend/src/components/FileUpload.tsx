import { useRef, useState } from "react";
import JSZip from "jszip";
import type { ProjectFile } from "../types/finding";
import { detectProjectLanguage, projectLanguageForPath, validateProjectFilePath } from "../api/files";

const MANIFEST_NAMES = new Set([
  "requirements.txt", "pyproject.toml", "package.json", "package-lock.json",
  "tsconfig.json", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "go.sum",
]);

interface PreparedFile extends ProjectFile {
  readonly sizeBytes: number;
}

interface FileUploadProps {
  readonly onFilesReady: (files: ProjectFile[]) => void;
}

export function FileUpload({ onFilesReady }: Readonly<FileUploadProps>) {
  const inputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement | null>(null);
  const [files, setFiles] = useState<PreparedFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ readonly completed: number; readonly total: number } | null>(null);

  async function handleFiles(fileList: FileList) {
    setError(null);
    const selectedFiles = Array.from(fileList);
    const sourceFiles = selectedFiles.filter((file) => projectLanguageForPath(file.name) !== null);
    const manifestFiles = selectedFiles.filter((file) => MANIFEST_NAMES.has(file.name.toLowerCase()));
    const zipFiles = selectedFiles.filter((file) => file.name.toLowerCase().endsWith(".zip"));
    const totalInputs = sourceFiles.length + manifestFiles.length + zipFiles.length;
    let completedInputs = 0;
    if (sourceFiles.length === 0 && manifestFiles.length === 0 && zipFiles.length === 0) {
      setError("Select supported source files, project manifests, a folder, or a ZIP archive.");
      return;
    }

    try {
      setProgress({ completed: 0, total: totalInputs });
      const readFiles: PreparedFile[] = await Promise.all(sourceFiles.map(async (file) => {
        const prepared = {
          path: validateProjectFilePath(file.webkitRelativePath || file.name),
          content: await file.text(),
          sizeBytes: file.size,
        };
        completedInputs += 1;
        setProgress({ completed: completedInputs, total: totalInputs });
        return prepared;
      }));

      const readManifests: PreparedFile[] = await Promise.all(manifestFiles.map(async (file) => {
        const prepared = {
          path: validateProjectFilePath(file.webkitRelativePath || file.name),
          content: await file.text(),
          sizeBytes: file.size,
        };
        completedInputs += 1;
        setProgress({ completed: completedInputs, total: totalInputs });
        return prepared;
      }));
      readFiles.push(...readManifests);

      const archiveContents = await Promise.all(zipFiles.map(async (archive) => {
        const zip = await JSZip.loadAsync(archive);
        const entries = Object.values(zip.files).filter((entry) => !entry.dir && (
          projectLanguageForPath(entry.name) !== null
          || MANIFEST_NAMES.has(entry.name.split("/").pop()?.toLowerCase() ?? "")
        ));
        const extracted = await Promise.all(entries.map(async (entry) => {
          const content = await entry.async("string");
          return { path: validateProjectFilePath(entry.name), content, sizeBytes: new Blob([content]).size };
        }));
        return extracted;
      }));
      readFiles.push(...archiveContents.flat());
      completedInputs += zipFiles.length;
      setProgress({ completed: completedInputs, total: totalInputs });

      const uniqueFiles = Array.from(new Map(readFiles.map((file) => [file.path, file])).values());
      const sourcePaths = uniqueFiles.filter((file) => projectLanguageForPath(file.path)).map((file) => file.path);
      if (sourcePaths.length === 0) {
        setError("No supported source files were found in the selected input.");
        return;
      }
      detectProjectLanguage(sourcePaths);

      setFiles(uniqueFiles);
      onFilesReady(uniqueFiles.map(({ path, content }) => ({ path, content })));
    } catch (error) {
      setError(error instanceof Error
        ? error.message
        : "Input could not be read. Use supported source files and relative paths without '..' segments.");
    } finally {
      setProgress(null);
      if (inputRef.current) inputRef.current.value = "";
      if (directoryInputRef.current) directoryInputRef.current.value = "";
    }
  }

  function removeFile(path: string) {
    const remaining = files.filter((file) => file.path !== path);
    setFiles(remaining);
    onFilesReady(remaining.map(({ path, content }) => ({ path, content })));
  }

  const totalSize = files.reduce((sum, file) => sum + file.sizeBytes, 0);

  return (
    <div className="file-upload">
      <div
        className={`file-upload__dropzone ${files.length > 0 ? "file-upload__dropzone--ready" : ""}`}
        onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }}
        onDrop={(e) => {
          e.preventDefault();
          if (!progress && e.dataTransfer.files.length) void handleFiles(e.dataTransfer.files);
        }}
        aria-busy={progress !== null}
      >
        <label className="file-upload__prompt" htmlFor="project-files-input">
          <span className="upload-glyph">↑</span>
          <span className="file-upload__prompt-copy"><strong>Drop source files, a folder, or ZIP archive</strong><br /><span className="file-upload__hint">Python, JavaScript, TypeScript, Java, C/C++, Go, or C#</span></span>
        </label>
        <div className="upload-actions">
          <button type="button" className="btn btn--secondary upload-action" disabled={progress !== null} onClick={() => inputRef.current?.click()}>Choose files or ZIP</button>
          <button type="button" className="btn btn--link upload-action" disabled={progress !== null} onClick={() => directoryInputRef.current?.click()}>Choose folder</button>
        </div>
        <input
          ref={inputRef}
          id="project-files-input"
          type="file"
          multiple
          accept=".py,.js,.jsx,.mjs,.cjs,.ts,.tsx,.java,.c,.h,.cc,.cpp,.cxx,.hh,.hpp,.hxx,.go,.cs,.zip,requirements.txt,pyproject.toml,package.json,tsconfig.json,pom.xml,build.gradle,build.gradle.kts,go.mod,go.sum"
          hidden
          onChange={(e) => e.target.files && void handleFiles(e.target.files)}
        />
        <input
          type="file"
          multiple
          hidden
          onChange={(e) => e.target.files && void handleFiles(e.target.files)}
          ref={(element) => {
            if (element) element.setAttribute("webkitdirectory", "");
            if (element) element.setAttribute("accept", ".py,.js,.jsx,.mjs,.cjs,.ts,.tsx,.java,.c,.h,.cc,.cpp,.cxx,.hh,.hpp,.hxx,.go,.cs,.zip,requirements.txt,pyproject.toml,package.json,tsconfig.json,pom.xml,build.gradle,build.gradle.kts,go.mod,go.sum");
            directoryInputRef.current = element;
          }}
        />
      </div>

      {progress && <div className="file-upload__progress" role="status" aria-live="polite"><span>Preparing selected files: {progress.completed} of {progress.total}</span><progress max={progress.total} value={progress.completed} aria-label="Preparing selected files" /></div>}
      {error && <p className="file-upload__error" role="alert">{error}</p>}

      {files.length > 0 && (
        <>
        <p className="file-upload__summary">{files.filter((file) => projectLanguageForPath(file.path)).length} source files · {files.filter((file) => MANIFEST_NAMES.has(file.path.split("/").pop()?.toLowerCase() ?? "")).length} manifests · {formatBytes(totalSize)}. Selecting another set replaces these files.</p>
        <ul className="file-upload__list">
          {files.map((file) => (
            <li key={file.path}><span>{projectLanguageForPath(file.path)?.slice(0, 2).toUpperCase() ?? "CFG"}</span><span>{file.path}<small>{formatBytes(file.sizeBytes)}</small></span><b>ready</b><button type="button" className="btn btn--ghost" aria-label={`Remove ${file.path}`} onClick={() => removeFile(file.path)}>Remove</button></li>
          ))}
        </ul>
        </>
      )}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
