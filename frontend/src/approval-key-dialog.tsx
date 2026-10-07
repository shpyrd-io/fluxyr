import { useEffect, useState } from "react";
import { Button, Input } from "./components";
import { Label } from "./ui/label/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogBody,
} from "./ui/dialog/dialog";
import { approvalAuthEvent, type ApprovalKeyRequest } from "./approval-auth";

export function ApprovalKeyDialog() {
  const [request, setRequest] = useState<ApprovalKeyRequest | null>(null);
  const [key, setKey] = useState("");
  useEffect(() => {
    const listener = (event: Event) => {
      setKey("");
      setRequest((event as CustomEvent<ApprovalKeyRequest>).detail);
    };
    window.addEventListener(approvalAuthEvent, listener);
    return () => window.removeEventListener(approvalAuthEvent, listener);
  }, []);
  function close() {
    request?.reject(new Error("Approval authentication cancelled"));
    setRequest(null);
    setKey("");
  }
  return (
    <Dialog
      open={!!request}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Authorize approval</DialogTitle>
          <DialogDescription>
            This request requires a valid approval API key. It is kept only
            until this page is reloaded and is never sent to the agent.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {request?.invalid && (
            <p role="alert">Invalid approval key. Please try again.</p>
          )}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!key.trim()) return;
              request?.resolve(key.trim());
              setRequest(null);
              setKey("");
            }}
          >
            <Label htmlFor="approval-api-key">Approval API key</Label>
            <Input
              id="approval-api-key"
              type="password"
              autoComplete="off"
              value={key}
              onChange={(event) => setKey(event.target.value)}
            />
            <div className="actions">
              <Button onClick={close}>Cancel</Button>
              <Button type="submit" className="primary" disabled={!key.trim()}>
                Continue
              </Button>
            </div>
          </form>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
