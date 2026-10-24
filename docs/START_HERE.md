# Start here: use CBI_RenderRepo in Comfy

RenderRepo is a store for Comfy workflows. **Find a workflow → open its page →
Install → watch progress → Open workflow.** Supply your inputs and use Comfy’s
**Run** when ready. RR never renders as part of browsing, installation or opening.

- **Explore:** choose an action such as **Edit Image**, **Resize & Crop**, or
  **Combine Images**, or search for your task. Actions appear when the catalog has
  matching entries; tags remain searchable without filling the sidebar.
- Compare the cards’ image counts, prompt roles and operations. Open a workflow
  page for required/optional inputs, meaningful controls, processing and outputs.
  Older catalog entries say **Input details not yet available** until prepared.
  Unknown custom nodes or branches are labelled uncertain; RR does not turn a
  generic image editor into a face-swap tool.
- Author descriptions explain intent. Graph summaries describe connected paths;
  **Required nodes available** is a static check, not a promise of successful
  execution, memory fit or speed. Technical evidence stays in collapsed Details.
- **My Workflows:** choose an installed workflow and open it directly.
- **Settings / Manage:** sources, **Import workflow**, export and history.
  Ordinary Comfy editor JSON uses the same workflow-page path as `.rrpkg` packages.
- Readiness evidence and optional local tests stay under **Manage this workflow**
  on an installed workflow’s page. They are separate from installation.

[Install RenderRepo](#1-install-renderrepo) · [Install a workflow](#2-install-a-workflow-package) ·
[Browse repositories](#3-browse-a-repository) · [Use local files](#4-use-files-already-on-your-computer) ·
[Downloads](#5-manage-downloads) · [Share](#6-share-your-workflow) ·
[Updates/removal](#7-updates-and-removal) · [Troubleshooting](#8-troubleshooting)

## 1. Install RenderRepo

**Get a RenderRepo button in an existing, working local Comfy installation.**
You need Linux, Git, this CBI_RenderRepo checkout, a working
Comfy environment with Python **3.13+**, and `uv` or that environment's `pip`.
The checkout contains `pyproject.toml`, `scripts/` and `extensions/`. There is no
public catalog or download address to assume.

1. In a terminal, run setup from the RenderRepo checkout. Replace the quoted path
   with the folder containing your Comfy `main.py`:

   ```bash
   python3 scripts/setup_renderrepo.py --comfy-dir "/path/to/ComfyUI"
   ```

   Setup identifies Comfy's Python and prints the environment and extension-link
   changes before asking **Apply these setup changes?** It installs RR and connects
   its extension. It does not install/upgrade Comfy, Torch, CUDA, models, or node
   requirements. If the chosen environment has no `pip`, an existing `uv` can be
   used instead. If both are missing, setup explains how to proceed without changing
   the installation. It will not overwrite an unrelated extension directory.
2. Follow setup's last instruction: after changes, restart Comfy with its normal
   launcher, reload its browser page, and click **RenderRepo** near the lower-left.
   **Explore** opens with the task search and available workflows. An empty list is normal before
   adding a catalog. A correct rerun makes no duplicate link or package installation.
   Setup never stops/restarts a running server for you.

Omit `--comfy-dir` for bounded discovery in the current folder, its sibling
`ComfyUI`, and `~/ComfyUI`, `~/Apps/ComfyUI`, `~/PycharmProjects/ComfyUI`.
Multiple choices require a selection; noninteractive use requires an explicit
path. Use `--python "/absolute/path/to/Comfy/python"` for a nonstandard interpreter.
`--check` only verifies/reports; `--yes` explicitly approves the printed plan.
These options also work with `renderrepo setup --checkout "/path/to/renderrepo"`
when the CLI is already installed.

Currently demonstrated: **local Linux, local filesystem, Comfy frontend 1.55.14**.
Windows locking is unsupported; macOS, network filesystems and remote multi-user
Comfy are not qualified. A fresh Comfy dependency install is a separate prerequisite.

Restart after RR/backend or custom-node code changes; reload after frontend changes.
File-only installs normally need neither: **Open workflow** refreshes input choices.

<details>
<summary>Manual setup and launch (fallback / Advanced)</summary>

These steps change the selected Comfy environment. For a rehearsal, use a separate
Comfy checkout/environment with separate user, input, output and RR state. Keep the
RR checkout. Shell quotes below protect spaces; they are not GUI field characters.

1. Find the Python executable used by your **Comfy launcher**. It must be the
   environment where RR is installed. A separate system Python or unrelated venv
   will not make RR available to Comfy.
2. In a **terminal**, set the following paths. Replace `YOUR_NAME` and each example
   location with your actual paths. `.venv/bin/python` is an example; change it if
   your Comfy launcher uses a different environment.

   ```bash
   RR_CHECKOUT="/path/to/CBI_RenderRepo"
   RR_COMFY_DIR="/path/to/ComfyUI"
   RR_COMFY_PY="$RR_COMFY_DIR/.venv/bin/python"
   "$RR_COMFY_PY" --version
   ```

   | Name | What it means |
   | --- | --- |
   | `RR_CHECKOUT` | RenderRepo source files, not your Comfy folder |
   | `RR_COMFY_DIR` | Existing Comfy folder containing `main.py`, `custom_nodes`, etc. |
   | `RR_COMFY_PY` | Full path to the Python executable that runs Comfy |

3. Install RR into that environment. With an existing `uv` installation:

   ```bash
   uv pip install --python "$RR_COMFY_PY" "$RR_CHECKOUT"
   ```

   If that Python already has `pip`, you can use this **instead**:

   ```bash
   "$RR_COMFY_PY" -m pip install "$RR_CHECKOUT"
   ```

   These install RR and its Python dependencies, not Comfy, Torch or models.
   No editable (`-e`) installation is required. If neither tool is available,
   finish your Python environment setup before continuing.

   Verify the installed package with that same interpreter:

   ```bash
   "$RR_COMFY_PY" -I -c 'import renderrepo, sys; print(sys.executable); print(renderrepo.__file__)'
   ```

   The module path should be inside that environment's `site-packages`. `-I`
   prevents a checkout in your current directory from masking a missing or wrong
   installation. This check does not replace opening RR in Comfy.
4. Link the extension into Comfy. This Linux command refuses to replace an existing
   destination; if it already exists, inspect it rather than deleting it.

   ```bash
   ln -sT "$RR_CHECKOUT/extensions/comfyui_renderrepo" "$RR_COMFY_DIR/custom_nodes/comfyui_renderrepo"
   ```

   The Python package and this extension link are both needed. Copying the extension
   directory to that destination is an alternative to linking; do not nest it inside
   another directory of the same name.
5. Restart Comfy using its normal launcher, then reload its browser page. For a
   checkout normally launched from a terminal, a local launch is:

   ```bash
   cd "$RR_COMFY_DIR"
   "$RR_COMFY_PY" main.py --listen 127.0.0.1 --port 8188
   ```

   Use your usual hardware/options if different, and do not start a second server
   on an occupied port. Open `http://127.0.0.1:8188/` for this example launch.


Click **RenderRepo** after Comfy starts. For the model-free example, a CPU/offline
launch on the demonstrated Comfy version is:

```bash
cd "$RR_COMFY_DIR"
"$RR_COMFY_PY" main.py --cpu --offline --listen 127.0.0.1 --port 8190
```

Open `http://127.0.0.1:8190/`; never start another server on an occupied port.
The RR checkout, Comfy checkout and Comfy Python are separate locations. For later
terminal examples, set/re-enter `RR_CHECKOUT`, `RR_COMFY_DIR`, `RR_COMFY_PY` from
this panel in that terminal.
</details>

## 2. Install a workflow package

1. Search **Explore** and choose a result to open its page. Or use **Settings /
   Manage → Import workflow** for a `.rrpkg` or ordinary Comfy editor `.json`.
2. RR prepares the recipe and checks the selected Comfy environment automatically.
   Viewing the page does not download dependencies, install code or run the graph.
   The page shows the proposed files, known download size and reuse. Choose a
   download source there if alternatives exist. **Preparing…** becomes **Install**
   when the plan is ready. Resolve any missing requirement inline.
3. Click **Install**. That click authorizes the displayed ordinary file changes;
   there is no second routine approval screen. Extra confirmation is required for
   custom-node code, selected Python changes or unknown download sizes. Python
   requirements stay off under **Details → Advanced options** unless you select them.
   Conflicting/edited destinations remain protected. Changed scope requires a new click.
4. Progress and pause/resume/cancel controls stay on the workflow page. The bottom
   strip lets you return to ongoing work while browsing. When complete, click
   **Open workflow**. The previous unsaved graph stays in its existing tab.
   Choose your inputs and use Comfy’s **Run** to execute.

If already installed, **Open workflow** is available immediately. Other sources and
versions are under **Other sources and versions** on the page; inspecting another
version is not an upgrade. Newly installed custom nodes can require a Comfy restart.

A node can be available, missing with a declared install source, unavailable on an
installation-restricted target, or Unknown when inventory is incomplete. These are
static checks, not guarantees of execution, memory fit, quality or speed.

Recipe uploads are limited to 64 MiB compressed and unpacked. Ordinary JSON keeps
its original bytes and detected inputs; path selections are recorded separately.
Known loader schemas and declared model metadata are inspected, including supported
nested subgraphs. Arbitrary strings are not guessed to be model names. API-prompt
JSON is explicitly rejected by this import action: obtain the editor workflow from
its author instead. Dynamic/unrecognized bindings can still require manual review.

A hash verifies file identity against its declared artifact. Filename-only reuse is
weaker evidence. Catalog associations inferred from a filename are identified as
such; they do not prove model compatibility with the imported workflow. User source
mappings remain user-provided evidence, not official catalog metadata.

## 3. Browse a repository

**Find packages without collecting individual archives yourself.** A **source** is
a named location containing a RenderRepo package listing (`index.json`). It can be
a directory on the Comfy computer or an HTTP(S) index address supplied by a publisher.
No public catalog is configured by default.

1. Open **Manage → Sources** → expand **Add a package source** if collapsed.
2. In **Location on this
   computer or an index URL**, enter either a directory containing `index.json`
   or the publisher's actual index URL. **Display name (optional)** is derived from
   the location if left blank. For example, these are **GUI field values**:

   ```text
   /path/to/Workflow Packages/publisher
   ```

   ```text
   https://example.org/my-workflows/index.json
   ```

   The address above is a placeholder, not an official repository. An arbitrary
   Hugging Face model page, model-download URL, or folder of weights is **not** a
   RenderRepo repository. Ask the publisher for its RR index location.
3. Click **Add source**. RR validates and refreshes the listing, then shows packages
   in Explore automatically. A failure stays beside that source; correct the location
   or restore it and click **Refresh**. Refresh reads indexes only; it does not
   download models, install code, run pip or upgrade anything.
4. Use the prominent **Find a workflow** search. With the starter catalog, try
   `resize photo`, `make image smaller`, `crop portrait`, or `combine pictures`.
   Search uses publisher metadata and a small deterministic vocabulary, not AI.
5. Choose a result to open its workflow page. It shows purpose, source/version,
   inputs, limitations, compatibility findings and concrete installation changes.
   Source choices belong here; **Details** holds technical evidence. Going Back
   preserves your search and category. Existing workflows offer **Open workflow**.

The **Official catalog — optional configuration** setting starts unconfigured and
contacts no placeholder host. Leave it blank until the project's real index is
available. Community catalogs and local imports work independently.

| View | Use it to… |
| --- | --- |
| **Explore** | Search and choose a workflow page |
| **My Workflows** | Find installed packages, newer versions and **Open workflow** |
| **Activity** | Check current operations, transfer controls and retained installation history |
| **Sources** | Add, refresh, enable/disable or remove repository locations |

**Stale source** means the last refresh failed or could not be verified. RR keeps
its last valid listing. Expand **Location and refresh details** for the reason,
restore the source and **Refresh** again. Stale packages may no longer be retrievable;
installation still checks archive integrity. Age alone does not make a source stale.
**Disable** hides that source's packages without removing installed packages;
**Enable** includes it again. Source controls do not uninstall anything.

### Check local readiness

An uninstalled workflow page checks the target automatically. For an installed workflow,
open its page from **My Workflows**, then **Manage this workflow → Check local readiness**.
Expand **Readiness evidence** for the observed Comfy version, Python/Torch versions,
runtime device types, timestamp and individual reasons. **Recheck local readiness**
refreshes those observations without installing or running anything.

- **Ready:** the declared checks passed against this local server.
- **Missing:** a required node, visible model selection or input/file is absent.
- **Incompatible:** an explicit runtime requirement or expected file/revision identity conflicts with local evidence.
- **Unknown:** evidence is missing, unsupported, unreadable or no longer describes the selected workflow.

Readiness and installation status are separate. A ready result does not prove that
a graph will execute or that hardware has enough capacity. Publisher task descriptions
and recorded authoring versions do not establish readiness. Checks read server
inventories and local files; they fetch no package archive or artifact, install no
dependencies and submit no render. Older catalogs/receipts without dependency
inventories show unknown. Changing version or reloading clears the displayed result;
check again when inputs, nodes, files or runtime settings change.

### Test locally

For an installed original starter package, open **My Workflows**, choose its page, then
**Manage this workflow → Test locally**.
Choose one **First observed run**, or add a **Recomputed repeat**. Read the review:
it names this native Comfy server, exact controls and input dimensions, run count,
deadlines, expected output folder and local CPU/GPU/RAM/file costs. Preparing the
review runs nothing. Click **Start reviewed test** once to execute that plan.

Results show state, dimensions/batch, runtime identity, queue time, server execution
time, total elapsed and output-verification time. **Local test evidence** contains
fingerprints, submitted prompts, intentional overrides, cache events, outputs and
memory observations. **Cached** or **Computation unverified** runs are excluded from
compute-performance evidence. First observed does not mean cold start.

Tests currently support only the three original model-free starter settings and
bundled inputs, batch 1, all inputs/outputs at most 1024×1024. Changed graphs,
provider/API nodes, generative/custom-node workflows and unresolved destinations
are unsupported; the review explains what is missing. No workload is silently
reduced. The repeat changes one recorded center pixel and private input filenames
to force recomputation; no shared caches are flushed and no models are unloaded.

**Stop** prevents further submissions and targets only this test's job. A running
operation may continue if safe targeted interruption or server confirmation is
unavailable. A deadline is not proof of stopping. Reopen Installed after reload;
known jobs reconnect without resubmission. **Reconcile known jobs** checks uncertain
work without starting another run. Use the recorded-tests selector for earlier
attempts; original results are preserved and marked when current configuration
differs. An explicit rerun creates a new reviewed attempt.

Success applies only to the recorded configuration. It does not establish image
quality, minimum hardware, a cold start, an exact memory peak or whether a workflow
is practical. Process memory is Unknown; available memory samples describe the
whole host/device. See [CLI commands](CLI_REFERENCE.md#bounded-local-execution) and
[developer evidence](DEVELOPMENT.md#test-locally-validation).

### Local starter catalog

From your RR checkout, using your existing RR Python environment, run:

```bash
python scripts/build_starter_catalog.py --directory "/tmp/RR Starter Catalog"
```

Choose a **new** directory; an existing destination is refused without changing it.
This creates three version 0.1.0 packages and `index.json`. It installs nothing and
configures no source. This is a local, unpublished catalog.

In RR **Settings / Manage → Sources**, add `/tmp/RR Starter Catalog` as the location, then **Refresh**.
In **Explore**, search for a task below, open its page, wait for preparation, then
**Install** → **Open workflow**. Ordinary installation needs no second approval screen. Bundled examples should
already be selected. In each **Choose input image** or **Base image / Overlay image**
node, use **choose file to upload** or the `image` selector for your own image.
Change the visible controls below, then click Comfy **Run**.

| Package / search | What it does and controls to use |
| --- | --- |
| **Resize an image** (`rr-starter-resize`): `resize photo`, `make image smaller` | **Resize**: set `width` and `height`; set one to 0 to preserve aspect ratio. Keep `crop` disabled. Default 160×100; two nonzero dimensions may stretch. `upscale_method` chooses interpolation, with nearest-exact supplied. |
| **Center-crop a portrait** (`rr-starter-portrait-crop`): `crop portrait`, `crop to aspect ratio` | **Center crop**: set `width` and `height` for both output size and ratio; keep `crop` center. Default 100×200. It crops centrally and resizes if needed; it cannot locate a subject or move the crop. |
| **Combine two images with an overlay** (`rr-starter-overlay`): `combine pictures`, `overlay images` | **Place overlay**: change `x` / `y` from the base's top left. Keep `resize_source` false for original overlay size. Default (224,120). An opaque rectangle replaces pixels on the base; edges clip. No automatic transparency or canvas expansion. |

The **Save** nodes write separate prefixes under Comfy output:
`rr-starter/resize/resized`, `rr-starter/portrait-crop/portrait`, and
`rr-starter/overlay/combined`. Inspect the saved PNG or Save node preview.
These are ordinary pixel operations, with no models, custom-node dependencies or
AI enhancement. Original procedural SFW inputs are bundled. Actual example results
are included under `examples/` in the catalog and archives; these review assets are
not installed into Comfy. Exporting a workflow preserves selected bundled inputs,
but does not retain these supplementary example outputs.

The generated directory also works when served by an ordinary HTTP server. If you
choose to serve it locally, run this in another terminal:

```bash
python -m http.server 8200 --bind 127.0.0.1 --directory "/tmp/RR Starter Catalog"
```

Add `http://127.0.0.1:8200/index.json` instead. Keep that terminal running while RR
refreshes or retrieves packages. This does not publish a hosted catalog.

Measured execution and reproduction details are in
[Developer documentation](DEVELOPMENT.md).

## 4. Use files already on your computer

Comfy’s configured model directories are searched automatically. If a requirement
is unresolved, use **Choose existing server file**, select the matching path, and
**Use selected server file**, or enter a server path/direct source URL and choose
**Use source**. RR prepares the revised review without discarding other resolutions.
Dependencies with multiple declared sources have a compact source selector.

Paths refer to the **Comfy server**, not the browser computer. Importing a browser
file uploads the recipe; it does not reveal or provide access to that file’s original
filesystem path. Additional search directories and manual `FILENAME=source` entries
remain under **Advanced options**; changes there require **Review changes again**.

Reused model files stay where they are. RR creates safe references when necessary,
including for a chosen server file with a different basename. Expected hashes and
sizes must match when supplied. A filename or user choice without a declared hash
is not labelled verified. Different contents at an existing destination block
installation rather than overwrite it. Different versions can use distinct declared
paths; RR does not silently rename models or change workflow parameters.

Keep external originals in place. Removing a workflow preserves shared placements,
model weights and externally managed files. There is no automatic model cleanup.

## 5. Manage downloads

**Return to an installation and control its file transfers.**

1. The active or most recent interrupted installation reconnects when RR reopens.
   Its transfer controls and **Resume installation** action appear alongside the
   operation; there is no need to discover a history page. Closing/reloading the
   browser does not cancel an approved worker. Older entries remain in
   **Manage → Activity → Installation history**.
2. During installation, use **Pause downloads**, **Resume downloads** and
   **Download limit (MB/s; 0 = unlimited). One file transfers at a time.** The
   default limit is 5 MB/s. Amount, speed and ETA describe the current file.
   **Pausing…** can appear before **Paused** while a network read finishes.
3. To stop transfers, click **Cancel; keep partial files**. It is not an undo.
   Completed files and tracked partial work remain available for recovery.
4. After a failed/interrupted installation, use its inline **Resume installation**.
   RR checks the current plan and reuses completed files.
   Changed scope appears on the workflow page and requires a new Install click; you need not upload again.

Pause, limit and cancel apply to installation **file transfers**, not Git cloning
or custom-node pip operations. They do not control archive retrieval while preparing
review, index refresh, export hashing, or Comfy renders. A slow network read can delay
acknowledgment. Temporary HTTP failures can be retried up to three attempts per source
within the approved installation.

Resumable partial downloads require an expected fingerprint and server resume
support. Otherwise that file restarts; corrupt partials are discarded. A missing
fingerprint prevents retaining an incomplete transfer for safe resume.

After restarting Comfy, RR does not automatically start another installation. An
unfinished entry is **interrupted**, unless a surviving worker is still active:
RR monitors it and blocks duplicate retry/removal until it exits. A surviving worker
may finish work already approved. Check its status before retrying; do not delete
history, partials or lock files as a recovery step.

## 6. Share your workflow

**Export a graph and its dependency information for someone else to install.**
File fingerprints establish content identity, not publisher signatures or model
compatibility.

1. Open the working graph in Comfy. In RR → **Manage → Export workflow**,
   enter **Package name** and **Package version**.
2. In **Required custom-node repositories (select only those used by this graph)**,
   select the repositories the graph actually needs. Eligible entries are clean
   Git checkouts with usable origins and readable revisions. Unavailable entries
   show their reasons; standalone Python node files are not silently bundled as code.
   RR cannot infer every node's repository or Python requirement.
3. To include an example, enter detected input filenames in **Input filenames to
   bundle as examples (one per line; leave blank for user-supplied inputs)**. Use
   names relative to the input folder, for example `example.png`, not a shell path.
   Include only examples you intend to share. Leaving it blank means the recipient
   supplies those inputs.
4. Optionally expand **Help others find this workflow (optional)** and fill its
   title, summary, task phrases, tags, required inputs, examples or limitations
   before export. These are publisher-supplied claims, not independent verification.
   Without task phrases RR warns and falls back to names/descriptions.
5. Click **Export .rrpkg**. RR reads available dependency files to calculate their
   fingerprints; large weights can make this take time even though they are not
   uploaded into the archive. Wait for the browser's archive download. Review any
   unresolved notices in the export report before sharing it.
6. Share the `.rrpkg` with instructions about required inputs and runtime needs.
   The recipient needs Comfy/RR, compatible nodes and hardware, and access to the
   required model files—locally or through usable sources declared in the package.
   Exporting does not upload your models or create a public repository.

Export uses the current editor graph and preserves its subgraphs. It inventories
known loaders conservatively; unknown/dynamic dependencies need author attention.
Export sessions are temporary, not installation history. Keep the panel/page open
until the download finishes; do not rely on recovering an export after a browser or
backend restart. Non-running temporary sessions can expire once over an hour old.
The archive you saved in Downloads remains yours to keep.

## 7. Updates and removal

**See newer versions without disturbing your installed workflow.**

1. In **Settings / Manage → Sources**, refresh the relevant source. My Workflows
   explains when newer versions are listed.
2. A workflow page’s **Other sources and versions** exposes the available choices.
   Selecting one prepares its changes; it does not replace the installed package.
   A different registered version or edited destination can block installation.
3. Keep using the installed workflow unless you deliberately choose separate CLI
   removal and installation. There is no safe GUI upgrade, atomic replacement,
   rollback or Update All action. Inspecting another version removes nothing.

Edited workflows are not overwritten. **My Workflows → workflow page → Open workflow** reads the
current file, including valid local edits. An old history entry's opening action
can be unavailable if the file no longer matches its retained package. After removal,
a preserved edited/pre-existing workflow can still block a new version at that path.
Back it up and decide what to keep; do not delete receipts or edit recorded hashes
to bypass the conflict.

**Choose the kind of removal you actually want:**

| Action | Result |
| --- | --- |
| Sources → **Remove source (installed packages stay)** | Removes the listing location from configuration; installed files and history remain. |
| Activity → **History options** → **Remove retained history (installed files stay)** | Deletes that job's retained package/logs/history, not the installed package. Active jobs cannot be removed. |
| Terminal package removal below | Removes its installation registration and the listed unchanged, unshared RR-owned placements after approval. |

Package removal currently uses the CLI, not an Installed-page button. In a terminal
with the paths from [setup](#1-install-renderrepo), list package names first:

```bash
"$RR_COMFY_PY" -m renderrepo list --comfy-dir "$RR_COMFY_DIR"
"$RR_COMFY_PY" -m renderrepo remove "my-workflow" --comfy-dir "$RR_COMFY_DIR"
```

Replace `my-workflow` with the listed **package name**, not an archive filename.
Stop any active installation using it, back up edits, inspect the removal plan,
and answer its confirmation only if appropriate. Declining leaves files unchanged.

Removal can delete unchanged RR-created workflow/example files, links and owned
custom-node checkouts. It preserves model weight files, pre-existing/modified/shared
placements, installed Python packages and installation history. Empty directories
can remain. Python environment changes are not rolled back. See the
[CLI reference](CLI_REFERENCE.md#remove-an-installed-package) before a manual replacement.

## 8. Troubleshooting

**Find the failed operation before trying another installation.** Open **Activity**
and the relevant **View status / report**, or expand **Technical details and full
report**. Source failures stay beside their row under **Sources**.

| Problem | What to do |
| --- | --- |
| RR button missing, cannot connect, or failed request | Confirm Comfy is running. Check the fresh startup terminal for RR import errors and the correct Python environment. Restart after backend installation, then reload the browser. Use the same local Comfy address for page and backend. Do not assume an intentional test interruption; inspect history before retrying an ambiguous install. |
| Newly installed image missing from selector, even with previews | Use the current RR frontend, then reopen through **My Workflows → workflow page → Open workflow**. RR refreshes file choices first. If that fails, inspect the input-choice refresh error/connection; verify the saved filename and installed input path. Do not treat old previews as proof of working input. |
| File not found | Confirm the full path exists on the Comfy computer and the external drive is mounted. Check spelling and capitalization exactly. Filename search ignores case, but installation paths and automatic filename matching on Linux do not. In GUI fields use `/path/to/AI Models`, not quotes or `AI\ Models`. Re-review after correcting a source. |
| Wrong size/fingerprint | The file is not the expected bytes. Obtain the intended version or ask the package author to correct its metadata; do not rename a different model or disable checks. |
| Missing custom nodes | Check whether the package actually declares them and whether code/Python installation completed. Restart Comfy after installing node code. If declarations are missing, ask the author; RR does not infer every node repository. A pip error or missing `pip` belongs to the selected Comfy Python environment; partial reports show what completed. |
| Interrupted download | Reopen its Activity entry and **Retry installation** or **Resume installation** after restoring connectivity/storage. Retain partials/history. A server without resume support restarts that file. If a previous worker is active, wait or use its transfer controls rather than start another install. |
| Installed, but render fails | Read Comfy's node error and execution log. Check required inputs, compatible model/node versions, memory, drivers and runtime requirements. RR handles declared file acquisition/placement and selected code requirements; Comfy handles graph execution. Installed is not a rendering certification. |
| Previews appear, but no new image is saved | Check Comfy history and the actual output filename. An unchanged graph can return cached results. For an uncached retest use an idle Comfy restart or Comfy's cache controls, then Run again. Do not remove output files/history to hide the cache behavior. |
| Stale listing or source conflict | Restore/refresh the same source, or explicitly choose the intended source/version. A model URL is not a package index; get the actual RR source location from its publisher. |

<details>
<summary>Technical connection checks (terminal)</summary>

Use the local address from your own Comfy page. For the example launch:

```bash
curl -fSs http://127.0.0.1:8188/api/renderrepo/config -H 'X-RenderRepo-Request: 1'
```

A JSON response means the RR backend is reachable. No response points to a stopped
server, disconnect or blocked request; HTTP 404 suggests missing route registration,
403 a local/same-origin rejection, and 500 a backend error. Read the operation and
URL RR reports plus the server traceback. Do not expose the bridge publicly or
bypass origin checks to make a request succeed.

To inspect the extension link without changing it:

```bash
ls -ld "$RR_COMFY_DIR/custom_nodes/comfyui_renderrepo"
"$RR_COMFY_PY" -I -c 'import renderrepo, sys; print(sys.executable); print(renderrepo.__file__)'
```

A healthy backend, installed files and a successful rendering job are separate
checks. The [CLI reference](CLI_REFERENCE.md#check-a-running-comfy-instance) describes
`doctor`, a read-only check of known runtime/file requirements; it does not execute
or certify the graph.
</details>

If a new page says **Backend update needed**, the running Comfy still has older RR
Python code. Restart that selected Comfy when convenient and reload the page.
Installed workflows can still open; RR will not authorize new installs against the
older approval contract.
