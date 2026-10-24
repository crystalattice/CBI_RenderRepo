# RenderRepo CLI reference

For Comfy GUI tasks, [start here](START_HERE.md). These commands use the same
package/install implementation. Run them with the Python environment where RR is
installed. `python -m renderrepo …` and the installed `renderrepo …` entry point
expose the same commands; use a full Python path when more than one environment exists.

## Command map

| Command | Purpose |
| --- | --- |
| `setup --comfy-dir ROOT --checkout CHECKOUT` | Review/connect RR to an existing Comfy; `--check` verifies only |
| `inspect PACKAGE` | Check required files in configured directories; does not install |
| `install PACKAGE --comfy-dir ROOT` | Review/approve dependency and workflow placement in Comfy |
| `install PACKAGE --destination DIR` | Acquire files into a directory; no Comfy receipt/workflow registration |
| `pack WORKFLOW --output PATH` | Create a package directory, or a ZIP when PATH ends in `.rrpkg` |
| `index DIRECTORY` | Generate `index.json` from immediate `.rrpkg` files |
| `source add/list/remove/enable/disable` | Manage named repository locations |
| `refresh` (`update`) | Retrieve/validate enabled repository indexes |
| `packages` / `search [QUERY]` | Read saved listings; no model/archive retrieval |
| `updates --comfy-dir ROOT` | Compare installed receipts with saved listings; no upgrade |
| `list --comfy-dir ROOT` | List installed/partial package registrations |
| `remove NAME --comfy-dir ROOT` | Show and confirm removal of owned placements/registration |
| `doctor PACKAGE --comfy-url URL` | Check known requirements against a running Comfy instance |

Each command accepts `--help`; for example `python -m renderrepo install --help`.
`PACKAGE` can be a package directory, its manifest, an `.rrpkg`, a direct HTTP(S)
archive URL, or an indexed name
for `install`. Only the Comfy GUI importer requires editor-format graph JSON.

## Normal package-manager commands

```bash
renderrepo search "fix photo"
renderrepo install my-workflow
renderrepo install ./package.rrpkg
renderrepo install https://example.org/package.rrpkg
```

The URL is a placeholder; use a publisher's real direct archive link. Named/local/
URL installs share review and installation. Without `--comfy-dir`/`--destination`,
install uses the same bounded Comfy discovery as setup and asks only for genuine
ambiguity. Explicit `--comfy-dir` remains recommended for multiple installations.
Catalog CLI state stays separate from installation-local GUI state unless the
source/cache flags below select it. No default public catalog is contacted.
GitHub landing pages, MEGA shares, HTML/authentication walls are not resolved;
obtain a local `.rrpkg` and import/install it instead. Direct archives are bounded
to 64 MiB and structurally validated. Their computed checksum has no independently
supplied expected checksum; indexed archives have the index's expected SHA-256.

Search is deterministic: structured task phrases, then tags, title, summary/
description/name. Query terms are ANDed after case/punctuation normalization and a
small explicit synonym map. Legacy partial name/description search remains a lower
rank fallback. Versions group within a source/name; conflicting sources stay separate.

## Inspect and install

**Terminal examples:** replace every `/path/to/…` and package name with your own.
Shell quotes preserve spaces; do not put those quotes in GUI fields.

```bash
python -m renderrepo inspect "/path/to/example.rrpkg" --artifact-dir "/path/to/AI Models"
python -m renderrepo install "/path/to/example.rrpkg" --comfy-dir "/path/to/ComfyUI" \
  --artifact-dir "/path/to/AI Models" --dry-run
python -m renderrepo install "/path/to/example.rrpkg" --comfy-dir "/path/to/ComfyUI" \
  --artifact-dir "/path/to/AI Models"
```

Repeat `--artifact-dir` for more roots. `--config FILE` supplies `artifact_dirs` in
YAML; `RENDERREPO_ARTIFACT_DIRS` is an alternative separated by `os.pathsep`
(`:` on Linux). `--source 'FILENAME=/absolute/local path'` or
`--source 'FILENAME=https://publisher.example/direct-file'` overrides a missing
file's source. Use the package's expected filename on the left. No scraping or
arbitrary model substitution is performed.

Comfy defaults: models in categorized `ROOT/models`, inputs in `ROOT/input`, graph
in `ROOT/user/default/workflows/NAME.json`. `--input-dir`, `--workflow-dir` and
`--model-dir` override these areas; `--model-dir` stores weights in that categorized
root and links them into Comfy. Existing matching files are reused. Existing
conflicting workflow/placement contents are refused. File identities are checked
against supplied size/SHA-256; missing metadata remains unverified.

`--dry-run` avoids dependency/workflow installation. Requesting an indexed/direct-link
install authorizes archive retrieval to read the plan, including during dry-run.
Dependency/code installation still has one approval. `--yes` approves it and fails
if sources remain unresolved; do not use it merely to inspect a plan.
`--rate-limit MBPS` defaults to 5; 0 is unlimited. Transfer retries/partials follow
[the user guide](START_HERE.md#5-manage-downloads).

Custom-node requirements need `--install-nodes`. To install their `requirements.txt`
using a specific environment, also supply `--comfy-python /path/to/python` (uses
`python -m pip`). Missing/uninstalled node dependencies can leave a partial receipt;
RR does not roll back Python changes. Restart Comfy before checking newly installed
node types. Browser-backed jobs have durable recovery controls; a direct CLI install
does not create that GUI job history. Avoid simultaneous manual CLI changes and
GUI installs against one Comfy root.

## Create and browse an independent repository

Use an unused output path. The example URL below is a placeholder, not an official
RR catalog. Model-file URLs are not package repository indexes.

```bash
python -m renderrepo pack "/path/to/workflow.json" --output "/path/to/publisher/my-workflow-0.1.0.rrpkg" \
  --name my-workflow --version 0.1.0 --description "My workflow" \
  --artifact-dir "/path/to/ComfyUI/input" --bundle-input example.png
python -m renderrepo index "/path/to/publisher"
python -m renderrepo source add personal "/path/to/publisher"
python -m renderrepo refresh --repo personal
python -m renderrepo search workflow --repo personal
python -m renderrepo install my-workflow --repo personal --version 0.1.0 --comfy-dir "/path/to/ComfyUI"
python -m renderrepo updates --repo personal --comfy-dir "/path/to/ComfyUI"
```

For an actual HTTP index supplied by a publisher:

```bash
python -m renderrepo source add community "https://example.org/packages/index.json"
python -m renderrepo source list
python -m renderrepo source disable community
python -m renderrepo source enable community
python -m renderrepo source remove community
```

No sources are enabled by default. `refresh` reads indexes only; search/updates read
saved data. Failure keeps the last valid cache, marked stale. Disabled/removed
sources are excluded. Indexed install uses the newest numeric version unless
`--version` selects one; multiple sources for one name require `--repo`.
Numeric dotted versions compare by number (`1.10 > 1.9`; `1 = 1.0 = 1.0.0`),
without general SemVer/prerelease ordering. Archive identity/size/SHA-256 are checked
before dependency installation. Hashes provide integrity, not signatures.

CLI sources default to `~/.config/renderrepo/sources.yaml` and cache to
`~/.cache/renderrepo` (XDG overrides supported). They are **separate from the GUI's
installation-local source settings**. For CLI/GUI interoperability, append both
flags to source, refresh, search, updates and indexed-install commands:

```text
--sources /path/to/ComfyUI/.renderrepo/repositories/sources.yaml
--cache-dir /path/to/ComfyUI/.renderrepo/repositories/cache
```

Quote paths containing spaces when used in a terminal. Use separate paths to
isolate experiments from your normal sources/cache.

## Export dependencies and custom nodes

`pack` preserves the input JSON bytes. Model weights remain external references;
`--bundle-input` explicitly includes a detected input file. Additional
`--artifact-dir` roots help locate files for fingerprinting. Packing does not
execute the graph or infer every dynamic dependency. Review unresolved notices.

```bash
python -m renderrepo pack "/path/to/workflow.json" --output "/path/to/new-package.rrpkg" \
  --name my-workflow --version 0.1.0 --comfy-dir "/path/to/ComfyUI" \
  --custom-node ComfyUI-Example
```

Choose clean Git custom-node repositories with usable origins; repeat
`--custom-node` for each needed one. `--node-source NAME=URL_OR_ABSOLUTE_GIT_PATH`
can supply an eligible repository's missing/portable origin. Code is pinned to a
full revision; standalone Python node files are provenance, not automatically
packaged code. Submodules need separate manual setup. Comfy itself is not upgraded.

## Remove an installed package

```bash
python -m renderrepo list --comfy-dir "/path/to/ComfyUI"
python -m renderrepo remove my-workflow --comfy-dir "/path/to/ComfyUI"
```

Read the removal plan, then answer its confirmation. `--yes` approves explicitly.
Removal deletes the receipt and unchanged, unshared RR-owned placements. Weights,
modified/pre-existing/shared files and Python packages are preserved; history is
not removed. Edited files can remain and block a replacement at the same path.
Updates require explicit remove/install, with no atomic upgrade or rollback.
See [the three distinct removal actions](START_HERE.md#7-updates-and-removal).

## Check a running Comfy instance

```bash
python -m renderrepo doctor "/path/to/example.rrpkg" \
  --comfy-url http://127.0.0.1:8188 --comfy-dir "/path/to/ComfyUI"
```

`doctor` reads Comfy's native `/object_info`, not the SDK proxy. It checks known
node/model selections and declared local files/inputs, plus captured runtime
provenance where available. Pass more `--artifact-dir` roots as needed. It does
not render or prove hardware capacity. Optional install `--comfy-url` runs it
after placement; an already-running Comfy may still need a restart for new nodes.

Exit statuses: general inspect/install 0 success, 1 unresolved/mismatched files,
2 malformed input/operation error; Comfy/node installs can return 3 for unverified
Python dependencies. `doctor` uses 0 checked requirements pass, 1 known missing or
mismatched, 2 operation error, 3 unknown/incomplete. Source refresh returns 1 for
source failures. Read each command's report rather than interpreting a successful
file check as a successful render.

[Package/index contract and implementation details](PACKAGE_V0.md) ·
[Developer checks](DEVELOPMENT.md) · [Additional permutation tools](PERMUTATION_V0.md)

## Optional discovery metadata

Pass `pack --discovery /path/to/discovery.json`. The optional JSON object supports
`title` and `summary` strings, plus lists of `tasks`, `tags`, `required_inputs`,
`examples`, `limitations`. For example:

```json
{"title":"Repair an old photo","summary":"Publisher description of the workflow",
 "tasks":["Photo restoration","Repair scratched portraits"],
 "tags":["family photos"],"required_inputs":["A damaged photograph"],
 "limitations":["Publisher-supplied limitations; not an independent quality rating"]}
```

Packing warns when no task phrases are supplied but still works. `index` preserves
this metadata; existing manifests/indexes without it remain valid. The official
setting can be supplied later with `source official REAL_INDEX_URL`;
`source official` clears it. Leaving it blank makes no network request.


## Local readiness (read-only)

```bash
python -m renderrepo readiness "/path/to/package.rrpkg" \
  --comfy-url http://127.0.0.1:8188 --comfy-dir "/path/to/ComfyUI" --json
```

Accepts a local package directory, manifest or archive. No repository acquisition,
artifact retrieval, dependency installation or prompt submission occurs. Without
`--json`, prints the state and individual evidence. `--timeout` defaults to five
seconds per inventory endpoint. `--artifact-dir` and `--config` select additional
local artifact roots. `--input-dir` supplies a configured input folder override.
Point `--comfy-dir` at the installation belonging to the given
server; file identity and server visibility are separate evidence.

Exit codes: **0 ready**, **1 missing**, **4 incompatible**, **3 unknown**, **2 invalid
arguments/package**. Conflicts take precedence over missing dependencies, then unknown;
the report retains all individual reasons. The result includes observation time,
server URL, runtime versions/device types and the scope limitation. This diagnoses
declared dependencies and explicit constraints, not execution or hardware capacity.
`doctor` retains its earlier diagnostic text/exit-code contract.

Package authors can supply `pack --runtime-requirements requirements.json`:

```json
{"python_version": ">=3.13,<4", "comfyui_version": ">=0.39.0", "device_types": ["cpu"]}
```

This becomes `runtime.requirements` in the manifest. Supported version fields are
`python_version`, `comfyui_version`, `pytorch_version`; comma-separated numeric
`==`, `>=`, `<=`, `>`, `<` comparisons are supported. Device types list acceptable
observed runtime types (any match satisfies the requirement). Unknown keys,
unsupported specifiers and prerelease comparisons remain unknown. No requirement
is inferred from recorded export provenance. Use exact custom-node pins through
the existing `runtime.custom_nodes` contract.


## Bounded local execution

Only the original three installed starter profiles and their bundled SFW inputs
are supported. Use the existing Comfy Python (with its existing Pillow); the
command does not install dependencies. The RR extension must be loaded in the
selected native local server. Matching local Comfy/input/output directories and
built-in non-API node modules are verified; a loopback address alone is insufficient.

```bash
python -m renderrepo test plan --comfy-dir "/path/to/private Comfy" \
  --name rr-starter-resize --comfy-url http://127.0.0.1:8202 --runs 2 --deadline 60 --json
```

Planning writes a durable plan and small prepared input files, but submits nothing
and places nothing in Comfy's input/output folders. Review its `plan`, including
exact prompts/overrides, dimensions, count, resource notice, private output path and
time limits. Start only with the returned id and approval SHA-256:

```bash
python -m renderrepo test start --comfy-dir "/path/to/private Comfy" \
  --id PLAN_ID --approve REVIEWED_SHA256 --json
python -m renderrepo test status --comfy-dir "/path/to/private Comfy" --id PLAN_ID --json
python -m renderrepo test stop --comfy-dir "/path/to/private Comfy" --id PLAN_ID --json
python -m renderrepo test reconcile --comfy-dir "/path/to/private Comfy" --id PLAN_ID --json
```

The start approval is single-use and bound to the complete plan, current package /
workflow/input fingerprints and runtime/hardware/launch evidence. A mismatch requires
a new plan. There is no `--yes` bypass. Explicit reruns get new ids. `--input-dir` /
`--output-dir` on planning must match the native server's configured directories.
Readable output is the default; `--json` retains structured evidence for all actions.
Exit 0 means the requested control/status operation succeeded; inspect `state` for
the asynchronous test outcome. Invalid/unsupported actions return 2. The `worker`
action is the detached internal runner and cannot execute an unapproved plan.

One or two runs are permitted, with 1–60 seconds per submission including queue
time. Each output-verification budget is 15 seconds; polling/network observation
can lag by a five-second request timeout. The review reports the corresponding
elapsed observation budget. Monitoring may continue after timeout/Stop; no process
is killed and a deadline never proves computation stopped. Stop removes only owned
queued jobs, or uses Comfy's atomic prompt-id-targeted interrupt when available.
It never calls a global interrupt, clears a queue/cache or unloads models.

Status may reconcile a known job after a worker/backend interruption, but never
submits the unfinished remainder. An ambiguous POST acknowledgement retains its
intended prompt id, stops further submissions and is monitored/reconciled rather
than retried. History, original evidence and output copies live under
`COMFY/.renderrepo/local-tests/ID/`. The original private Comfy output and input
copies use `rr-local-tests/ID/`. No automatic cleanup removes these files.


## Immutable RR capsules

`pack` now seals exports as schema-1 RenderRepo-profile S4 capsules. Legacy v0
imports still work. Explicitly copy/migrate an existing package with:

```bash
renderrepo capsule old.rrpkg --output migrated.rrpkg
renderrepo pack changed.json --name example --version 0.2.0 \
  --predecessor previous.rrpkg --output next.rrpkg
```

Outputs must be new destinations. `--predecessor` links a changed export; without it,
`pack` creates a root capsule. Full hashing, provenance, observation separation and
compatibility rules are in [the capsule profile](S4_CAPSULE.md). S4 cross-application
metadata and middleware are not implemented.
