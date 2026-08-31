import type {
  DemoResetResponse,
  ConnectionCatalog,
  FileRoot,
  HealthStatus,
  Preferences,
  RunDetail,
  RunHistoryClearResponse,
  RunRecord,
  ToolCatalog,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `DayPilot API returned ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function createRun(goal: string): Promise<{ id: string }> {
  return request("/api/runs", { method: "POST", body: JSON.stringify({ request: goal }) });
}

export async function getRun(runId: string): Promise<RunDetail> {
  return request(`/api/runs/${runId}`);
}

export async function listRuns(): Promise<RunRecord[]> {
  return request("/api/runs?limit=100");
}

export async function decideRun(
  runId: string,
  decision: "approve" | "reject",
  feedback?: string,
): Promise<void> {
  await request(`/api/runs/${runId}/${decision}`, {
    method: "POST",
    body: JSON.stringify({ feedback: feedback || null }),
  });
}

export async function editRun(
  runId: string,
  feedback: string,
  planRevision: number,
): Promise<RunDetail> {
  return request(`/api/runs/${runId}/feedback`, {
    method: "POST",
    body: JSON.stringify({ feedback, plan_revision: planRevision }),
  });
}

export async function getTools(): Promise<ToolCatalog> {
  return request("/api/tools");
}

export async function getHealth(): Promise<HealthStatus> {
  return request("/health");
}

export async function getConnections(): Promise<ConnectionCatalog> {
  return request("/api/connections");
}

export async function startGoogleConnection(): Promise<{ authorization_url: string }> {
  return request("/api/connections/google/start", { method: "POST" });
}

export async function disconnectGoogle(): Promise<ConnectionCatalog> {
  return request("/api/connections/google/disconnect", { method: "POST" });
}

export async function startXConnection(): Promise<{ authorization_url: string }> {
  return request("/api/connections/x/start", { method: "POST" });
}

export async function disconnectX(): Promise<ConnectionCatalog> {
  return request("/api/connections/x/disconnect", { method: "POST" });
}

export async function listFileRoots(): Promise<FileRoot[]> {
  return request("/api/connections/files/roots");
}

export async function addFileRoot(path: string): Promise<FileRoot> {
  return request("/api/connections/files/roots", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export async function removeFileRoot(rootId: string): Promise<void> {
  await request(`/api/connections/files/roots/${encodeURIComponent(rootId)}`, { method: "DELETE" });
}

export async function getPreferences(): Promise<Preferences> {
  return request("/api/preferences");
}

export async function savePreferences(preferences: Preferences): Promise<Preferences> {
  return request("/api/preferences", {
    method: "PUT",
    body: JSON.stringify(preferences),
  });
}

export async function resetDemoWorkspace(): Promise<DemoResetResponse> {
  return request("/api/demo-workspace/reset", { method: "POST" });
}

export async function clearRunHistory(): Promise<RunHistoryClearResponse> {
  return request("/api/run-history/clear", { method: "POST" });
}
