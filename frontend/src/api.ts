export type RecordData = Record<string, any>;
export async function api(
  path: string,
  method = "GET",
  data?: unknown,
): Promise<any> {
  const result = await fetch("/api" + path, {
    method,
    headers: data ? { "Content-Type": "application/json" } : {},
    body: data ? JSON.stringify(data) : undefined,
  });
  const body = await result.json();
  if (!result.ok) throw new Error(body.error || "Request failed");
  return body;
}
export const mainSession = "00000000-0000-0000-0000-000000000001";
export function date(value: number) {
  return new Date(value * 1000).toLocaleString();
}
