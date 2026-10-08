// WebAuthn credentials stay on the private host pipe, never in tool responses.
export function passkeys({ core, onElement, requestPrivate }) {
  const pending = new Map();
  const cdp = () => core.browserSession.cdpClient;
  const send = (method, params, session) => cdp().send(method, params, session);

  async function persist(msg, record) {
    const saved = await requestPrivate(msg, "passkey_store", { credential: record.credential });
    if (saved !== true) return { saved: false, recoverable: true };
    pending.delete(msg.vault_item_id);
    return { saved: true };
  }

  async function run(msg, target, validate) {
    if (msg.op === "passkey_recover") {
      const record = pending.get(msg.vault_item_id);
      if (!record || record.origin !== msg.origin) throw new Error("request_expired");
      return persist(msg, record);
    }
    if (pending.size >= 16) throw new Error("session_limit");
    await validate(target);
    const session = target.resolvedSessionId;
    await send("WebAuthn.enable", { enableUI: false }, session);
    const { authenticatorId } = await send("WebAuthn.addVirtualAuthenticator", {
      options: {
        protocol: "ctap2", transport: "internal", hasResidentKey: true,
        hasUserVerification: true, isUserVerified: true,
        automaticPresenceSimulation: true,
      },
    }, session);
    let listener, timer;
    const event = msg.op === "passkey_register" ? "WebAuthn.credentialAdded" : "WebAuthn.credentialAsserted";
    try {
      if (msg.op === "passkey_authenticate") {
        const credential = await requestPrivate(msg, "secret_request");
        await send("WebAuthn.addCredential", { authenticatorId, credential }, session);
      }
      await validate(target);
      const captured = new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error("passkey_timeout")), 45000);
        listener = ({ authenticatorId: id, credential }) => {
          if (id !== authenticatorId) return;
          clearTimeout(timer);
          cdp().off(event, listener);
          // Retain until the host confirms durable encryption/storage.
          const record = { credential, origin: msg.origin };
          pending.set(msg.vault_item_id, record);
          resolve(record);
        };
        cdp().on(event, listener, session);
      });
      // Attach a rejection handler before the click; no unhandled timeout rejection.
      captured.catch(() => {});
      await onElement(target, `function() {
        if (!this.isConnected || this.disabled) throw new Error('changed');
        this.click();
      }`, [], true);
      const record = await captured;
      await send("WebAuthn.setAutomaticPresenceSimulation", { authenticatorId, enabled: false }, session);
      return {
        ...await persist(msg, record),
        asserted: msg.op === "passkey_authenticate",
      };
    } finally {
      clearTimeout(timer);
      if (listener) cdp().off(event, listener);
      await send("WebAuthn.removeVirtualAuthenticator", { authenticatorId }, session).catch(() => {});
    }
  }
  return { run, clear: () => pending.clear() };
}
