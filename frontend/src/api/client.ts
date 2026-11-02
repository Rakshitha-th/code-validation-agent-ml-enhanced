import type { AuthSession } from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_URL ?? import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const USER_KEY = "coding-validation-user";

export class ApiError extends Error {
  readonly status: number;
  readonly safeDetails?: string;

  constructor(message: string, status: number, safeDetails?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.safeDetails = safeDetails;
  }
}

export function mocksEnabled(): boolean {
  return import.meta.env.VITE_USE_MOCKS === "true";
}

export function saveSession(session: AuthSession): void {
  clearSession();
  window.sessionStorage.setItem(USER_KEY, JSON.stringify(session.user));
}

export function readSession(): AuthSession | null {
  const serialized = window.sessionStorage.getItem(USER_KEY);
  if (!serialized) return null;
  try {
    return { user: JSON.parse(serialized) as AuthSession["user"] };
  } catch {
    clearSession();
    return null;
  }
}

export function clearSession(): void {
  window.localStorage.removeItem(USER_KEY);
  window.sessionStorage.removeItem(USER_KEY);
}

function safeError(status: number, path: string): ApiError {
  if (status === 409 && path.endsWith("/register")) {
    return new ApiError("An account with that email already exists.", status);
  }
  if (status === 401 && path.endsWith("/login")) {
    return new ApiError("Invalid email or password.", status);
  }
  if (status === 400 && path.endsWith("/reset-password")) {
    return new ApiError("This password reset link is invalid, expired, or already used.", status);
  }
  if (status === 400 && path.endsWith("/change-password")) {
    return new ApiError("Current password is incorrect, or the new password is not acceptable.", status);
  }
  if (status === 429) return new ApiError("Too many attempts. Please wait before trying again.", status);
  if (status === 503 && path.endsWith("/forgot-password")) {
    return new ApiError("Password reset email is temporarily unavailable. Please contact your administrator.", status);
  }
  const messages: Record<number, string> = {
    400: "The request could not be understood. Check the submitted information.",
    401: "Your session is missing or has expired. Please sign in again.",
    403: "You do not have permission to perform this action.",
    404: "This feature or item is not available on the connected backend.",
    413: "The submitted content is too large.",
    422: "Some submitted information is invalid. Check the fields and try again.",
  };
  const message = messages[status] ?? (status >= 500 ? "The service is temporarily unavailable." : "The request could not be completed.");
  return new ApiError(message, status, `Request failed with HTTP ${status}. Sensitive server details are hidden.`);
}

function buildHeaders(options: RequestInit, isForm: boolean): Headers {
  const headers = new Headers(options.headers);
  if (!isForm && options.body !== undefined && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  return headers;
}

async function request<T>(path: string, options: RequestInit = {}, isForm = false): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: buildHeaders(options, isForm),
      credentials: "include",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Unable to reach the service. Check your connection and try again.", 0);
  }

  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    window.dispatchEvent(new CustomEvent("coding-validation:session-expired", { detail: { from: window.location.pathname } }));
  }
  if (!response.ok) throw safeError(response.status, path);
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) return await response.json() as T;
  return await response.text() as T;
}

function postWithProgress<T>(path: string, body: unknown, onProgress: (percent: number) => void, signal?: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const serialized = JSON.stringify(body);
    xhr.open("POST", `${API_BASE_URL}${path}`);
    xhr.withCredentials = true;
    buildHeaders({ method: "POST", body: serialized }, false).forEach((value, key) => xhr.setRequestHeader(key, value));
    xhr.timeout = 120_000;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
    };
    xhr.onload = () => {
      if (xhr.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new CustomEvent("coding-validation:session-expired", { detail: { from: window.location.pathname } }));
      if (xhr.status < 200 || xhr.status >= 300) { reject(safeError(xhr.status, path)); return; }
      if (xhr.status === 204) { resolve(undefined as T); return; }
      const contentType = xhr.getResponseHeader("content-type") ?? "";
      if (contentType.includes("application/json")) {
        try { resolve(JSON.parse(xhr.responseText) as T); }
        catch { reject(new ApiError("The service returned an unreadable response.", xhr.status)); }
      } else resolve(xhr.responseText as T);
    };
    xhr.onerror = () => reject(new ApiError("Unable to reach the service. Check your connection and try again.", 0));
    xhr.ontimeout = () => reject(new ApiError("The service did not accept the validation request before the timeout.", 0));
    xhr.onabort = () => reject(new DOMException("The request was cancelled.", "AbortError"));
    if (signal?.aborted) { xhr.abort(); return; }
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(serialized);
  });
}

export const apiClient = {
  get: <T>(path: string, signal?: AbortSignal): Promise<T> => request<T>(path, { method: "GET", signal }),
  post: <T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> => request<T>(path, { method: "POST", body: JSON.stringify(body), signal }),
  postWithProgress: <T>(path: string, body: unknown, onProgress: (percent: number) => void, signal?: AbortSignal): Promise<T> => postWithProgress<T>(path, body, onProgress, signal),
  patch: <T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> => request<T>(path, { method: "PATCH", body: JSON.stringify(body), signal }),
  delete: <T>(path: string, signal?: AbortSignal): Promise<T> => request<T>(path, { method: "DELETE", signal }),
  postForm: <T>(path: string, body: FormData, signal?: AbortSignal): Promise<T> => request<T>(path, { method: "POST", body, signal }, true),
};

export function apiBaseUrl(): string {
  return API_BASE_URL;
}

export function dispatchWorkspaceClear(): void {
  window.dispatchEvent(new Event("coding-validation:clear-user-data"));
}
