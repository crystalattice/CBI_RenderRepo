# CBI_RenderRepo development and verification

The publisher/maintainer is Chippie Bear Industries, LLC. Runtime identifiers remain `renderrepo`; see the [README](../README.md) for the public distribution name.

Follow [README testing](../README.md#test-a-fresh-checkout) in a fresh Python 3.13+ environment. Required runtime dependency: PyYAML. The `test` extra adds aiohttp for the bridge fixtures, jsonschema for descriptor conformance and Pillow for mocked image/SDK transport tests. No model, provider SDK account or native Comfy is required for the standard suites. The optional `sdk` extra is separate and is not required by this release/demo.

For the pinned test environment, run `uv pip install --python .venv/bin/python -r requirements-test.txt`, then `uv pip install --python .venv/bin/python --no-deps .`. Use a non-editable install to verify wheel assets and preserve the checkout. Run tests from this checkout; tests intentionally import sibling fixture modules. The Node prompt regression launches `python`, so put the same environment's bin directory first on PATH.

## Extension integration

`extensions/comfyui_renderrepo` supplies the Comfy entry point and launcher. `renderrepo/comfy_extension.py` supplies the local HTTP bridge; the wheel includes the storefront, starter assets, schemas and backend. Both package installation and extension wiring are required. The [user guide](START_HERE.md#1-install-renderrepo) documents setup and restart.

Reviews and target checks do not submit graphs. Ordinary editor JSON preserves original bytes; effective nested bindings drive graph analysis. A review scope is bound to its identity/options/target and explicit approval. Missing dependencies remain unresolved. Code/Python requirements have their own approval scope; SDK/local execution tools are additional opt-in paths and are not used in the contest demo.

## State, approvals and limits

State belongs beneath the selected Comfy root: `.renderrepo/repositories`, `.renderrepo/jobs`, `.renderrepo/receipts`; installed workflows use `user/default/workflows`. Paths are chosen by the operator. POSIX leases and atomic records coordinate workers. Closing the UI does not cancel an approved installation; server restart does not authorize a new one. Expanded scope requires renewed review. Removal preserves model weights, modified/pre-existing/shared placements and installed Python dependencies.

The HTTP bridge is for local/same-origin Comfy, not public multi-user hosting. Node registration and file availability are target observations. Installation, editor opening, graph execution and hardware performance are distinct evidence. Capsule semantics and deterministic hashing remain in [S4_CAPSULE.md](S4_CAPSULE.md); no external S4 middleware is implemented.

## Release-specific checks

`python scripts/verify_release_import.py` uses the actual raw-JSON bridge importer with a disposable Comfy-shaped layout, empty model roots, recorded/minimized node input-schema fixture, and denied non-loopback transport. It verifies the effective outer prompt, seven controls, recursive node list, immutable capsule/export and preserved workflow bytes. It does not install or execute the recipe, certify a native Comfy target or infer node support from model files. Real native-Comfy acceptance of the reviewed source is summarized separately in [DEMO_EVIDENCE.md](DEMO_EVIDENCE.md).

The release omits historical capture/SDK/operator scripts, prototypes, native runtime snapshots and tests dedicated solely to those excluded helpers. Core package, install/recovery, graph, bridge, storefront, comparison/permutation and mocked SDK tests are retained. Exact inclusion/exclusion and validation are recorded in [the release manifest](../RELEASE_MANIFEST.json).
