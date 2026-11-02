import { apiClient, mocksEnabled } from "./client";
import type { ProjectFile } from "./types";

export interface ProjectFilesResponse {
  readonly project_id: string;
  readonly files: ProjectFile[];
}

const mockProjectFiles = new Map<string, ProjectFile[]>();
window.addEventListener("coding-validation:clear-user-data", () => mockProjectFiles.clear());

function normalizeSeparators(path: string): string {
  return [...path].map((character) => character === "\\" ? "/" : character).join("");
}

export function setMockProjectFiles(projectId: string, files: ProjectFile[]): void {
  mockProjectFiles.set(projectId, [...files]);
}

export function deleteMockProjectFiles(projectId: string): void {
  mockProjectFiles.delete(projectId);
}

export function validatePythonPath(value: string): string {
  const path = normalizeSeparators(value.trim());
  if (!path || path.startsWith("/") || /^[a-z]:/i.test(path)) throw new Error("Use a relative Python file path.");
  if (path.split("/").some((segment) => segment === ".." || segment === "")) throw new Error("Path segments cannot be empty or contain '..'.");
  if (!path.toLowerCase().endsWith(".py")) throw new Error("Only Python source files (.py) are supported.");
  return path;
}

const SOURCE_EXTENSIONS: Readonly<Record<string, string>> = {
  ".py": "python",
  ".js": "javascript",
  ".jsx": "javascript",
  ".mjs": "javascript",
  ".cjs": "javascript",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".java": "java",
  ".c": "c",
  ".h": "c",
  ".cc": "cpp",
  ".cpp": "cpp",
  ".cxx": "cpp",
  ".hh": "cpp",
  ".hpp": "cpp",
  ".hxx": "cpp",
  ".go": "go",
  ".cs": "csharp",
};

const LANGUAGE_LABELS: Readonly<Record<string, string>> = {
  python: "Python",
  java: "Java",
  c: "C",
  cpp: "C++",
  csharp: "C#",
  javascript: "JavaScript",
  typescript: "TypeScript",
  go: "Go",
};
const LANDING_LANGUAGE_ORDER = ["python", "java", "c", "cpp", "csharp", "javascript", "typescript", "go"] as const;
const LANGUAGE_EXTENSIONS: Readonly<Record<string, string>> = {
  python: ".py",
  java: ".java",
  c: ".c",
  cpp: ".cpp",
  csharp: ".cs",
  javascript: ".js",
  typescript: ".ts",
  go: ".go",
};

export const SUPPORTED_LANGUAGE_OPTIONS = LANDING_LANGUAGE_ORDER
  .filter((language) => Object.values(SOURCE_EXTENSIONS).includes(language))
  .map((value) => ({ value, label: LANGUAGE_LABELS[value], extension: LANGUAGE_EXTENSIONS[value] }));

const PROJECT_MANIFESTS = new Set([
  "requirements.txt", "pyproject.toml", "package.json", "package-lock.json",
  "tsconfig.json", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "go.sum",
  "global.json", "nuget.config",
]);
const PROJECT_EXTENSIONS = new Set([".csproj", ".sln"]);

export function projectLanguageForPath(value: string): string | null {
  const extension = value.toLowerCase().slice(value.lastIndexOf("."));
  return SOURCE_EXTENSIONS[extension] ?? null;
}

export function detectProjectLanguage(paths: string[]): string | null {
  const languages = new Set(paths.map(projectLanguageForPath).filter((language): language is string => language !== null));
  if (languages.size === 0) return null;
  if (languages.size === 1) return [...languages][0] ?? null;
  if ([...languages].every((language) => language === "javascript" || language === "typescript")) return "typescript";
  if ([...languages].every((language) => language === "c" || language === "cpp")) return languages.has("cpp") ? "cpp" : "c";
  throw new Error("Analyze one programming language per project (JavaScript and TypeScript may be combined).");
}

export function validateSourcePath(value: string): string {
  const path = validateProjectFilePath(value);
  if (!projectLanguageForPath(path)) throw new Error("Choose a source file in a supported language.");
  return path;
}

export function validateProjectFilePath(value: string): string {
  const path = normalizeSeparators(value.trim());
  if (!path || path.startsWith("/") || /^[a-z]:/i.test(path)) throw new Error("Use a relative project file path.");
  if (path.split("/").some((segment) => segment === ".." || segment === "")) throw new Error("Path segments cannot be empty or contain '..'.");
  const pathSegments = path.split("/");
  const fileName = pathSegments[pathSegments.length - 1]?.toLowerCase();
  const extension = path.toLowerCase().slice(path.lastIndexOf("."));
  if (!projectLanguageForPath(path) && !PROJECT_MANIFESTS.has(fileName ?? "") && !PROJECT_EXTENSIONS.has(extension)) {
    throw new Error("Unsupported source file or project manifest.");
  }
  return path;
}

export async function getProjectFiles(projectId: string, signal?: AbortSignal): Promise<ProjectFilesResponse> {
  if (mocksEnabled()) {
    const files = mockProjectFiles.get(projectId);
    if (!files) throw new Error("Project files were not found in mock data.");
    return { project_id: projectId, files };
  }
  return apiClient.get<ProjectFilesResponse>(`/api/files?project_id=${encodeURIComponent(projectId)}`, signal);
}

export async function saveProjectFiles(projectId: string, files: ProjectFile[], signal?: AbortSignal): Promise<ProjectFilesResponse> {
  const safeFiles = files.map((file) => ({ ...file, path: validateProjectFilePath(file.path) }));
  if (mocksEnabled()) {
    mockProjectFiles.set(projectId, safeFiles);
    return { project_id: projectId, files: safeFiles };
  }
  return apiClient.post<ProjectFilesResponse>("/api/files", { project_id: projectId, files: safeFiles }, signal);
}

export async function saveProjectFile(projectId: string, file: ProjectFile, signal?: AbortSignal): Promise<void> {
  const path = validateProjectFilePath(file.path);
  const existing = await getProjectFiles(projectId, signal);
  await saveProjectFiles(projectId, [...existing.files.filter((item) => item.path !== path), { path, content: file.content }], signal);
}

export async function renameProjectFile(projectId: string, oldPath: string, newPath: string, signal?: AbortSignal): Promise<void> {
  const source = validateProjectFilePath(oldPath);
  const target = validateProjectFilePath(newPath);
  const files = (await getProjectFiles(projectId, signal)).files;
  if (!files.some((item) => item.path === source)) throw new Error("File was not found.");
  if (files.some((item) => item.path === target)) throw new Error("A file with that path already exists.");
  await saveProjectFiles(projectId, files.map((item) => item.path === source ? { ...item, path: target } : item), signal);
}

export async function deleteProjectFile(projectId: string, path: string, signal?: AbortSignal): Promise<void> {
  const safePath = validateProjectFilePath(path);
  const files = (await getProjectFiles(projectId, signal)).files;
  await saveProjectFiles(projectId, files.filter((file) => file.path !== safePath), signal);
}
