# RenderRepo profile of an S4 capsule, schema 1

This is RenderRepo's local capsule/profile contract. It does not assert conformance
to an external S4 schema or implement S4 middleware. No cross-application S4
specification or metadata vocabulary is defined in this repository. JSON is the
serialization; the rules here establish capsule semantics.

The structural schema is [s4-capsule-v1.json](../renderrepo/schemas/s4-capsule-v1.json).
The executable validator and hash rules are in `renderrepo/capsule.py`. Filesystem
membership, payload bytes, projections and forbidden target-state fields require
those semantic checks in addition to JSON Schema validation.

## Identity and compatibility

An RR capsule remains a directory or `.rrpkg` ZIP. It adds a UTF-8 JSON
`capsule.json` at the package root:

| Field | Rule |
| --- | --- |
| `format` | Exactly `"s4-capsule"`, a local format identifier. |
| `profile` | Exactly `"renderrepo"`. |
| `schema_version` | Integer `1`, the RR capsule descriptor/profile version. Unknown versions, profiles and fields are rejected. Boolean `true` is not version 1. |
| `files` | Complete sorted payload file inventory: `{path, size_bytes, sha256}`. |
| `payload_sha256` | Hash of that inventory, as specified below. |
| `claims` | Deterministic portable declaration projection of the RR payload manifest. |
| `observations` | Historical authoring provenance projection, separate from claims. No receiving-target observations are added here. |
| `provenance` | JSON object of known source records, empty if none. No publisher authentication is implied. |
| `predecessor` | Previous capsule's lowercase SHA-256, or JSON `null` for a root. |
| `s4` | Required JSON `null`. Reserved for future cross-application agreement; even `{}` is currently rejected. |
| `sha256` | Capsule content identity, calculated without this one field. |

The existing payload manifest still has `schema_version: 0`. These two schema
versions are independent. Existing fields, workflow bytes, artifact identities,
input definitions, discovery metadata, dependency pins and source declarations keep
their v0 meanings. New `pack` and editor-JSON imports write the manifest as **JSON
text at `manifest.yaml`**, retaining the filename for existing consumers. JSON is
accepted by the existing YAML reader. The capsule descriptor is always JSON.

Existing YAML v0 directories/archives remain importable without a capsule. Merely
inspecting, importing, reviewing or installing them never silently rewrites or
migrates their files. Generic `write_archive` can still transport an unsealed legacy
package; it does not silently declare it a capsule. Capsule-aware readers verify a
present descriptor and refuse tampering rather than falling back to legacy mode.
Older RR readers can read the preserved v0 payload but cannot enforce capsule rules.
Stripping a descriptor produces a legacy package; descriptor presence is not a
signature or an anti-downgrade mechanism. Repository archive hashes still protect
retrieved archive bytes where supplied.

Explicit migration copies all regular payload files, including supplementary files,
unchanged to a new destination and adds the JSON descriptor:

```bash
renderrepo capsule old-package.rrpkg --output migrated.rrpkg
# A directory or manifest path is also accepted as the input.
```

No output may overwrite its input or an existing destination. For migration from a
legacy package, the descriptor uses a null predecessor and empty provenance; its
identity is deterministically reconstructible from those legacy files. An unchanged
copy of an existing capsule preserves its identity and descriptor. Migration does
not invent a source, target ID or an observed compatibility result.

JSON-compatible v0 data is retained without changing the source bytes. Legacy
runtime extensions that contain target findings or a compatibility assertion other
than the historical `unverified` sentinel remain readable as legacy packages, but
cannot be promoted into profile-1 claims. Migration rejects them explicitly rather
than dropping their data or turning them into a qualification. Non-JSON YAML-only
values cannot be represented in the descriptor. A future legacy-observation migration
vocabulary is undecided.

## Exact hashes

All digests use SHA-256 and lowercase hexadecimal. The ZIP archive hash in catalogs
is **not** the capsule hash. Compression, timestamps, ZIP member order, file modes
and directory entries do not determine capsule identity.

`J(x)` is RR JSON canonicalization v1, defined by this exact reference operation on
JSON data in the supported Python runtime:

```python
json.dumps(x, sort_keys=True, ensure_ascii=True,
           separators=(',', ':'), allow_nan=False).encode('ascii')
```

Object keys are sorted lexicographically by Unicode code point; array order is
significant. No Unicode normalization occurs. Escaping, finite number formatting,
including the distinction between integer and floating point representations, follow
that reference operation. For example, `J({"b": [1, null], "a": "é"})` is the ASCII
byte string `{"a":"\u00e9","b":[1,null]}`. NaN/Infinity and duplicate descriptor keys
are rejected. This is an RR-local algorithm, **not** a claim of RFC 8785/JCS compliance.
Cross-language implementations must reproduce these bytes; adopting a shared S4
canonicalization standard would require a new profile/schema version.

1. Every regular file under the package root, except the root `capsule.json`, is a
   payload member. `manifest.yaml`, the unchanged workflow, bundled inputs and any
   supplementary examples are included. Referenced external model/code bytes are
   not included unless actually bundled; their declared hashes/sources are bound
   through the manifest, without pretending those external bytes were downloaded.
2. Each entry is `{"path": POSIX_relative_path, "size_bytes": byte_length,
   "sha256": SHA256(exact_file_bytes).hexdigest()}`. Sort the list by `path` using
   Unicode code point order. File bytes are not parsed, normalized, reserialized or
   given a trailing newline for this hash. Thus workflow/manifest whitespace changes
   change payload identity, while descriptor whitespace/key order do not.
3. `payload_sha256 = SHA256(b"renderrepo:s4:payload:v1\n" + J(files)).hexdigest()`.
   This hashes the complete inventory with member byte digests; it is not a bare
   concatenation of files or a hash of the ZIP stream.
4. Let `body` contain **all** required descriptor fields except the top-level
   `sha256`. Then `sha256 = SHA256(b"renderrepo:s4:capsule:v1\n" + J(body)).hexdigest()`.
   The body includes `files`, `payload_sha256`, claims, observations, provenance,
   predecessor, format/profile/version and reserved `s4:null`. No other fields are
   omitted. The descriptor is excluded from the payload, so neither digest is
   self-referential.

Safe relative paths, distinct case-folded names, regular files and no symlinks are
required. Empty directory structure is not semantic payload. Adding/removing an
unlisted supplementary file, altering any member, forging the projection, changing
the predecessor or changing descriptor metadata without its hash invalidates a
capsule. Archives keep the existing file count/unpacked-size restrictions.

## Claims, provenance and target observations

The raw v0 payload is retained for compatibility; its portable interpretation is
restricted by the descriptor projection. `claims` copies the validated v0 manifest,
with these exceptions:

- `runtime.local_installation` is removed and preserved as
  `observations.authoring_runtime`. This is the existing historical Git/code snapshot
  of the author's Comfy checkout, not evidence about a receiving target. Its old
  source/version/dirty/hash values are retained. When a legacy snapshot lacks a
  stable target identity, no such identity is invented.
- `runtime.compatibility: "unverified"` is omitted from claims. It is a legacy
  sentinel, not an observed outcome. Other compatibility values are rejected for
  profile-1 capsules.
- Runtime declarations are limited to the existing `node_packages`, `requirements`
  and `custom_nodes` fields. Explicit requirements constrain a target; they are not
  findings that the target satisfies them. Static graph capability descriptions
  remain descriptions, not availability, execution or hardware evidence.

Structured target-state fields such as availability, installed status, compatibility,
execution results, performance and `target_check` cannot be added to the portable
claims projection. This validates structured fields, not the truth of arbitrary
publisher prose. Capsule identity establishes content integrity, not truth, trust,
licensing or execution qualification.

Receiving-target availability, plan/reuse decisions, installation state, approvals,
compatibility, execution and performance remain in RR's existing Comfy-root-bound
review/jobs, receipts, readiness reports and local execution-test records. These
records are not capsule payload. Receipts now reference the installed capsule
identity and known derivative source provenance; receiving a new observation never
rewrites a capsule. Existing receipt format/version and removal ownership rules are
unchanged. Legacy receipts remain readable.

Original manifest sources and authoring provenance remain bound by the payload.
Existing repository receipt/job provenance continues to identify the acquired
archive. Import does not add an observed retrieval location to someone else's
immutable capsule. A backend-authored node-pin derivative additionally preserves
known upstream repository/direct-link/import records in its new descriptor's
`provenance.source_records`, with the relevant predecessor capsule hash. These are
known source records, not authenticated S4 assertions. A derivative does not reuse
the original publisher archive's digest as its own repository identity.

## Revision rules

A sealed capsule is immutable. RR checks payload hashes on load; rewriting its
manifest/workflow/example in place makes it invalid. `seal` creates a descriptor
exclusively and never overwrites it. Archive writes also refuse an existing output.
This is a content-integrity and backend-write rule, not filesystem write protection
or a distributed history service.

For a changed export with a known predecessor:

```bash
renderrepo pack changed-workflow.json --name example --version 0.2.0 \
  --predecessor previous.rrpkg --output next.rrpkg
```

The predecessor is verified first. The new capsule retains its predecessor hash
and known portable source provenance. Its new files/declarations/authoring snapshot
receive new hashes. Human package `version` is still the existing opaque/numeric
version used by the applicable RR command; it is not capsule identity. Multiple
capsule revisions can share a human version. An installation registered with one
capsule hash refuses a different capsule under that same package name until the
existing receipt-aware removal/review path is used. Identical capsule reinstall
continues to work.

`copy_capsule(source, new_output, edit=...)` is the backend operation for edits:
validate parent, copy into new staging, apply the edit, seal with the predecessor
hash, validate, publish to a new destination. Source bytes are never changed. For
an unsealed legacy parent, its predecessor identity is the virtual root descriptor
from the same hash/projection rules (null predecessor, empty provenance); it can be
reconstructed or explicitly migrated from the retained original bytes.

The bridge's node-source resolver uses this operation and returns a **new review
session**, without transferring approval. The original session and its uploaded
archive remain retained. Original workflow bytes and known import provenance are
preserved. Artifact download-source overrides, Python opt-in and target selection
findings remain review-local options; they do not rewrite the portable manifest.
Starter-catalog supplementary examples are added to a new linked capsule revision,
rather than appended to a sealed ZIP.

`pack` without `--predecessor` starts a new root. RR has no global lineage registry
and cannot infer that an arbitrary new export is related to an earlier package.
Callers must supply known predecessors. A hash link alone does not prove authorship,
authorization, ancestry completeness or predecessor availability. There is no chain
resolver, signature, global revision numbering or automatic upgrade transaction.

## Existing behavior, changes and undecided scope

| Area | Existing behavior retained | New profile rule |
| --- | --- | --- |
| Package/import | v0 manifest, original JSON graph, safe ZIP, source declarations | JSON descriptor and verified portable identity; legacy imports remain unchanged |
| Export | conservative dependency discovery, explicit pins, bundled media, no output overwrite | JSON manifest syntax at legacy filename; new exports sealed by default |
| Hashes | artifact SHA-256, archive SHA-256, retained tree/approval hashes | Distinct domain-separated payload and capsule hashes, including all payload files |
| Review/install | target checks, scoped authorization, leases, recovery and receipts | Capsule identity in responses/receipts; node-pin edits create linked review sessions |
| Repositories | current archive size/digest, source/index URI and version rules | Loading/indexing a capsule verifies it; no new index schema or publishing API |
| Observations | Comfy-bound readiness/execution and authoring Git snapshots | Portable claims exclude authoring observations and receiving-target findings |

Undecided: cross-application S4 metadata meanings/values, a shared S4 identifier and
canonicalization registry, signatures/trust, chain discovery, target-independent
execution interchange, and migration of nonstandard legacy runtime findings.
Reserved `s4` remains null until a separate contract defines it. Nothing here adds
S4 middleware, remote target discovery, execution support, a dependency solver or
hardware qualification.

## Validation

The targeted compatibility command below passed 187 tests, including the JSON
Schema check with `jsonschema` available.

`tests/test_capsule.py` covers explicit legacy migration, byte-preserving import,
JSON exports, unknown versions/reserved metadata, canonical hash inputs, ZIP
independence, immutability, linked revisions/source provenance, supplementary
payloads and separation of historical observations. It optionally checks the
published schema using `jsonschema`. Bridge tests cover a changed target without a
changed capsule, a retained immutable parent/new review session for node resolution,
and receipt identity after installation. Package/archive/runtime/repository tests
exercise the existing backend paths.

```bash
PYTHONPATH=tests python -m unittest test_capsule test_pack test_archive_receipts \
  test_runtime test_storefront test_comfy_extension \
  test_core_image_demo test_repository test_installation_history -q
```

The unrelated existing starter-catalog search assertion for `crop to aspect ratio`
returns two matches where it expects one; the same failure reproduces on unchanged
HEAD. The capsule tests separately verify the updated starter builder's sealed
supplementary payloads without changing that search behavior.
