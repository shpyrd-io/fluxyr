# Human interaction review

## Input limits

`ask_human` accepts a question of 1–500 characters and, optionally, 2–6 plain
string choices of 1–100 characters each. Labels must not be blank. Keep option
labels short and put explanations in the question or the preceding chat message.
These limits are included in the model-facing schema and checked before dispatch.

For Python action helpers, `key` and `title` accept up to 100 characters,
`message` up to 500, `placeholder` up to 200, and each choice label up to 100.
The complete interaction payload is limited to 64,000 bytes; put large preview
files under `data_dir`. User free-text responses accept up to 10,000 characters.

## Design background

The updated Fluxyr source (`fluxyr-work2/backend/prompts/skill_build.md` and `backend/res_templates/approval_helper.py`) describes interaction as a pause inside an action: choices, free text, or confirmation, followed by re-invocation of the same action with the decision and retained context. It supports up to five rounds. Previews can belong to selectable candidates; standalone render_preview is only presentation.

The initial Agent port diverged in four places:

- The local guide explicitly told the model to split multi-question flows into separate actions.
- The helper accepted one untyped reply, reused it for later questions, and the engine rejected a second wait after re-execution.
- Workbench test_action returned a waiting result without actually suspending the model.
- The human card rendered a textarea and option buttons together regardless of interaction type.

The existing choose_your_path and story_spinner definitions in the local instance reflect those instructions. They orchestrate prepare/ask_human/finish steps in conversation; story_spinner even uses its style choices as part of the topic question. Fixing the runtime does not rewrite existing skill definitions or active Python versions. Those artifacts need an explicit spec/instruction correction and rebuild before adopting a single-action flow. Existing versions remain callable.

## Restored local contract

The workbench specifies the whole interaction inside one cohesive action. The isolated builder writes request_input, request_choice, or request_confirmation calls with distinct stable keys. The action's schema describes initial parameters only. User answers and continuation are supplied privately by the runner.

The process exits at an unanswered helper. The persisted pending call includes its request, original candidates/context, and prior replies. A response is validated before scheduling. The same pinned action version and arguments run again; each earlier helper returns its own original response. The next new helper may suspend again. At most five interactions are accepted per invocation. All non-idempotent work belongs after the final interaction or behind explicit application checkpoints, since Python statements before a helper execute again on restart.

The original provider call ID remains stable. Each further round gets a fresh decision request ID so a stale click cannot answer the next question. Completed sibling results are preserved. The effect ledger remains authoritative for uncertain/completed execution outcomes. Cancellation and preflight rejection still block execution. An in-action rejection reaches the action's explicit declined branch.

Choice previews use local data paths. Small inline HTML is materialized in data/.previews and served through the existing local preview route. Original choice identity and context are preserved across re-execution, including randomly generated candidates. The UI renders exactly one input mode and validates selection/text; decline opens a separate optional reason field. HTML previews have a sandbox. Standalone render_preview does not wait for input.

Both candidate-test surfaces are covered: the workbench's test_action suspends its calling conversation; the resource API's test job suspends its isolated execution. Waiting is not a passing test and cannot activate a candidate.

## Verification

Private Vault requests use the same durable pause/resume protocol but a separate form submission route. `manage_vault_credential` accepts only metadata (including optional OAuth flow and certificate ID). The shared Vault form POSTs private values to `/api/jobs/<job_id>/vault/<request_id>`, which locks the waiting job, encrypts the credential, and records a metadata-only decision in one transaction. The generic decision route cannot approve a Vault save. Duplicate submissions do not rewrite the item, stale/cancelled requests cannot save, and cancellation discards arbitrary text. Parallel requests resume only after every pending response exists. No credential content is included in brain checkpoints, messages, tool outputs or events.

The parent workbench also displays credential requests from its persisted descendant jobs. This projection follows `input.parent_job_id`, excludes unrelated executions and keeps each response bound to the requesting job. The original Fluxyr tool name, `manage_vault_credential`, is retained for create/edit, with the existing Vault form shared between chat and resource dialogs.

Real-model private-form validation: `scripts/validate_live_vault.py --run` used OpenRouter MiniMax M3 to list/create and then edit one credential. Both private values were entered through the browser, both jobs completed, and the probe inspected outgoing provider inputs plus persisted state to assert that neither value leaked. Evidence: `.runtime/live-validation/vault_0fa42ce6a07f/report.json`. `tests/test_vault_oauth_mtls.py` separately uses a real local HTTPS endpoint requiring client authentication, verifying PEM/PFX, post/basic auth, renewal, stable references and explicit failure on a removed certificate; it never contacts a bank.

The real-model builder probe `scripts/validate_live_vault_builder.py --run` then used synthetic `key_password` and `certificate_pem` items with names containing spaces/accents. MiniMax discovered the actual metadata, built a single action, tested it with real local Python and activated it. Its source used `key`/`password` and `certificate`/`private_key`/`passphrase`, declared the same exact Vault names, and returned boolean availability only. No credential values reached provider requests or persisted conversation events. Evidence: `.runtime/live-validation/vault_build_9b0175f667ce/report.json`, active version `14f10593-8fbb-4e94-aa76-ffafb2d3c05a`.

The automated PostgreSQL suite runs real local Python subprocesses. The interaction tests exercise three rounds, original randomly generated candidate retention, local HTML retrieval, preflight followed by in-action questions, rejection before effects, blank/out-of-range/bool selection rejection, stale duplicate submissions, Engine replacement between rounds, concurrent isolated test jobs, workbench candidate testing, and the five-round limit. The timeline test confirms that answering one round does not prematurely complete the action node.

These deterministic tests validate the protocol. Real-model behavior is separately checked by scripts/validate_live_human_skill.py, which supplies only a natural-language request and allows the workbench and isolated builder to generate their own spec and source. Human responses are supplied through the UI in that disposable instance; no Python implementation or tool-call sequence is scripted into the model.

### Observed real-model run (2026-10-07)

OpenRouter MiniMax M3 built `revisao_cartao` in isolated schema `human_skill_79b19b8063bf`. The successful workbench job was `ce49a0cb-93a8-4985-b083-12da05dc22de`, builder `d5ae62d7-9c82-474c-beb4-b1e6580ba5c9`, and tested/activated version `ebdb5e6a-6e31-4ac9-be06-242a5fe47b78`. There was one action, with two candidate revisions produced by the builder.

The UI showed local Azul/Verde HTML previews, then a separate text input, then a confirmation displaying the retained selection and title. Selecting Verde and entering `Continuidade — ação única 🎲` produced that exact pair in `data/cartao_revisao.json`. The three decisions kept provider call ID `call_01a116639c5673aea9b00ecb` and used different request IDs. No model request occurred between those decisions. The workbench resumed after the action completed and activated the tested version.

A subsequent isolated test of the same generated code selected Azul, supplied a different title and rejected the final confirmation through the API. It returned `saved: false`; the existing JSON's SHA-256 stayed unchanged. This rejection check used explicit test inputs and no model calls; it is separate from the model-generated build and browser-driven successful path.

Earlier real-provider attempts failed with an internal error, an idle timeout and an upstream overload. The successful attempt routed the same model to OpenRouter's native MiniMax endpoint only in the probe; production routing was not changed. The first test_action call omitted params and received a validation error; the model retried with `params_json: "{}"`. These failures are retained, rather than counted as successful validation.

Evidence is stored locally under `.runtime/live-validation/human-skill-79b19b8063bf/` (full model/job report, generated source, rejection report and data) and `.runtime/ui-validation/human-*.png`. These ignored artifacts are not committed. Automated validation: 86 backend tests passed, 18 frontend tests passed, and the final focused continuation/engine run passed all 16 tests. The frontend production build also passed.
