# Headless browser, private input, OTP and passkeys

Fluxyr can run a local headless Chrome through **public-browser 3.0.0**. Browser
tools are optional. The normal Python package does not start Chrome or require
Node. The controller and Chrome communicate over inherited pipes: no MCP server,
HTTP Script API, or listening Chrome debugging port is used.

## Install locally

Install Node.js 20+ and Google Chrome/Chromium, then, in your application directory:

```sh
python -m fluxyr.browser install
```

The installer uses pnpm with the bundled lockfile when available, otherwise npm
with the pinned public-browser version. It does not install Chrome. For a custom
Chromium executable, set `CHROME_PATH`.
Run the installer again after upgrading Fluxyr to update the private controller
alongside the Python package. Rebuild the optional Docker image for deployments.

Enable the feature in your application environment and restart Fluxyr:

```dotenv
FLUXYR_BROWSER_ENABLED=true
```

For an installation outside the application's `.runtime/browser-engine`:

```sh
python -m fluxyr.browser install --directory /opt/fluxyr-browser
```

```dotenv
FLUXYR_BROWSER_RUNTIME=/opt/fluxyr-browser
```

When working on the framework checkout, `pnpm --dir fluxyr/browser_runtime install
--frozen-lockfile` also works. The bundled development directory is discovered
automatically. Installation is explicit; serving requests never downloads code.

## Docker

Build the optional target, which adds Node and Chromium to the ordinary image:

```sh
docker build --target browser -t fluxyr-browser .
docker run --rm --init --shm-size=512m -p 5050:5050 \
  --env-file .env -v fluxyr-data:/var/lib/fluxyr fluxyr-browser
```

This target enables browser tools and sets the runtime/executable paths. The
default Docker target remains the Python-only image. Size memory limits for your
workload: Chromium adds processes and its memory consumption depends on the page.
The optional target runs Chromium with `--no-sandbox` as the non-root `agent` user:
it relies on the container boundary, because ordinary Docker restrictions prevent
Chrome from creating its internal namespaces. It does not require privileged mode
or extra capabilities. Use this target for the trusted, isolated application
environment described above. Local Chrome keeps its default sandbox.

## Vault authenticator

Create an **Authenticator (TOTP)** item in Vault or let the agent open its private
credential form. Enter a Base32 secret or an `otpauth://totp/...` URI. The defaults
are SHA1, six digits and a 30-second interval; SHA256/SHA512, eight digits and
15–120 second intervals are supported. URI settings are imported when supplied.
The seed and configuration use the existing Vault encryption. Listing items or
checking readiness does not return the seed or generated code.

Use descriptive names such as “Example Portal — Alice — Production OTP”. Password
items can use `key_password` (username in `key`, password in `password`) or `text`.
TOTP uses the host's clock, which must be synchronized. HOTP counters are not
supported; SMS/email codes use private human input instead.

## Agent tools

- `browser(tool="help")` lists supported browser operations. For the exact schema,
  request `browser(tool="help", arguments_json='{"tool":"navigate"}')`.
- `browser(tool="navigate", arguments_json='{"url":"https://portal.example.com"}')`
  opens a page. `view_page` returns text and element refs, and `capture_image`
  returns a file path. The agent can use `read` to inspect the image or
  `render_preview` to display it in the conversation. To embed it in Markdown,
  use the returned `url`, for example `![Page capture](/preview/browser/example.webp)`.
  PNG, JPEG and WebP are supported; relative Files paths in Markdown images also
  resolve through `/preview/`.
- `browser_fill_private(origin="https://portal.example.com", ref="e12",
  vault_item_id="...", field="password")` resolves the value inside Fluxyr and
  delivers it directly to the controller. No plaintext value is a tool argument
  or result. For a TOTP item, omit `field` or use `field="code"`.
- `browser_request_input(origin="https://portal.example.com", selector="#code",
  title="Enter the code received by SMS")` pauses for a private form. Passing a
  `vault_item_id` instead asks permission to use that saved item. Without this
  explicit permission request, Vault fills run automatically in the application's
  trusted environment.
- `browser(tool="close")` closes this conversation's Chrome and removes its
  temporary profile.

Supply exactly one field `ref` or unique CSS `selector`. The controller validates
the field's actual origin, document, tab, visibility and editability. A private
request holds the exact element, not a selector re-evaluated after the human
answers. Navigation, field replacement, expiry or a browser restart invalidates
it. Iframe/shadow-root refs use public-browser's element resolution.

The host releases the value only after the controller requests it. TOTP codes are
generated at this point; if less than five seconds remain, Fluxyr waits for the
next code. `submit=true` requests immediate submission of the containing form,
without waiting for another model response. Inspect the resulting page to confirm
success; filling or requesting submission does not prove authentication succeeded.

## Private human input and session routing

The card appears in the requesting session and also in its parent conversation
when an isolated builder requests input. It displays the destination origin and
whether the form will be submitted. The value goes through a dedicated endpoint,
not the ordinary `ask_human` answer. Only the outcome is persisted in decisions,
events and model context. Temporary input is not saved in Vault.

`GET /api/approvals` includes these requests with `kind=browser_private_input` and
a dedicated `submission_url`. POST `{"value":"..."}` to that URL for temporary
input, or `{}` to authorize the Vault item already bound to the card. Use its
`decision_url` with `{"decision":"reject"}` to decline. Both submission URL
forms enforce `FLUXYR_APPROVALS_API_KEY` when configured. Repeated submissions do
not fill again. Use HTTPS for human/API clients accessing a non-local deployment;
the existing outer authentication layer still protects the rest of the app.

Private requests expire after ten minutes or earlier if the browser closes. A
restart preserves the conversation and request metadata, but cannot restore a
live element or temporary value. An expired delivery resumes with a safe failure
so the agent can inspect the page and request fresh input. Uncertain effects are
not replayed automatically.

## Passkeys registered inside Fluxyr

Passkeys can be created directly by Chrome's virtual WebAuthn authenticator and
stored encrypted as browser-managed Vault items. No private key needs to be pasted
into a form, imported from another password manager or passed through model context.

1. Sign in to the account in Fluxyr's browser using its normal supported login flow.
2. Navigate to the site's account/security settings and locate its final Add passkey button.
3. Call `browser_register_passkey(origin="https://portal.example.com", selector="#add-passkey", suggested_name="Example — Alice — Production")`.
4. Confirm the destination and Vault name in the embedded card. Only then does
   Fluxyr activate the authenticator and click the registration button.
5. Inspect the site's result to confirm registration. `saved=true` means the key
   was encrypted in Vault; the website can still reject its registration response.

On later executions, navigate to the login screen and call
`browser_use_passkey(origin="https://portal.example.com", vault_item_id="...", selector="#passkey-login")`.
The authenticator is restored for that one operation, then removed. Login cookies
are still temporary, but the passkey survives browser closure and server restart.
Signature counters are reserved in Vault before assertions, avoiding reuse after
an interrupted operation. Inspect the resulting page: a signed assertion alone
does not confirm the site's login succeeded.

The exact origin is bound to the saved credential. Registration requests expire
after ten minutes or browser restart and bind the exact button/document/tab.
The key is handled only by Chrome, the local controller and encrypted Vault storage;
tool results contain metadata and outcome only. Vault permits renaming/deletion,
not viewing or manually replacing the key. Deleting it locally does not revoke
the registration on the website.

If persistence fails after creation, `saved=false, recoverable=true` means the
controller temporarily retains the credential. Keep that browser open and call
`browser_register_passkey(origin="...", vault_item_id="...")` without a button
to confirm retrying the save without creating another credential. If the process
dies before storage, the key cannot be recovered; inspect/revoke that incomplete
registration on the site before enrolling again. A pending Vault item is not usable
for login.

This is a software authenticator intended for trusted automation, not hardware
attestation or biometric verification. Services requiring approved hardware or a
specific authenticator may reject it. This version handles explicit WebAuthn
buttons and resident credentials; it does not import existing phone/security-key
passkeys or emulate native platform account-selection dialogs.

The approval API reports `kind=browser_passkey`. POST `{"name":"Example account"}`
to its `submission_url` to confirm, or reject through `decision_url`. The same
approval-key protection applies as for private browser input.

## Boundaries and resources

This feature targets trusted application tools in an isolated deployment, not a
hostile-code sandbox. The destination website and the local runtime necessarily
receive the value. Known private strings are redacted from subsequent textual
browser responses. **Explicit screenshots can show visible fields, including
OTP**, and page scripts can transform values; this is not a guarantee against
malicious pages, arbitrary code or an administrator of the host. Network/console
collectors and generic batched plans are not exposed by this integration.

| Environment variable | Default |
| --- | --- |
| `FLUXYR_BROWSER_ENABLED` | `false` |
| `FLUXYR_BROWSER_RUNTIME` | Bundled development install, or `<root>/.runtime/browser-engine` |
| `FLUXYR_BROWSER_NODE` | `node` from PATH |
| `CHROME_PATH` | Chrome/Chromium discovery by public-browser |
| `FLUXYR_BROWSER_MAX_SESSIONS` | `2` |
| `FLUXYR_BROWSER_IDLE_SECONDS` | `600` (minimum 60) |

Each conversation gets its own browser process/profile, created only on demand.
Operations on a browser are serialized; different conversations may run up to the
configured limit. Idle browsers close automatically without database polling.
Profiles are temporary, so login cookies do not survive closure. Downloads and
explicit screenshots are retained under `data/browser` and available in Files.
The first implementation supports local pipes only, not remote TCP controllers.

## Validation

```sh
pytest -q tests/test_browser_private.py
FLUXYR_TEST_BROWSER=1 pytest -q -m integration tests/test_browser_chrome.py
```

The second command requires the installed controller and Chrome. It uses a local
test website, not real credentials. It verifies private fill, redacted text,
headless capture, pipe transport, TOTP submission and stale-handle rejection.
It also exercises the private-input card through the actual web UI. The release
integration workflow builds the optional Docker target and runs these tests in
Linux; ordinary fast CI does not require Chrome.
