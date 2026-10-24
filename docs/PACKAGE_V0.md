# RenderRepo payload manifest v0

New exports are the [RenderRepo profile of an S4 capsule](S4_CAPSULE.md), with a
versioned JSON `capsule.json` descriptor. This page specifies the preserved v0
payload manifest. New manifest bytes use JSON syntax at `manifest.yaml`; legacy
YAML v0 packages remain readable without automatic migration. Target observations
and portable claim boundaries are defined by the capsule profile.

A package is a directory containing `manifest.yaml` and a ComfyUI JSON workflow.
The workflow is authoritative for node topology, connections, widget values, and
embedded subgraphs. RenderRepo does not rewrite it or introduce another graph DSL.
The manifest is the manually authored or exported inventory of external files required by
that exact workflow, including input images when they are fixed in the export.

```yaml
schema_version: 0
name: example
workflow: workflow.json
artifacts:
  - filename: model.safetensors
    size_bytes: 123456
    sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    sources:
      - uri: https://example.org/model.safetensors
      - uri: file:///srv/models/model.safetensors
  - filename: input.png
```

| Field | Contract |
| --- | --- |
| `schema_version` | Required integer, exactly `0`. |
| `name` | Required nonempty string identifying the package. |
| `description` | Optional string used in repository search; may be empty. |
| `workflow` | Required relative path to an existing ComfyUI JSON editor graph or API prompt graph within the package. |
| `artifacts` | Required list; may be empty for a workflow with no external files. |
| `filename` | Required unique basename, case-sensitive. No directories, slash, backslash, NUL, `.` or `..`. |
| `size_bytes` | Optional nonnegative integer; exact byte count, never rounded GB. |
| `sha256` | Optional 64 hexadecimal characters; comparisons ignore hex case. |
| `sources` | Optional ordered list of `{uri: string}` mappings. `inspect` never contacts them. `install` tries supported sources after consent. |

Unknown fields and invalid types are errors. Omit unavailable sizes/hashes;
do not invent them. SHA-256 is computed over the full file, streamed in chunks.
Without size/hash, `FOUND` means filename presence only, not verified identity.
With both specified, both must match. Sources can describe HF, CivitAI, ordinary
HTTP repositories, or local file URIs. `install` downloads ordinary HTTP(S)
direct URLs (including HF/CivitAI direct download URLs) or copies local files.
There are no provider SDKs, automatic account login, or Internet-wide searches.

## Inspection and local configuration

```bash
renderrepo inspect examples/wan2.2-i2v \
  --artifact-dir /opt/ComfyUI/models \
  --artifact-dir /opt/ComfyUI/input
renderrepo inspect examples/wan2.2-i2v/manifest.yaml --config local.yaml
```

`local.yaml`:

```yaml
artifact_dirs:
  - /opt/ComfyUI/models
  - ./input
```

Directories from `--artifact-dir` take precedence over `--config`; config takes
precedence over the `RENDERREPO_ARTIFACT_DIRS` environment variable (paths
separated with the platform's `os.pathsep`, `:` on Linux). CLI and environment
relative paths use the current working directory; config relative paths use the
config's parent. `~` is expanded. An explicit empty config list searches nothing.
No configured roots means every required artifact is `MISSING`; the package
directory and current directory are not implicitly searched. Invalid roots and
filesystem read errors fail inspection with exit 2 instead of claiming absence.

Search is recursive, with roots in configured order, sorted directory and file
names within each root, and no descent into directory symlinks. File symlinks
are followed. All same-name candidates are tried until one satisfies the
specified checks. A bad copy cannot hide a valid copy in another root. The first
valid candidate is reported. Manifest order determines report order.

| Status | Meaning |
| --- | --- |
| `FOUND` | At least one filename candidate satisfies every specified check. |
| `MISSING` | No candidate with that exact filename exists in the search roots. |
| `HASH MISMATCH` | Candidates exist, but all fail size or SHA-256 verification. The detailed reason explicitly distinguishes size from digest mismatch. |

Exit codes: `0` = every artifact found; `1` = one or more missing/mismatched;
`2` = invalid package/config/arguments or filesystem error. Inspection is
read-only: nothing is installed, downloaded, moved, linked, or executed.

## Installing missing artifacts

```bash
renderrepo install examples/wan2.2-i2v \
  --artifact-dir /path/to/AI_Models \
  --destination /path/to/AI_Models/RenderRepo/wan2.2-i2v
```

The destination is required and is automatically searched if it already exists.
Configured roots still use the same CLI/config/environment precedence as
inspection. Existing `FOUND` artifacts are reused wherever found; no duplicate
copy is created. Source selection is automatic from the ordered manifest list.
For a missing artifact without sources, the interactive CLI asks for a direct
URL or local file path. To manually select or override any source:

```bash
renderrepo install PACKAGE --destination ./artifacts \
  --source 'model.safetensors=https://example.org/direct-download' \
  --source 'input.png=/path/to/input.png'
```

Repeated `--source` options must name distinct declared artifact filenames.
Overrides apply to that invocation only and replace that artifact's entire
source list. Local paths resolve relative to the current directory. They may
contain spaces when quoted; file URIs are also supported.

All unresolved sources must be supplied before any transfer. The plan lists
each artifact, its source(s), exact size when supplied, expected SHA-256 status,
destination, and whether it replaces a destination file. Total known bytes and
unknown-size file count are shown before `Download N artifacts? [y/N]`.
Only `y` or `yes` approves. Decline, blank, or EOF returns 1 without network
requests or destination writes. `--yes` is explicit approval for automation;
missing sources still cause exit 1 without any downloads. Invalid configuration
or source syntax returns 2. Ctrl-C during transfer returns 130.

After approval, RR creates the destination and streams files sequentially in
1 MiB chunks into unique temporary siblings. It follows HTTP(S) redirects,
rejects HTML responses and incomplete responses when Content-Length is supplied,
checks every declared size/hash, and atomically renames the finished file to
its declared basename. Progress is printed every two seconds during transfer.
`--timeout` sets the positive network I/O timeout in seconds (default 60).
HTTP source failures, size failures, and digest failures try the next listed
source. Failed/interrupted temporary files are removed. Existing destination
files survive failed replacement attempts; files elsewhere are never replaced.
Successful files survive a partial batch failure and are reused on rerun.
Exit 0 means all artifacts are now satisfied; exit 1 means transfer failure or
cancelled/unresolved acquisition. Downloads do not yet resume byte ranges.

There is no metadata probe or artifact request before consent. Unknown lengths
are honestly reported as unknown. Without expected size/hash, a completed
download has only transfer-completeness assurance, not verified file identity.
The calculated SHA-256 is printed for every successful transfer. Authentication
for gated/private URLs is outside this version; supply an accessible direct
URL or a local file. URLs with embedded credentials are rejected.

Without `--comfy-dir`, this command acquires artifacts into one directory; it does not install
ComfyUI, custom nodes, or Python dependencies, configure ComfyUI search paths,
or render. The original workflow bytes remain unchanged.

## What the answer guarantees

Inspection answers whether the configured roots satisfy the declared external
file requirements of the manually authored package. The author must include
every required file; v0 does not infer arbitrary custom-node dependencies or
prove that the inventory is exhaustive. It does not validate ComfyUI/node
versions, Python dependencies, hardware capacity, graph execution, or whether
ComfyUI's own search paths are wired to the files found. A success is artifact
readiness, not a render success guarantee. Files should remain stable during
inspection; this is not a filesystem snapshot.

## Real reference package

`examples/wan2.2-i2v/workflow.json` is the user-supplied
`video_wan2_2_14B_i2v.json`, copied byte-for-byte. It includes an embedded Wan
subgraph. Its manifest declares two diffusion models, two LoRAs, one text
encoder, one VAE, and `video_wan2_2_14B_i2v_input_image.jpg` from `LoadImage`.
Both LoRAs remain declared because they occur in the exported graph, including
its turbo-mode branch. Runtime branch pruning is outside v0.

Model source URLs are transcribed from the export's Model Links note. Model
SHA-256 values were verified against the official HF file pages on 2026-10-07;
exact byte counts for the two diffusion models and two LoRAs come from their
Git LFS pointers. The VAE and encoder sizes remain omitted rather than rounded.
The source image URL is from Comfy-Org's workflow template input-file index.

Published model metadata references (replace `/resolve/` with `/raw/` to see
the small pointer; `/resolve/` downloads the actual model):

- [Wan 2.2 repackaged files](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/main/split_files)
- [Wan 2.1 text encoder](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/blob/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors)
- [Official input-file index](https://github.com/Comfy-Org/workflow_templates/blob/main/workflow_template_input_files.json)

The source image and model weights are not bundled. Real local inspection will
report them missing until supplied. Tests keep the real workflow and filenames,
use temporary small stand-ins with matching test-only checksums in a separate
manifest, and verify that the published checks reject the stand-ins. Download
tests transfer small files over a real localhost HTTP server; they do not
download models or prove model usability.

Signatures, accounts, trust systems, ratings, scanning, and RenderLab integration
are outside this version. Static repository catalogs are described below.


## Export and Comfy placement extensions

`pack WORKFLOW --output DIRECTORY` writes a new package; existing outputs are
refused. Workflow bytes are preserved exactly. Only referenced embedded subgraphs
are visited, each definition once. Known loader widget/API input names determine
selected model filenames; matching `properties.models` records supply URLs.
Named subgraph instance values are followed through explicit definition input
links to loader widgets. Each instance is inventoried separately. The original
workflow bytes remain unchanged. Stale model records are flagged and their URLs
are not assigned to a differently named selected model. Positional-only or
unresolved override bindings require manual inventory; RR does not infer a
variant from a similar filename. Locally captured hashes identify the files used
to author the package, rather than verifying an absent upstream/workflow digest.
Records on other loaders are retained conservatively. Notes are not scraped for
arbitrary download links. All exported branches remain inventoried. Arbitrary
custom-node code and dynamic file loads cannot be inferred; review is required.

Optional artifact `target` is a relative path starting `models/` or `input/`, with
basename equal to `filename`. No absolute paths, traversal, backslashes, or NULs.
Conflicting categories for one basename cause export failure; this v0 inventory
cannot express distinct artifacts sharing a filename. Local fingerprint selection
uses the same deterministic configured-root and filename order as inspection.
Missing local files have no invented checksum/size. Missing sources are reported.

Optional top-level fields:

- `inputs`: list of `{filename: relative/path, kind: image|video, mode: user|bundled}`.
  User slots are excluded from artifact satisfaction; they must be supplied at run
  time. `--bundle-input PATH` requires that exact path in a configured root, copies
  it into `assets/PATH`, and adds a verified artifact targeting `input/PATH`.
- `runtime`: metadata mapping. Export writes `compatibility: unverified` and
  `node_packages: [{id, recorded_version}]`. These are annotations, not exact
  simultaneous install constraints or a capture of the working runtime revision.
- `unresolved`: list of human-readable outstanding inventory/identity issues.
  Printed during pack, inspect, and Comfy installation; does not change the
  definition of declared-artifact satisfaction. A draft with unresolved entries
  is not an exhaustive execution guarantee.

Sources additionally support `package:assets/PATH` for bundled files. At install,
the resolved file must remain within the package. Ordinary local source paths
continue to resolve relative to the invoking process; packing does not publish
machine-specific paths as model sources. Provider URLs retain their exported
revision (often mutable `main`); publishers should pin revisions and supply hashes.

`install PACKAGE --comfy-dir ROOT` requires artifact targets and an existing ROOT.
It shows acquisition and placement before consent; decline/unresolved sources
cause no writes or HTTP requests. Existing matching models become file symlinks.
`--model-dir ROOT` selects a shared categorized model store and creates links into
Comfy's own model tree. Default workflow destination is
`ComfyUI/user/default/workflows`; `--workflow-dir` selects an explicit alternative.
Workflows are preserved byte-for-byte; conflicting existing workflow contents are
refused. Runtime code is never fetched or executed. Partial successful acquisitions
survive failures; placement is not a transactional install or an uninstall database.

Default workflow location reference:
https://github.com/Comfy-Org/docs/discussions/378

Real export fixtures are the user-supplied FLUX2, Krea2, Qwen Image 2.1 edit,
MiniMax H3, and SCAIL2 character-replacement JSONs under `tests/fixtures`, plus
the original Wan 2.2 example. Real models are not bundled or fetched in tests.

## Portable archives and installation receipts

Optional manifest `version` is a nonempty string; legacy manifests default to
`"0"` in receipts. `pack --version` defaults to `"0.1.0"`. Standalone manifest versions remain opaque strings. Repository catalogs use the
numeric ordering subset defined below; no dependency solver is implemented.
Quote version values in hand-authored YAML.

`pack WORKFLOW --output NAME.rrpkg` writes a ZIP, while other output names write
package directories. Members are only `manifest.yaml`, the declared workflow,
and files referenced by `package:` sources (the packer emits those only for
explicitly bundled inputs). Output files/directories are never intentionally
overwritten. Workflow bytes remain unchanged. Archives need no author-machine
paths to resolve bundled assets. Model URLs and their expected identities stay in
the manifest; source revisions may still require publisher review.

Both `inspect` and `install` accept `.rrpkg` files. They validate then extract into
a temporary directory which is removed when the invocation ends, including on
failure. No archive symlinks, special files, encrypted entries, absolute paths,
drive paths, traversal, backslashes, duplicate paths across case, or colons are
accepted. Maximum 4096 members and 1 GiB total declared unpacked bytes. Corrupt ZIP
members fail with an error. Inspection continues to answer whether the configured
machine roots satisfy artifacts; a bundled asset is available for installation
but is not implicitly counted as already installed on the machine.

Comfy installs record receipts under `<ComfyUI>/.renderrepo/receipts/<name-hash>.json`
using temporary-file + fsync + atomic rename. Receipt version 1 records:

- Package name/version and resolved Comfy root.
- Workflow path and SHA-256.
- Artifact filenames, resolved content locations, placement paths, byte counts,
  and actual SHA-256 values, including files without publisher checksums.
- `installed` or `partial` status.
- Managed placement path, type (file/link), original file digest or symlink text,
  and resolved parent directory.

A partial receipt is created after approval and before acquisition. On normal
completion/failure/interrupt, completed owned placements and artifact locations
are checkpointed. A receipt is not itself a write-ahead journal; a process killed
before checkpoint may leave untracked placements. GUI-backed installations also
have [durable jobs and worker recovery](DEVELOPMENT.md#state-approvals-and-limits);
direct CLI installs do not create that GUI history. Receipts
contain no paths to extracted temporary package assets. Completed weight files
are retained on partial failure. Package-local artifact acquisition always copies
bytes into durable input locations, never links to temporary extraction paths.

`list --comfy-dir ROOT` lists name, version, status, and workflow digest.
`remove NAME --comfy-dir ROOT` plans and confirms removal of registered owned
placements. It compares current file SHA-256 or exact symlink text and resolved
parent directory before removal, rechecking immediately before unlinking. Changed
placements, all model weight files, pre-existing identical workflow/input files,
and resources referenced by another receipt remain. Ownership of shared managed
placements is carried into subsequent package receipts, allowing the last package
to remove an unchanged placement. Removal deletes the package's receipt even when
modified files are preserved; it does not delete empty directories. Missing
placements are tolerated. No automatic cache garbage collection is performed.

Same-package reinstall preserves known ownership. An already registered different
workflow digest/version is refused; remove the old registration first. The previous
version's installs have no receipts and are not retroactively owned. `--destination`
acquires files only and does not register a Comfy install. Concurrent installation
and removal against a single root are not supported in v0.

Acceptance tests hand off an archive to a separate simulated receiver, delete the
author's files, then use declared HTTP sources plus bundled input bytes to install,
inspect, list, and remove. Separate cases verify shared ownership, modified-file
preservation, pre-existing-file preservation, partial receipts, consent, and malformed
archives. They use tiny artifacts rather than downloading real model weights.

## Repository sources and discovery v0

A repository is a local directory/file URI or HTTP(S) directory/index URI.
`source add/remove/list/enable/disable` manages a persistent YAML
`repositories: [{name, uri, enabled}]` config. `enabled` is boolean and defaults to
true when omitted. Disabled sources retain their caches but are excluded from
refresh, discovery, updates, and indexed installation.
Repository names use letters, digits, underscore, and hyphen and must be unique.
Explicit CLI source locations normalize relative to the invoking directory;
hand-authored relative config locations normalize relative to the config parent.
No default repositories are supplied.

`index DIRECTORY` enumerates its immediate `.rrpkg` files, validates each manifest,
and atomically writes `index.json` alongside them (or an explicit output in that
same directory). Example:

```json
{
  "index_version": 1,
  "packages": [{
    "name": "demo",
    "version": "1.0",
    "description": "Example personal workflow",
    "uri": "demo-1.0.rrpkg",
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "size_bytes": 12345
  }]
}
```

Version 1 requires every displayed field; unknown fields are rejected. Description
is a string, possibly empty; generation copies the optional manifest description
(`pack --description TEXT`) or uses an empty string. Legacy `index_version: 0`
remains readable and permits omitted descriptions. Digest is 64 hex digits;
archive size is a positive integer. Package name/version pairs must be unique,
including equivalent dotted versions. Catalog versions use one or more nonnegative
integer components separated by periods; no leading zeroes except `0` itself.
Trailing zero components are ignored for comparison. No prerelease syntax,
semantic-version ranges, or alphabetical comparison is used. Standalone manifest
versions remain nonempty strings; catalogs require the ordered numeric subset.

`refresh` (alias `update`) fetches enabled catalogs only, validates them, and
atomically caches resolved package entries by repository name and index-URI digest. Maximum fetched
catalog size is 8 MiB. HTTP indexes must return a complete HTTP 200 response when
Content-Length is available. Local and HTTP(S) sources work without provider SDKs.
HTTP catalogs cannot direct archive acquisition to local files. Failed source
refreshes preserve old cache bytes, are reported, and return 1; subsequent cache-only
searches can still use the previously fetched catalog, visibly marked `STALE`.
An atomic status sidecar records checked/last-successful UTC timestamps, failure
reason, and cache digest. It marks refresh-in-progress before fetching, so an
interrupted refresh remains stale. Missing/mismatched status also means stale. A
successful refresh clears it; elapsed age alone does not mark a cache stale.
Source configuration and individual refreshes use nonblocking POSIX advisory locks;
atomic writes fsync both file and directory. These locks coordinate RenderRepo
writers on a local Linux filesystem, not arbitrary external editors/network
filesystems. Other sources may refresh successfully during the same invocation. Removed/reconfigured sources are excluded
from discovery even when old cache files remain on disk.

`source list` shows enabled state and cache status. `search [QUERY]` prints cached
package names/versions/sources/descriptions and stale markers; query matching
is case-insensitive substring matching on name or description. `packages` is an
alias for search (no query lists all versions). `--repo NAME` filters refresh,
search, packages, and updates; unknown/disabled explicit sources fail.
`install NAME` resolves cached entries, selecting newest unless `--version` is supplied. Ambiguity across sources
requires `--repo NAME` rather than an implicit source-priority policy. Without a
cached entry, installation fails and advises refresh; it does not discover random
Internet sources. Local package paths retain their existing behavior.

Archive acquisition displays source, size, and expected SHA-256 for inspection; requesting install authorizes it
before requests/writes. A verified cached archive skips acquisition. `--yes`
explicitly approves the subsequent artifact/placement plan; interactive users see
that plan and confirm once before dependencies or code are installed. Archive size/hash are checked before extraction,
and manifest name/version must exactly match the selected catalog entry before
installing any artifact. A requested inspection reacquires corrupted cached archives before preparing the plan.
HTTP downloads retain the existing streaming, cleanup, and redirect constraints.

Repository installations add `{name, index_uri, uri, archive_sha256}` provenance
to the receipt. `updates --comfy-dir ROOT` compares receipts with cached catalogs;
`refresh --comfy-dir ROOT` also reports after refreshing. Repository-installed packages
compare only with the recorded repository name AND index URI; directly installed
packages report candidates separately by repository. Opaque installed versions are
reported as unordered rather than guessed. Partial installations are marked in
update output. Neither command fetches package archives/models nor changes installed
files. Equal versions with different bytes are not treated as upgrades.

Source config and cache defaults follow XDG_CONFIG_HOME/XDG_CACHE_HOME with
`~/.config`/`~/.cache` fallbacks; explicit `--sources` and `--cache-dir` override.
Index generation is shallow (no recursive archive discovery), and caches are not
automatically garbage-collected. Installing a replacement version requires explicit
removal of its prior registration followed by installation; this is update detection,
not an upgrade transaction. SHA-256 establishes integrity, not publisher identity.
No signatures/trust framework, source accounts, automatic upgrade execution,
provider crawling, remote publishing service, or dependency solver is introduced.

Local and HTTP command sequences are in the [CLI reference](CLI_REFERENCE.md#create-and-browse-an-independent-repository).
Validation uses disposable local packages and loopback HTTP servers:

```bash
python -m unittest discover -s tests -p test_repository.py -q
```

Fixtures cover generated indexes, local/HTTP discovery, disabled/conflicting sources,
malformed indexes, atomic failure/stale recovery, receipt updates, archive-hash
rejection, both approval stages, and installation through the existing path.
Refresh/search tests reject any artifact downloader or subprocess invocation.

## Runtime diagnostics and export provenance

`doctor PACKAGE --comfy-url NATIVE_URL` accepts directories/manifests/archives and
queries native ComfyUI `/object_info` with read-only HTTP requests. Only referenced
embedded subgraphs are walked. Editor-only Note/MarkdownNote/Reroute/PrimitiveNode
objects are excluded; other exported branches are checked conservatively, without
reconstructing execution topology or pruning runtime branches. Missing node names
are reported without guessing an installation repository.

Known model loaders use their widget/API input selection and the server's advertised
required/optional combo list. A local model may be FOUND while not visible to Comfy;
these states are distinct. Dynamic selections, unavailable combo inventories, and
unresolved subgraph overrides remain unknown. Arbitrary custom-node internal file
loads cannot be inferred. Explicit artifact directories/config use the existing
search rules; `--comfy-dir` adds existing models/input directories. Without local
roots, remote model visibility cannot prove local size/hash identity.

User input slots are checked against local supplied roots or remote loader combo
lists. Missing inputs are reported separately from nodes/models. A replacement input
can still be selected in Comfy, but the exported default does not count as supplied.
No authenticated server/session discovery, account handling, package execution, or
custom-node installation occurs in doctor. Caller-supplied native URL and local root
must describe the same installation; this tool does not attest that relationship.

Optional `pack --comfy-dir ROOT` adds `runtime.local_installation`:

- `comfy`: `{revision, dirty, source}` from ROOT's own Git checkout. Parent Git
  repositories are not incorrectly attributed to a non-repository child.
- `custom_nodes`: entries with `name` and known Git `{revision, dirty, source}`, or
  a full-file `sha256` for standalone `.py` nodes. Non-Git directories retain null
  revisions. Disabled directories, metadata entries, and caches are skipped.

Remote URLs omit embedded credentials, query strings, and fragments. Local machine
paths and exporter-Python versions are not falsely presented as runtime identities.
Dirty state accounts for tracked/untracked code according to Git ignores and
explicitly excludes RR's `.renderrepo` receipt directory. Uncommitted code is flagged
as unreproducible because the commit alone does not capture it. The inventory is an
environment snapshot, not a proven minimal set of custom-node dependencies; revision
comparison is deliberately conservative and can include unused installed extensions.
Runtime annotations in the exported graph remain separate evidence.

Doctor compares captured revisions and standalone code hashes when a local root is
supplied. Exit codes: 0 checked requirements satisfied, 1 known missing/mismatched
requirements, 2 malformed input/network/filesystem error, 3 unknown/incomplete checks.
Known failures take precedence over unknown checks. Runtime/Python dependency
compatibility and hardware fit are not inferred from successful node discovery.

Optional SDK execution is a separate path: `python -m renderrepo.sdk_execution --help`. It requires the optional SDK extra and a separately qualified Comfy API v2 deployment/proxy. It is not required for inspection, packing, repository management, doctor, import or the local-only contest demo. Historical operator scripts/runtime captures are excluded from this release; the retained SDK transport tests are mocked and are not provider or hardware qualification.

## Explicit custom-node requirements

`runtime.custom_nodes` is an optional list, separate from the provenance snapshot:

```yaml
runtime:
  custom_nodes:
    - name: ComfyUI-Example
      source: https://example.org/creator/ComfyUI-Example.git
      revision: 0123456789abcdef0123456789abcdef01234567
```

Each entry has exactly `name`, `source`, and `revision`. Names are safe single
folder names under `custom_nodes`; revisions are full lowercase 40-character Git
commits. Sources accept HTTP(S), SSH URLs, local file URLs, or absolute local Git
paths. Embedded passwords and HTTP credentials are rejected. Local paths are
legitimate personal sources but must be replaced with accessible sources for
portable publication. Mutable branches and tags are not dependency identities.

`pack --comfy-dir ROOT --custom-node NAME` explicitly selects a clean Git checkout,
records its origin and HEAD, and limits custom-node snapshot checks to the selected
repositories. `--node-source NAME=SOURCE` supplies an absent/replacement origin.
Names do not establish which workflow node types a repository provides; the author
must select every required implementation. Neither extraction nor packing imports
custom code to discover it.

`install --comfy-dir ROOT --install-nodes` displays the pinned code plan before
consent. Acquisition clones without checkout into staging, checks out the exact
commit with hooks/global Git configuration disabled, then places the repository.
Submodule installations are rejected. Existing modified or mismatched checkouts
are never replaced. New code is not imported by the installer.

An explicitly selected `--comfy-python PATH` runs `PATH -m pip install -r
requirements.txt` if that regular file exists. Dependency resolution is pip's;
RR pins repository code but does not claim a locked transitive Python environment.
Without PATH, a declared requirements file leaves status partial and exit 3. pip
failure records a partial installation and does not undo Python-environment changes.
Removal retains environment dependencies and compares owned Git trees before
removing them, including content, symlinks, modes, and HEAD but excluding Git metadata
and bytecode caches. Shared checkouts transfer ownership across package receipts.

After placement RR reports that a restart is required. Optional `--comfy-url` runs
read-only doctor checks, which may fail against a server that has not been restarted.
The installation remains recorded even when those subsequent checks fail. Doctor
compares explicit pinned custom-node revisions when a local Comfy root is supplied.


## ComfyUI bridge

The optional `extensions/comfyui_renderrepo` loader registers `/renderrepo` HTTP
routes and a frontend panel. The Python bridge delegates pack/install to CLI
subprocesses. `install --comfy-dir ROOT --dry-run` uses the normal preflight without
interactive source prompts, acquisition, receipts, or placements; unsatisfied
sources return 1, invalid plans return 2, and a satisfiable plan returns 0.
`--input-dir PATH` maps input artifacts to Comfy's configured input directory.

Uploaded archives are held in temporary server-side sessions. A reviewed session
pins archive bytes and installation options; changed options require review and
changed preflight results refuse the approved operation. An explicit approved
boolean starts one serialized background installation. Results expose exit status,
logs, workflow availability, and restart notices; a normal package receipt remains
the durable installation record. Workflow responses return the original editor
JSON after placement. API prompt graphs are rejected by the editor importer.

The frontend calls Comfy's native `app.loadGraphData` and `app.graph.serialize` for
editor import/export. It creates no alternate graph format or execution engine.
The bridge is local and single-user; loopback, same-origin, and request-header
checks are not a cloud authentication system. See the extension README for limits,
installation, and separate HTTP, interaction-harness, and real-browser checks.

## Optional task discovery metadata

Manifest schema 0 and index versions 0/1 accept an optional `discovery` mapping.
`title`/`summary` are text (at most 2000 characters); `tasks`, `tags`,
`required_inputs`, `examples`, `limitations` are lists of up to 50 nonempty text
items (at most 2000 characters each). Missing metadata is valid and uses legacy
names/descriptions. These fields are publisher claims, not verified capability,
quality, hardware or runtime guarantees. The new reader accepts old packages and
indexes; older strict readers may reject new metadata fields.

`pack --discovery JSON_FILE` and GUI export preserve the mapping; `index` copies it
to package entries. Search uses the explicit vocabulary in `discovery.py`, AND term
matching and tiered field ranking. Source/index URI plus package name defines a
listing identity; conflicting catalogs are not merged. Installation ownership still
uses the existing package-name receipt namespace, so conflicting packages cannot be
installed side by side under one name. Review protects the existing registration.

Requesting an indexed install/review now authorizes only bounded archive inspection;
there is no separate archive approval. Artifact/code installation still requires
the reviewed plan approval. Direct package URLs use the ordinary downloader with a
64 MiB inspection bound and redirect scheme checks; no independent digest is claimed
when none is supplied. Durable jobs retain the common review and installation path.


## Local readiness inventory and explicit runtime requirements

`runtime.requirements` optionally declares runtime constraints, independently of
`node_packages`, `local_installation` and publisher discovery descriptions:

```yaml
runtime:
  requirements:
    python_version: ">=3.13,<4"
    comfyui_version: ">=0.39.0"
    pytorch_version: ">=2.0"
    device_types: [cpu]
```

Requirements are a mapping with at most 30 fields. Version fields accept numeric
comparisons (`==`, `>=`, `<=`, `>`, `<`, comma separated). PyTorch local build
suffixes such as `+cu130` are excluded from numeric comparisons. Prerelease strings,
unsupported constraints, unrecognized requirement keys and unavailable observations
produce unknown, rather than a guessed match. `device_types` is a nonempty list of
acceptable runtime device types. This is the device Comfy is using, not a hardware
capacity certification. Other requirement values must be nonempty strings up to
200 characters. The exporter accepts `--runtime-requirements FILE`; captured
versions/revisions are never automatically promoted to requirements. Explicit
custom-node Git pins retain their existing meaning.

New `index` output includes an optional `dependency_inventory` per package. It is
computed from the archive manifest and used graph/subgraphs, and contains `nodes`
(type-name list), `models` (node/field/category/filename records; null filename means
an unresolved selection), `artifacts`, `inputs`, `runtime`, and `unresolved`.
Artifacts/inputs retain their manifest meanings, and selected media inputs are also
included when detectable in the graph. Updated readers validate this structure;
legacy indexes remain readable without it and yield unknown readiness. Older RR
readers that reject unknown index fields need updating to read these new catalogs.
The inventory is package-declared metadata supplied by the catalog, not proof that
the unrequested archive agrees with it. A package retrieval still requires its
normal archive verification and installation review.

New installation receipts retain the dependency inventory. An edited/missing
installed workflow cannot receive a ready result based on its old inventory.
Legacy receipts without inventories remain valid and return unknown. No installed
record or measured readiness state is synthesized from a matching package name.
Readiness results are transient, timestamped observations, not install receipts or
publisher certifications. All checks are read-only; no archive/artifact download,
Git acquisition, pip action, workflow opening or prompt submission is performed.

## Built-in local execution test profiles

Test locally does not add executable fields to package manifests. RR ships trusted,
fixed bindings for the three original starter workflows; support is checked against
installed graph structure/settings, known bundled input bytes and native built-in
node modules. A package name, publisher description, readiness result or authoring
fingerprint alone never authorizes execution. Package-provided commands, scripts,
probes, provider destinations and arbitrary custom-node test bindings are unsupported.
The existing v0 payload schema remains unchanged; the separate capsule descriptor
binds immutable portable identity. Local test plans/results live in a
separate installation-local durable store, and never promote observed execution into
static compatibility metadata or a general hardware claim.
