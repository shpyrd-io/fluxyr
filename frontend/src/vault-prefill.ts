// Only public configuration can initialize an agent-requested credential form.
export function oauthPrefill(config: Record<string, unknown> = {}) {
  const content: Record<string, string> = {
    grant_type: "authorization_code",
    token_auth_method: "client_secret_post",
    certificate_id: "",
  };
  for (const key of [
    "grant_type",
    "token_auth_method",
    "certificate_id",
    "token_url",
    "authorization_url",
    "scope",
  ]) {
    if (typeof config[key] === "string" && config[key]) {
      content[key] = config[key];
    }
  }
  return content;
}
