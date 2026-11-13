import { useState } from "react";
import { api, RecordData } from "./api";
import { Button, Input } from "./components";
import { Label } from "./ui/label/label";
import { SelectField, SelectOption } from "./form-select";

// The model settings: where the model comes from, the key, and the model
// itself, chosen from what the provider lists for the key. Saved through
// /api/settings; the key goes to the Vault. A value the environment fixes
// is shown but not editable.

const providerNames: Record<string, string> = {
  openrouter: "OpenRouter",
  anthropic: "Anthropic",
  openai: "OpenAI",
  custom: "Custom OpenAI- or Anthropic-compatible server",
};

const keysAt: Record<string, string> = {
  openrouter: "openrouter.ai/keys",
  anthropic: "console.anthropic.com",
  openai: "platform.openai.com/api-keys",
};

export function ModelSettingsForm({
  settings,
  onSaved,
}: {
  settings: RecordData;
  onSaved: (settings: RecordData) => void;
}) {
  const managed: Record<string, string> = settings.managed || {};
  const fixed = (field: string) => managed[field] === "environment";
  const [provider, setProvider] = useState<string>(settings.provider || "openrouter");
  const [model, setModel] = useState<string>(settings.model || "");
  const [endpoint, setEndpoint] = useState<string>(
    settings.provider === "custom" ? settings.provider_endpoint || "" : "",
  );
  const [format, setFormat] = useState<string>(
    settings.provider === "custom" ? settings.provider_format || "openai" : "openai",
  );
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const sameProvider = provider === settings.provider;
  const keyFixed = sameProvider && settings.key_source === "environment";
  const hasKey = sameProvider
    ? !!settings.key_source
    : !!settings.configured_keys?.[provider];
  const custom = provider === "custom";
  const canCheck = !busy && (custom ? !!endpoint : !!key || hasKey);

  const payload = () => ({
    provider,
    model,
    provider_endpoint: custom ? endpoint : "",
    provider_format: custom ? format : "",
    api_key: key || undefined,
  });

  const check = async () => {
    setBusy("check");
    setError("");
    setNotice("");
    try {
      const result = await api("/settings/check", "POST", payload());
      const list: string[] = result.models || [];
      setModels(list);
      if (!list.includes(model)) setModel(list[0] || "");
      setNotice(`The key works. ${providerNames[provider] || provider} lists ${list.length} models.`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const saved = await api("/settings", "POST", payload());
      setKey("");
      setNotice("Saved. Executions use these settings from now on.");
      onSaved(saved);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };

  const fixedNote = (field: string) =>
    fixed(field) ? <small>Set by {variableOf(field)} in the environment.</small> : null;

  return (
    <form onSubmit={save} className="resource-form model-settings" autoComplete="off">
      <fieldset disabled={!!busy}>
        <Label>
          Provider
          <SelectField
            value={provider}
            disabled={fixed("provider")}
            onValueChange={(value) => {
              setProvider(value);
              setModels([]);
              setKey("");
              setError("");
              setNotice("");
              if (value !== settings.provider) setModel("");
              else setModel(settings.model || "");
            }}
          >
            {(settings.providers || Object.keys(providerNames)).map((p: string) => (
              <SelectOption key={p} value={p}>
                {providerNames[p] || p}
              </SelectOption>
            ))}
          </SelectField>
          {fixedNote("provider")}
        </Label>
        {custom && (
          <>
            <Label>
              Endpoint
              <Input
                type="url"
                value={endpoint}
                readOnly={fixed("provider_endpoint")}
                placeholder="https://llm.example.com/v1"
                onChange={(e) => setEndpoint(e.target.value)}
              />
              <small>The API base URL of an OpenAI- or Anthropic-compatible server: Ollama, vLLM, LM Studio, a gateway.</small>
              {fixedNote("provider_endpoint")}
            </Label>
            <Label>
              Format
              <SelectField value={format} disabled={fixed("provider_format")} onValueChange={setFormat}>
                <SelectOption value="openai">OpenAI</SelectOption>
                <SelectOption value="anthropic">Anthropic</SelectOption>
              </SelectField>
              {fixedNote("provider_format")}
            </Label>
          </>
        )}
        <Label>
          API key
          <Input
            type="password"
            value={key}
            autoComplete="off"
            spellCheck={false}
            disabled={keyFixed}
            placeholder={keyFixed ? "Set in the environment" : hasKey ? "Saved; leave empty to keep it" : ""}
            onChange={(e) => setKey(e.target.value)}
          />
          <small>
            {keyFixed
              ? `${settings.key_name || "The key"} is set in the environment.`
              : custom
                ? "Leave empty when the server asks for none. Kept in the Vault, never shown again."
                : `From ${keysAt[provider] || "the provider"}. Kept in the Vault, never shown again.`}
          </small>
        </Label>
        <div className="model-settings-actions">
          <Button onClick={check} disabled={!canCheck}>
            {busy === "check" ? "Checking…" : "Check the key"}
          </Button>
        </div>
        <Label>
          Model
          {models.length ? (
            <SelectField value={model} disabled={fixed("model")} onValueChange={setModel} aria-label="Model">
              {(models.includes(model) || !model ? models : [model, ...models]).slice(0, 400).map((m) => (
                <SelectOption key={m} value={m}>
                  {m}
                </SelectOption>
              ))}
            </SelectField>
          ) : (
            <Input
              value={model}
              readOnly={fixed("model")}
              placeholder="Check the key to choose from the provider's list, or type a model name"
              onChange={(e) => setModel(e.target.value)}
            />
          )}
          <small>The model the agent answers with, as the provider names it.</small>
          {fixedNote("model")}
        </Label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {notice && <p className="muted">{notice}</p>}
        <div className="model-settings-actions">
          <Button className="primary" type="submit" disabled={!!busy || !model || fixed("model") && fixed("provider") && !key}>
            {busy === "save" ? "Saving…" : "Save"}
          </Button>
        </div>
      </fieldset>
    </form>
  );
}

function variableOf(field: string) {
  return (
    {
      provider: "FLUXYR_PROVIDER",
      model: "FLUXYR_MODEL",
      provider_endpoint: "FLUXYR_PROVIDER_ENDPOINT",
      provider_format: "FLUXYR_PROVIDER_FORMAT",
    } as Record<string, string>
  )[field];
}
