# RenderRepo Permutation Engine v0

A separate direct runner: no prompt rewriting, AI judging, installer changes, or
RenderLab deployment. Every command is `python -m renderrepo.permutation`.
The installer does not execute workflows. The permutation runner does, **only
after exact plan approval and a subsequent run/resume command**.

## Definition and approval

Supply an operator-authored experiment definition with an **API-format** workflow
and explicit `["node-id", "input-name"]` pairs. A binding may target several inputs
(e.g. both LoRA strengths). Multiple LoRAs use `lora:slot` and
`lora_strength:slot`. Reference slots use `reference:slot`. No title inference,
editor-to-API conversion, runtime node installation, or provider model discovery.

Each binding needs a fixed value, a variation, a reference selection, or the
shared `seeds` list. Unbound graph inputs remain exactly as supplied. Bind scalar
inputs upstream of graph connections; v0 refuses to replace connections. CFG,
samplers, schedulers, LoRA names/strengths, dimensions and positive/negative
conditioning are not inferred. Comfy validates runtime class/model compatibility;
local validation checks the explicit graph/input existence and setting types.
LoRA `"off"` has explicit semantics: keep the original concrete filename and set
**all explicitly bound effect strengths** for that LoRA slot to zero. Bind both
model and CLIP strengths where present; an incomplete binding fails planning.
Requested settings and effective settings are both retained. Other selection
strings are literal model filenames, not inferred states. Existing approved
legacy Off plans are readable, but unsubmitted jobs need a new Plan/Approve cycle;
accepted jobs are reconciled without rewriting their original submission.

Reference selections can be explicit lists or `{ "directory": "path" }`.
Directory walking is recursive, sorted by path and limited to PNG/JPEG/WebP.
Slots form independent Cartesian axes (not zipped pairs). Axis names sort
lexically, selection order is preserved, and shared seeds are the innermost axis.
For every configuration, the same explicit seed set runs in the same order.
Duplicate seeds/axis values/jobs are rejected. Reference bytes are snapshotted
and hashed; deleting or changing an original source does not change execution.

The entire canonical plan determines the experiment ID (SHA-256). Job IDs hash
the exact local compiled graph and settings; unchanged plans are reused, not
reset. The original workflow file is retained byte-for-byte, with both file and
canonical graph hashes. Changed workflow, bindings, sources, settings, provider,
estimate or limits creates a new experiment requiring new approval. No old
approval expands to a new plan.

```
python -m renderrepo.permutation plan experiment.json
python -m renderrepo.permutation approve PLAN_HASH --plan-hash PLAN_HASH
python -m renderrepo.permutation run PLAN_HASH
```

Plan prints every job's settings, complete count, native-unit **hypothetical**
estimate/basis and all limits. Planning/approval never instantiate a provider,
upload assets or resolve credentials. Spend limits enforce hypothetical total
only; they are not a guarantee about actual provider billing. Choose conservative
estimates. v0 has no authoritative per-job cost API available in the reused path.

## Durable execution and controls

Default store: `~/.local/share/renderrepo/permutations`. Every command accepts
`--store PATH` **before** the command. Use one store for the account; copied stores
are not a distributed scheduler. Store permissions should be private (`umask 077`).
State contains workflows, prompts and reference images needed for provenance.
No provider key is persisted. Shared installation-history atomic writes fsync
files and their parent directories; nonblocking file leases fence workers.
A store-wide runner lease prevents concurrent experiments and duplicate workers.
Unresolved work in another experiment blocks new submissions in this store.

The CLI is the worker: it runs in the foreground, independent of the review page.
Closing/refreshing the review browser never cancels it. A terminal disconnect or
process restart interrupts the worker; use `resume`, optionally `tmux` for terminal
persistence. No automatic rendering restarts on reboot. One provider render is
accepted at a time, including when its outputs need recovery.

```
python -m renderrepo.permutation pause PLAN_HASH
python -m renderrepo.permutation stop PLAN_HASH
python -m renderrepo.permutation resume PLAN_HASH
python -m renderrepo.permutation status PLAN_HASH
```

Pause/Stop finish the current accepted job and prevent additional submissions.
Pause requested is distinct from paused. Resume explicitly clears that request.
Both commands can run from another terminal or the review page. The elapsed
limit retains a durable monotonic elapsed floor and durable wall time **since the first execution start, including pauses and
offline time**. At the limit, no new POST is permitted. An already accepted job
is checked/retrieved on resume even over budget; if still running, the worker
halts for later reconciliation. The limit cannot cancel work already accepted
by the provider. An active clock rollback and subsequent worker restart cannot
restore consumed budget. HTTP request timeout means a hard wall deadline can be exceeded
by the current network call. Consecutive billable failures trigger the approved
failure threshold. No render failures are automatically retried; failed and
completed jobs remain terminal. Preparation failures may be retried by Resume
because no rendering POST occurred.

Submission tokens are stable per experiment/job, rather than shared across
separately approved experiments. Before a billable POST, the exact uploaded-asset
wire graph, its hash and durable
submission intent are written. The acknowledgement ID is persisted before polling.
Restart reconciles accepted IDs first, retrieves outputs without rerendering, then
continues planned jobs. A timeout, process death around POST, or invalid
acknowledgement leaves **ambiguous** state, blocking remaining jobs. Neither an
idempotency token nor a provider 404 is treated as proof that no charge occurred.
There is no automatic ambiguous-submission reset. If the operator independently
finds the accepted UUID, associate it explicitly, then resume:

```
python -m renderrepo.permutation associate PLAN_HASH JOB_HASH --confirmed-provider-id PROVIDER_UUID
python -m renderrepo.permutation resume PLAN_HASH
```

If acceptance cannot be established, keep the experiment unresolved and inspect
provider records/support. Do not copy it into a new store to bypass the fence.
Provider asset expiry may make old accepted outputs unretrievable; v0 will retain
the evidence and require reconciliation rather than spend again silently.
Corrupt job entries remain visible; execution blocks until repaired. A damaged
empty sibling experiment is reported and isolated so it does not break valid
experiments. Corrupt records with unfinished/unknown submission evidence still
block new work conservatively. Completed experiments are a local no-op on Run/Resume
and require no credential resolution or provider call.

## Provenance, review and export

Each job directory retains `state.json`, `submitted-workflow.json` and atomic
output files/hashes. It records all fixed/varied settings, reference hashes and
slots, seed, prompt/negative, provider ID/state, start/end timestamps, wall seconds,
provider execution seconds where supplied, and estimated vs actual charges.
The retained original graph also records settings not explicitly bound. A
provider-job/workflow-bound delivery manifest is saved before output publication.
If all manifested files are present and hash-verified after interruption, Resume
marks delivery completed locally, without redownloading or rerendering. A partial
delivery still reconciles the known provider job. Prior recovery errors are cleared
on successful completion. Completed output hashes are checked on inspection.
The adapter deliberately ignores unqualified guessed billing fields; actual
charges remain JSON `null` (Unknown), not zero. No account balance differencing.

```
python -m renderrepo.permutation serve PLAN_HASH --port 8844
# Open http://127.0.0.1:8844/ ; Ctrl-C stops review only.
python -m renderrepo.permutation review PLAN_HASH JOB_HASH keeper --notes "Good preservation"
python -m renderrepo.permutation export PLAN_HASH ./review-export
```

The minimal review page groups by exact configuration or seed; shows all saved
images and exact settings/provenance; persists Keeper, Modify, Toss or Unreviewed
with optional notes up to 1000 characters. Failed executions cannot receive
human ratings. Notes have an explicit save button; rating buttons also save notes.
No AI review. JSON includes original and submitted workflows and summaries;
CSV contains settings/provenance JSON columns and a review row per job. Export
rows and metrics use a single review snapshot. `notes_json` retains lossless notes;
the readable `notes` column escapes spreadsheet formulas, including leading
whitespace. Known provider accounting/timing evidence survives later omission.
Ratings cover the selected output collection of one render, not each image separately.
Unreviewed results remain distinct from Toss. User text is displayed as text,
not HTML. CSV formula-leading scalar text is escaped.

Keeper and Keeper+Modify rates divide by **reviewed completed results**; review
coverage divides reviewed by all completed. Execution failure rate divides
terminal failed by terminal completed+failed; pending/unresolved are reported
separately through counts/status, never ratings. Seconds/keeper sums terminal
execution wall seconds (including failures/recovery/offline waits), divided by
reviewed keepers; unresolved attempts are excluded and explicitly labeled.
Known cost/keeper includes known charges for all attempts, grouped by native
unit, divided by reviewed keepers. Coverage/missing counts and a completeness
flag are included; an empty known-cost map means Unknown, not free execution.
Rates without a denominator are null.

Review server binds 127.0.0.1 only, checks exact Host and same-origin requests,
and requires a per-server token + Origin for writes. Only manifest-associated
completed image files are served, with output hash checks. No general directory
serving, remote/global analytics, auth identities, key logging or telemetry.
It is a **single local operator** tool, not a multi-user/hosted security boundary.
Any local process under that user can access the private store and review page.


## Release verification boundary

The standard release suite tests this engine with mocked transport and synthetic temporary files. Historical billable operator smoke scripts, adapter checkout paths, credential-loading examples and machine-specific rehearsals are excluded from this public source candidate. Use `python -m renderrepo.permutation --help` for current command syntax. A real execution path requires separate target/provider qualification and explicit approvals; no execution is part of the contest workflow import or release tests.
