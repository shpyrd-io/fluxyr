// One local process per browser. JSON lines on inherited pipes, no TCP/MCP transport.
import readline from "node:readline";
import { createSessionCore } from "public-browser/lib/session-core.js";
import { resolveElement } from "public-browser/tools/element-utils.js";

const send = (message) => process.stdout.write(JSON.stringify(message) + "\n");
// Dependency diagnostics must never corrupt the protocol or record private CDP arguments.
console.log = console.info = console.warn = console.error = () => {};
const core = createSessionCore({
  headless: true,
  transport: "pipe",
  stealth: false,
  userDataDir: process.env.FLUXYR_BROWSER_PROFILE,
  downloadDir: process.env.FLUXYR_BROWSER_DOWNLOADS,
});
const privateValues = new Set();
const targets = new Map();
const secrets = new Map();
let sequence = 0;
let closing = false;
const allowed = new Set([
  "virtual_desk",
  "view_page",
  "navigate",
  "click",
  "type",
  "fill_form",
  "press_key",
  "scroll",
  "drag",
  "switch_tab",
  "tab_status",
  "wait_for",
  "observe",
  "capture_image",
  "dom_snapshot",
  "handle_dialog",
  "file_upload",
  "download",
  "evaluate",
]);

function redact(value) {
  if (typeof value === "string") {
    for (const secret of privateValues)
      value = value.split(secret).join("[private]");
    return value;
  }
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, k === "data" ? v : redact(v)]),
    );
  return value;
}
async function onElement(target, fn, args = []) {
  const result = await core.browserSession.cdpClient.send(
    "Runtime.callFunctionOn",
    {
      objectId: target.objectId,
      functionDeclaration: fn,
      arguments: args.map((value) => ({ value })),
      returnByValue: true,
      silent: true,
    },
    target.resolvedSessionId,
  );
  if (result.exceptionDetails) throw new Error("field_changed");
  return result.result?.value;
}
const fieldInfo = `function() {
  const w = this.ownerDocument.defaultView;
  return {origin:w.location.origin, url:w.location.href, connected:this.isConnected,
    editable: (this instanceof w.HTMLInputElement || this instanceof w.HTMLTextAreaElement)
      && !this.disabled && !this.readOnly && this.type !== 'hidden',
    visible:!!this.getClientRects().length};
}`;
async function validate(target) {
  if (
    Date.now() > target.expires ||
    core.browserSession.sessionId !== target.tab
  )
    throw new Error("request_expired");
  const info = await onElement(target, fieldInfo);
  if (
    !info?.connected ||
    !info.editable ||
    !info.visible ||
    info.origin !== target.origin ||
    info.url !== target.url
  )
    throw new Error("field_changed");
}
async function command(msg) {
  if (msg.op === "close") {
    await shutdown();
    return { closed: true };
  }
  if (msg.op === "status")
    return { running: core.browserSession.isReady, transport: core.transport };
  if (msg.op === "call") {
    if (!allowed.has(msg.tool)) throw new Error("unsupported_tool");
    const result = await core.callTool(msg.tool, msg.args || {});
    if (
      result.isError &&
      JSON.stringify(result).includes("--remote-debugging-port")
    ) {
      return {
        isError: true,
        content: [
          {
            type: "text",
            text: "Chrome could not start over its private pipe. Check Chrome installation, CHROME_PATH and Linux sandbox permissions. Do not open a debugging port.",
          },
        ],
      };
    }
    // Only explicit capture_image produces images. Ambient observations stay textual.
    if (msg.tool !== "capture_image" && result.content)
      result.content = result.content.filter((c) => c.type !== "image");
    return redact(result);
  }
  if (msg.op === "prepare") {
    await core.start();
    for (const [id, old] of targets)
      if (Date.now() > old.expires) {
        targets.delete(id);
        await core.browserSession.cdpClient
          .send(
            "Runtime.releaseObject",
            { objectId: old.objectId },
            old.resolvedSessionId,
          )
          .catch(() => {});
      }
    if (targets.size >= 64) throw new Error("too_many_requests");
    const browser = core.browserSession;
    const element = await resolveElement(
      browser.cdpClient,
      browser.sessionId,
      msg.target,
      browser.sessionManager,
    );
    const info = await onElement(element, fieldInfo);
    if (
      !info?.connected ||
      !info.editable ||
      !info.visible ||
      info.origin !== msg.origin
    )
      throw new Error("wrong_destination");
    const id = String(++sequence);
    const target = {
      ...element,
      ...info,
      tab: browser.sessionId,
      expires: Date.now() + 600000,
    };
    targets.set(id, target);
    return {
      target_id: id,
      origin: info.origin,
      expires_at: target.expires / 1000,
    };
  }
  if (msg.op === "fill") {
    const target = targets.get(msg.target_id);
    targets.delete(msg.target_id); // single use even after an uncertain failure
    if (!target) throw new Error("request_expired");
    try {
      await validate(target);
      const value = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          secrets.delete(msg.id);
          reject(new Error("secret_timeout"));
        }, 15000);
        secrets.set(msg.id, { resolve, reject, timer });
        send({ type: "secret_request", id: msg.id });
      });
      if (typeof value !== "string" || !value || value.length > 10000)
        throw new Error("private_input_unavailable");
      privateValues.add(value);
      // Bound retained redaction state by ending the browser rather than forgetting a password.
      if (privateValues.size > 256) {
        await shutdown();
        throw new Error("session_limit");
      }
      await validate(target);
      const filled = await onElement(
        target,
        `function(value, origin) {
      const w = this.ownerDocument.defaultView;
      if (!this.isConnected || w.location.origin !== origin || this.disabled || this.readOnly) return false;
      const proto = this instanceof w.HTMLTextAreaElement ? w.HTMLTextAreaElement.prototype : w.HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(this, value);
      this.dispatchEvent(new w.Event('input', {bubbles:true}));
      this.dispatchEvent(new w.Event('change', {bubbles:true}));
      return true;
    }`,
        [value, target.origin],
      );
      if (!filled) throw new Error("field_changed");
      if (msg.submit) {
        const submitted = await onElement(
          target,
          `function(origin) {
        if (!this.isConnected || this.ownerDocument.defaultView.location.origin !== origin || !this.form) return false;
        this.form.requestSubmit(); return true;
      }`,
          [target.origin],
        );
        return { filled: true, submitted: !!submitted };
      }
      return { filled: true, submitted: false };
    } finally {
      await core.browserSession.cdpClient
        .send(
          "Runtime.releaseObject",
          { objectId: target.objectId },
          target.resolvedSessionId,
        )
        .catch(() => {});
    }
  }
  throw new Error("unsupported_operation");
}
async function shutdown() {
  if (closing) return;
  closing = true;
  for (const s of secrets.values()) {
    clearTimeout(s.timer);
    s.reject(new Error("closed"));
  }
  secrets.clear();
  targets.clear();
  privateValues.clear();
  await core.close();
}
let chain = Promise.resolve();
const input = readline.createInterface({ input: process.stdin });
input.on("line", (line) => {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    return;
  }
  if (msg.type === "secret_value") {
    const pending = secrets.get(msg.id);
    if (pending) {
      secrets.delete(msg.id);
      clearTimeout(pending.timer);
      pending.resolve(msg.value);
    }
    return;
  }
  chain = chain.then(async () => {
    try {
      send({ id: msg.id, result: await command(msg) });
    } catch (error) {
      // No raw CDP, page or dependency errors on the private path.
      const known = new Set([
        "field_changed",
        "request_expired",
        "wrong_destination",
        "unsupported_tool",
        "too_many_requests",
        "secret_timeout",
        "private_input_unavailable",
        "session_limit",
      ]);
      send({
        id: msg.id,
        error: known.has(error.message)
          ? error.message
          : "browser_operation_failed",
      });
    }
  });
});
input.on("close", () => shutdown().finally(() => process.exit(0)));
process.on("SIGTERM", () => shutdown().finally(() => process.exit(0)));
send({ type: "ready", version: "3.0.0" });
