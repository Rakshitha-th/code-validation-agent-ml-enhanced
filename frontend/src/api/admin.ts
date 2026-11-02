import { apiClient, mocksEnabled } from "./client";
import type { Role, User } from "./types";

export interface CreateUserRequest {
  readonly full_name: string;
  readonly email: string;
  readonly role: Role;
  readonly password: string;
}

export interface UpdateUserRequest {
  readonly full_name?: string;
  readonly email?: string;
  readonly role?: Role;
  readonly active?: boolean;
}

let mockUsers: User[] = [];
window.addEventListener("coding-validation:clear-user-data", () => { mockUsers = []; });

export async function getAdminUsers(signal?: AbortSignal): Promise<User[]> {
  if (mocksEnabled()) return mockUsers;
  const response = await apiClient.get<{ readonly items: User[] }>("/api/admin/users", signal);
  return response.items;
}

export async function createAdminUser(request: CreateUserRequest, signal?: AbortSignal): Promise<User> {
  if (mocksEnabled()) {
    const user: User = { ...request, id: `mock-user-${crypto.randomUUID()}`, active: true };
    mockUsers = [...mockUsers, user];
    return user;
  }
  return apiClient.post<User>("/api/admin/users", request, signal);
}

export async function updateAdminUser(userId: string, changes: UpdateUserRequest, signal?: AbortSignal): Promise<User> {
  if (mocksEnabled()) {
    const updated = mockUsers.map((user) => user.id === userId ? { ...user, ...changes } : user);
    mockUsers = updated;
    const user = updated.find((item) => item.id === userId);
    if (!user) throw new Error("User was not found in mock data.");
    return user;
  }
  return apiClient.patch<User>(`/api/admin/users/${encodeURIComponent(userId)}`, changes, signal);
}