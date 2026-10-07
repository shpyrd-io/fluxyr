import assert from "node:assert/strict";
import test from "node:test";
import { oauthPrefill } from "./vault-prefill.ts";

test("public OAuth suggestions initialize editable form values, never credentials", () => {
  const config = {
    grant_type: "client_credentials",
    certificate_id: "certificate-id",
    token_url: "https://api.example.com/token",
    authorization_url: "https://api.example.com/authorize",
    scope: "weather.read",
    token_auth_method: "client_secret_basic",
    client_id: "private-client",
    client_secret: "private-secret",
    access_token: "private-token",
  };
  const content = oauthPrefill(config);
  assert.deepEqual(content, {
    grant_type: "client_credentials",
    certificate_id: "certificate-id",
    token_url: "https://api.example.com/token",
    authorization_url: "https://api.example.com/authorize",
    scope: "weather.read",
    token_auth_method: "client_secret_basic",
  });
  content.token_url = "https://api.example.com/corrected-token";
  assert.equal(config.token_url, "https://api.example.com/token");
});

test("forms without suggestions retain defaults and no fabricated endpoints", () => {
  assert.deepEqual(oauthPrefill(), {
    grant_type: "authorization_code",
    token_auth_method: "client_secret_post",
    certificate_id: "",
  });
});
