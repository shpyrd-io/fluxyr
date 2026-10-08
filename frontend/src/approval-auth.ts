// The shared approval key lives only in this tab's memory, never in URLs or storage.
let key = "";
let pending: Promise<string> | undefined;
export const approvalAuthEvent = "fluxyr:approval-key-required";
export type ApprovalKeyRequest = {
  invalid: boolean;
  resolve: (value: string) => void;
  reject: (reason: Error) => void;
};
export function isApprovalPath(path: string) {
  return (
    /^\/approvals(?:\/|\?|$)/.test(path) ||
    /^\/jobs\/[^/]+\/(decisions|vault|private-input)\/[^/?]+(?:\?.*)?$/.test(
      path,
    )
  );
}
export function approvalKey() {
  return key;
}
export function requestApprovalKey(): Promise<string> {
  const invalid = !!key;
  key = "";
  if (!pending) {
    pending = new Promise<string>((resolve, reject) => {
      window.dispatchEvent(
        new CustomEvent<ApprovalKeyRequest>(approvalAuthEvent, {
          detail: { resolve, reject, invalid },
        }),
      );
    })
      .then((value) => {
        key = value;
        return value;
      })
      .finally(() => {
        pending = undefined;
      });
  }
  return pending;
}
