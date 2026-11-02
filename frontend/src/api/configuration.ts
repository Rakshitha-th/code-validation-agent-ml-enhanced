import { apiClient, mocksEnabled } from "./client";

export type LlmProvider = "Ollama" | "OpenAI" | "Claude";
export type NetworkPolicy = "restricted" | "off";

export interface AnalysisCheckOptions {
  readonly codeQuality: boolean;
  readonly security: boolean;
  readonly bugResource: boolean;
  readonly complexity: boolean;
  readonly automatedRepair: boolean;
  readonly finalValidation: boolean;
  readonly aiReportRecommendations: boolean;
}

export const DEFAULT_ANALYSIS_CHECKS: AnalysisCheckOptions = {
  codeQuality: true,
  security: true,
  bugResource: true,
  complexity: true,
  automatedRepair: true,
  finalValidation: true,
  aiReportRecommendations: true,
};

export interface AnalysisConfiguration {
  readonly sandbox_timeout_seconds: number;
  readonly memory_limit_mb: number;
  readonly cpu_limit: number;
  readonly network_policy: NetworkPolicy;
  readonly llm_provider: LlmProvider;
  readonly model_name: string;
}

export interface LlmConnectionResult {
  readonly reachable: boolean;
  readonly message?: string;
}

export const DEFAULT_ANALYSIS_CONFIGURATION: AnalysisConfiguration = {
  sandbox_timeout_seconds: 60,
  memory_limit_mb: 512,
  cpu_limit: 1,
  network_policy: "restricted",
  llm_provider: "Ollama",
  model_name: "llama3.1",
};

export function validateAnalysisConfiguration(configuration: AnalysisConfiguration): boolean {
  return configuration.sandbox_timeout_seconds >= 1 && configuration.sandbox_timeout_seconds <= 600
    && configuration.memory_limit_mb >= 64 && configuration.memory_limit_mb <= 8192
    && configuration.cpu_limit >= 0.1 && configuration.cpu_limit <= 8
    && Boolean(configuration.model_name.trim());
}

export async function getAdminConfiguration(signal?: AbortSignal): Promise<Partial<AnalysisConfiguration> & { readonly api_key_configured?: boolean }> {
  if (mocksEnabled()) return DEFAULT_ANALYSIS_CONFIGURATION;
  return apiClient.get("/api/admin/config", signal);
}

export async function saveAdminConfiguration(configuration: AnalysisConfiguration, apiKey?: string, signal?: AbortSignal): Promise<void> {
  if (mocksEnabled()) return;
  await apiClient.patch<void>("/api/admin/config", { ...configuration, ...(apiKey ? { api_key: apiKey } : {}) }, signal);
}

export async function testLlmConnection(provider: LlmProvider, model: string, apiKey?: string, signal?: AbortSignal): Promise<LlmConnectionResult> {
  if (mocksEnabled()) return { reachable: provider === "Ollama" || Boolean(apiKey?.trim()), message: provider === "Ollama" || apiKey ? "Mock connection test completed." : "An API key is required for this provider." };
  return apiClient.post<LlmConnectionResult>("/api/llm/test-connection", { provider, model, ...(apiKey ? { api_key: apiKey } : {}) }, signal);
}