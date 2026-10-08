import {
  approvalKey,
  isApprovalPath,
  requestApprovalKey,
} from "./approval-auth";

export type RecordData = Record<string, any>;
const pendingReads = new Map<string, Promise<any>>();
export function api(
  path: string,
  method = "GET",
  data?: unknown,
): Promise<any> {
  if (method !== "GET") return request(path, method, data);
  const pending = pendingReads.get(path);
  if (pending) return pending;
  const read = request(path, method, data).finally(() =>
    pendingReads.delete(path),
  );
  pendingReads.set(path, read);
  return read;
}
async function request(
  path: string,
  method = "GET",
  data?: unknown,
): Promise<any> {
  while (true) {
    const headers: Record<string, string> = data
      ? { "Content-Type": "application/json" }
      : {};
    if (isApprovalPath(path) && approvalKey())
      headers.Authorization = `Bearer ${approvalKey()}`;
    const result = await fetch("/api" + path, {
      method,
      headers,
      body: data ? JSON.stringify(data) : undefined,
    });
    const body = await result.json();
    if (
      result.status === 401 &&
      body.code === "approval_auth_required" &&
      isApprovalPath(path)
    ) {
      await requestApprovalKey();
      continue;
    }
    if (!result.ok) throw new Error(body.error || "Request failed");
    return body;
  }
}
export const mainSession = "00000000-0000-0000-0000-000000000001";
export function date(value: number) {
  return new Date(value * 1000).toLocaleString();
}
