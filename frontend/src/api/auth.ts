import { apiClient } from "./client";
import type { AuthSession } from "./types";

export interface RegisterRequest {
  readonly email: string;
  readonly full_name: string;
  readonly password: string;
}

export interface LoginRequest {
  readonly email: string;
  readonly password: string;
  readonly remember: boolean;
}

export interface ResetPasswordRequest {
  readonly token: string;
  readonly new_password: string;
}

export interface ChangePasswordRequest {
  readonly current_password: string;
  readonly new_password: string;
}

export async function register(request: RegisterRequest, signal?: AbortSignal): Promise<AuthSession> {
  return apiClient.post<AuthSession>("/api/auth/register", request, signal);
}

export async function login(request: LoginRequest, signal?: AbortSignal): Promise<AuthSession> {
  return apiClient.post<AuthSession>("/api/auth/login", request, signal);
}

export async function loginLocalDemo(signal?: AbortSignal): Promise<AuthSession> {
  return apiClient.post<AuthSession>("/api/auth/demo", {}, signal);
}

export async function getCurrentUser(signal?: AbortSignal): Promise<AuthSession["user"]> {
  const session = await apiClient.get<AuthSession>("/api/auth/me", signal);
  return session.user;
}

export async function logout(signal?: AbortSignal): Promise<void> {
  await apiClient.post<void>("/api/auth/logout", {}, signal);
}

export async function requestPasswordReset(email: string, signal?: AbortSignal): Promise<{ message: string }> {
  return apiClient.post<{ message: string }>("/api/auth/forgot-password", { email }, signal);
}

export async function resetPassword(request: ResetPasswordRequest, signal?: AbortSignal): Promise<{ message: string }> {
  return apiClient.post<{ message: string }>("/api/auth/reset-password", request, signal);
}

export async function changePassword(request: ChangePasswordRequest, signal?: AbortSignal): Promise<AuthSession & { message: string }> {
  return apiClient.post<AuthSession & { message: string }>("/api/auth/change-password", request, signal);
}
