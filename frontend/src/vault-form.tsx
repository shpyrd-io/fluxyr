import { useEffect, useState, type FormEvent } from "react";
import { api, type RecordData } from "./api";
import { Button, Input, Textarea } from "./components";
import { Label } from "./ui/label/label";
import { Separator } from "./ui/separator/separator";
import { SelectField, SelectOption } from "./form-select";
import { oauthPrefill } from "./vault-prefill";

export const vaultKinds: Record<string, string> = {
  totp: "Authenticator (TOTP)",
  text: "Secret text",
  key_password: "Key and password",
  access_token: "Access token",
  oauth2: "OAuth 2.0",
  certificate_pem: "PEM certificate",
  certificate_pfx: "PFX / PKCS#12 certificate",
};
type Field = {
  key: string;
  label: string;
  type?: string;
  required?: boolean;
  accept?: string;
};
const vaultFields: Record<string, Field[]> = {
  totp: [
    {
      key: "secret",
      label: "Base32 secret or otpauth:// URI",
      type: "password",
      required: true,
    },
    { key: "issuer", label: "Issuer (optional)" },
    { key: "account", label: "Account (optional)" },
    { key: "algorithm", label: "Algorithm (default SHA1)" },
    { key: "digits", label: "Digits (6 or 8; default 6)", type: "number" },
    { key: "period", label: "Period in seconds (default 30)", type: "number" },
  ],
  text: [
    { key: "value", label: "Secret value", type: "password", required: true },
  ],
  key_password: [
    { key: "key", label: "Key / username", required: true },
    { key: "password", label: "Password", type: "password", required: true },
  ],
  access_token: [
    {
      key: "access_token",
      label: "Access token",
      type: "password",
      required: true,
    },
  ],
  oauth2: [
    { key: "client_id", label: "Client ID", required: true },
    { key: "client_secret", label: "Client secret", type: "password" },
    {
      key: "authorization_url",
      label: "Authorization URL",
      type: "url",
      required: true,
    },
    { key: "token_url", label: "Token URL", type: "url", required: true },
    { key: "scope", label: "Scopes (space separated)" },
  ],
  certificate_pem: [
    {
      key: "certificate",
      label: "Certificate PEM",
      type: "pem",
      required: true,
      accept: ".pem,.crt,.cer",
    },
    {
      key: "private_key",
      label: "Private key PEM",
      type: "pem",
      required: true,
      accept: ".pem,.key",
    },
    { key: "passphrase", label: "Private key passphrase", type: "password" },
  ],
  certificate_pfx: [
    {
      key: "pfx_base64",
      label: "PFX / PKCS#12 file",
      type: "binary",
      required: true,
      accept: ".pfx,.p12",
    },
    { key: "passphrase", label: "Certificate password", type: "password" },
  ],
};

export function VaultForm({
  initial,
  editing = false,
  lockKind = false,
  onSave,
  onCancel,
  onBusyChange,
  saveLabel = "Save vault item",
}: {
  initial: RecordData;
  editing?: boolean;
  lockKind?: boolean;
  onSave: (body: RecordData) => Promise<void>;
  onCancel: () => void | Promise<void>;
  onBusyChange?: (busy: boolean) => void;
  saveLabel?: string;
}) {
  const selected = editing;
  const [values, setValues] = useState<RecordData>(() => ({
    name: initial.name || "",
    kind: initial.kind || "text",
    content:
      initial.kind === "oauth2" ? oauthPrefill(initial.oauth_config) : {},
  }));
  const [certificates, setCertificates] = useState<RecordData[]>([]);
  const [certificateError, setCertificateError] = useState("");
  const grant = values.content.grant_type || "authorization_code";
  const changedGrant =
    editing &&
    grant !== (initial.oauth_config?.grant_type || "authorization_code");
  useEffect(() => {
    if (values.kind !== "oauth2") return;
    let current = true;
    api("/vault")
      .then((items) => {
        if (current)
          setCertificates(
            items.filter((item: RecordData) =>
              ["certificate_pem", "certificate_pfx"].includes(item.type),
            ),
          );
      })
      .catch(() => {
        if (current)
          setCertificateError(
            "Could not load certificates. Reopen this form to retry.",
          );
      });
    return () => {
      current = false;
    };
  }, [values.kind]);
  const [fileNames, setFileNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (key: string, value: any) =>
    setValues((v) => ({ ...v, [key]: value }));
  const content = (key: string, value: string) =>
    setValues((v) => ({ ...v, content: { ...v.content, [key]: value } }));
  function setWorking(value: boolean) {
    setBusy(value);
    onBusyChange?.(value);
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setWorking(true);
    setError("");
    try {
      const entries = Object.fromEntries(
        Object.entries(values.content).filter(
          ([key, v]) => !editing || v !== "" || key === "certificate_id",
        ),
      );
      await onSave({ name: values.name, kind: values.kind, content: entries });
      setValues((v) => ({ ...v, content: {} }));
      setFileNames({});
    } catch (e: any) {
      setError(e.message);
    } finally {
      setWorking(false);
    }
  }
  async function cancel() {
    setWorking(true);
    setError("");
    try {
      await onCancel();
      setValues((v) => ({ ...v, content: {} }));
      setFileNames({});
    } catch (e: any) {
      setError(e.message);
    } finally {
      setWorking(false);
    }
  }
  async function upload(field: Field, file?: File) {
    if (!file) return;
    try {
      if (file.size > 2 * 1024 * 1024)
        throw new Error("Certificate file must be smaller than 2 MB.");
      if (field.type === "binary") {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = "";
        for (let offset = 0; offset < bytes.length; offset += 8192)
          binary += String.fromCharCode(
            ...bytes.subarray(offset, offset + 8192),
          );
        content(field.key, btoa(binary));
      } else content(field.key, await file.text());
      setFileNames((names) => ({ ...names, [field.key]: file.name }));
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }

  return (
    <form
      onSubmit={save}
      className="resource-form vault-form"
      autoComplete="off"
    >
      {editing && (
        <p className="muted">
          Leave fields blank to keep saved values. Upload a file to replace a
          certificate.
        </p>
      )}
      <fieldset
        disabled={busy}
        className={values.kind === "oauth2" ? "vault-oauth-fields" : ""}
      >
        <Label>
          Name
          <Input
            required
            value={values.name}
            maxLength={200}
            onChange={(e) => set("name", e.target.value)}
            autoComplete="off"
          />
        </Label>
        <Label>
          Credential type
          <SelectField
            disabled={!!selected || lockKind}
            value={values.kind}
            onValueChange={(value) => {
              set("kind", value);
              set(
                "content",
                value === "oauth2"
                  ? {
                      grant_type: "authorization_code",
                      token_auth_method: "client_secret_post",
                      certificate_id: "",
                    }
                  : {},
              );
              setFileNames({});
            }}
          >
            {Object.entries(vaultKinds).map(([k, v]) => (
              <SelectOption key={k} value={k}>
                {v}
              </SelectOption>
            ))}
          </SelectField>
        </Label>
        {values.kind === "oauth2" && (
          <Label className="vault-wide">
            OAuth flow
            <SelectField
              aria-label="OAuth flow"
              value={grant}
              onValueChange={(value) => content("grant_type", value)}
            >
              <SelectOption value="authorization_code">
                Authorization code · browser sign-in
              </SelectOption>
              <SelectOption value="client_credentials">
                Client credentials · server to server
              </SelectOption>
            </SelectField>
            {grant === "client_credentials" && (
              <small>
                Uses client ID + secret, optionally with mTLS. No authorization
                URL or browser sign-in.
              </small>
            )}
          </Label>
        )}
        {vaultFields[values.kind]
          ?.filter(
            (field) =>
              values.kind !== "oauth2" ||
              field.key !== "authorization_url" ||
              grant !== "client_credentials",
          )
          .map((field) => (
            <Label key={field.key}>
              {field.label}
              {["pem", "binary"].includes(field.type || "") ? (
                <>
                  <Input
                    type="file"
                    accept={field.accept}
                    aria-label={`Upload ${field.label}`}
                    required={
                      !selected &&
                      field.required &&
                      !values.content?.[field.key]
                    }
                    onChange={(e) => upload(field, e.target.files?.[0])}
                  />
                  {fileNames[field.key] && (
                    <small>{fileNames[field.key]} · ready to save</small>
                  )}
                  {field.type === "pem" && (
                    <Textarea
                      rows={4}
                      aria-label={field.label}
                      value={values.content?.[field.key] || ""}
                      onChange={(e) => content(field.key, e.target.value)}
                      placeholder={
                        selected
                          ? "Leave blank to keep saved file"
                          : "Or paste PEM content here"
                      }
                    />
                  )}
                </>
              ) : (
                <Input
                  type={field.type || "text"}
                  autoComplete="off"
                  required={
                    (!selected && field.required) ||
                    (values.kind === "oauth2" &&
                      field.key === "client_secret" &&
                      grant === "client_credentials" &&
                      (!editing || changedGrant)) ||
                    (field.key === "authorization_url" &&
                      changedGrant &&
                      grant === "authorization_code")
                  }
                  value={values.content?.[field.key] || ""}
                  onChange={(e) => content(field.key, e.target.value)}
                  placeholder={selected ? "Unchanged unless entered" : ""}
                />
              )}
            </Label>
          ))}
        {values.kind === "oauth2" && (
          <>
            <Label className="vault-wide">
              mTLS client certificate
              <SelectField
                aria-label="mTLS client certificate"
                value={values.content.certificate_id || ""}
                onValueChange={(value) => content("certificate_id", value)}
              >
                <SelectOption value="">No client certificate</SelectOption>
                {values.content.certificate_id &&
                  !certificates.some(
                    (c) => c.id === values.content.certificate_id,
                  ) && (
                    <SelectOption value={values.content.certificate_id}>
                      Selected certificate (unavailable)
                    </SelectOption>
                  )}
                {certificates.map((c) => (
                  <SelectOption key={c.id} value={c.id}>
                    {c.name} · {vaultKinds[c.type]}
                  </SelectOption>
                ))}
              </SelectField>
              <small>
                Uses a PEM or PFX item from Vault for the token request. Add the
                certificate to Vault first.
              </small>
              {certificateError && (
                <small role="alert">{certificateError}</small>
              )}
            </Label>
            <Label>
              Token authentication
              <SelectField
                value={values.content?.token_auth_method || ""}
                onValueChange={(value) => content("token_auth_method", value)}
              >
                <SelectOption value="">
                  {selected
                    ? "Keep current method"
                    : "Client secret in request body (default)"}
                </SelectOption>
                <SelectOption value="client_secret_post">
                  Client secret in request body
                </SelectOption>
                <SelectOption value="client_secret_basic">
                  HTTP Basic authentication
                </SelectOption>
              </SelectField>
            </Label>
          </>
        )}
      </fieldset>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <Separator />
      <div className="dialog-actions">
        <Button disabled={busy} onClick={cancel}>
          Cancel
        </Button>
        <Button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : saveLabel}
        </Button>
      </div>
    </form>
  );
}
