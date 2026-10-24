# CBI_RenderRepo

**Publisher and maintainer: Chippie Bear Industries, LLC.**

CBI_RenderRepo brings workflow package review, local dependency reuse, installation receipts and original-graph opening into ComfyUI. Import ordinary Comfy editor JSON or an RR package, inspect graph-derived inputs and controls, review the target-specific file plan, approve installation, then open the graph in Comfy.

The reviewed UI and terminal command remain **RenderRepo** / `renderrepo`; the Python module and S4 capsule profile also remain `renderrepo` for compatibility. The public project and Python distribution are **CBI_RenderRepo** (normalized by installers to `cbi-renderrepo`). No model weights, Comfy runtime or hosted service are bundled.

## Install and use

Supported/qualified scope: local Linux, Python **3.13+**, an existing working ComfyUI installation, Git and either uv or the selected Comfy environment's pip. Comfy/Torch/CUDA provisioning is separate; setup does not upgrade them.

From this checkout:

```bash
python3 scripts/setup_renderrepo.py --comfy-dir "/path/to/ComfyUI"
```

Inspect and approve the printed plan. Use `--python "/path/to/ComfyUI/.venv/bin/python"` if necessary. Restart Comfy with its normal launcher, reload its local browser page, and click **RenderRepo**. Keep this checkout for the extension link. See [the complete setup/use guide](docs/START_HERE.md) for manual setup, environment selection, catalogs, local models, downloads and troubleshooting.

For a portable, model-free first use, build the included procedural starter catalog with `python scripts/build_starter_catalog.py --directory "/path/to/new-starter-catalog"`, then add that directory's `index.json` through Manage → Sources. Explore a starter, review, Install and Open. Opening does not run it.

The contest recipe is [workflows/image_z_image_turbo.json](workflows/image_z_image_turbo.json). It is unchanged from the reviewed JSON and duplicated under tests/fixtures for regression coverage. Importing it is supported without acquiring models; missing files must remain missing in the plan. Do not treat its Markdown/BF16 links as selected dependencies or start a download to reproduce the local-only demonstration. See [workflow and demo limits](docs/DEMO_EVIDENCE.md).

## Test a fresh checkout

Use a separate environment; no running Comfy, GPU, private files or credentials are needed for these tests:

```bash
uv venv --python python3.13 .venv
uv pip install --python .venv/bin/python ".[test]"
.venv/bin/python -m unittest discover -s tests -q
PATH="$PWD/.venv/bin:$PATH" node --test tests/test_storefront_prompt.cjs tests/test_storefront_adapter.cjs tests/test_comfy_frontend.cjs tests/test_comparison_frontend.cjs tests/test_permutation_frontend.cjs
.venv/bin/python scripts/verify_release_import.py
```

Node.js with its built-in test runner is required for the five frontend suites (Node 22+ recommended; the exact tested version is recorded in the release manifest). `requirements-test.txt` records the tested dependency versions; install it before `uv pip install --no-deps .` to reproduce those versions. [Developer documentation](docs/DEVELOPMENT.md) explains isolated setup and verification boundaries. Test HTTP traffic uses disposable loopback fixtures and tiny synthetic payloads; SDK/execution transports are mocked. The contest graph is never queued by these commands.

## Contracts and evidence

- [CLI reference](docs/CLI_REFERENCE.md)
- [Package/index contract](docs/PACKAGE_V0.md) and [local S4 capsule profile](docs/S4_CAPSULE.md)
- [Additional permutation tooling](docs/PERMUTATION_V0.md), separate from installation
- [Sanitized demo evidence and uncertainty](docs/DEMO_EVIDENCE.md)
- [Release contents and validation](RELEASE_MANIFEST.json)
- [Public repository and contest handoff](docs/PUBLISH_LATER.md) — source publication is complete; demo and submission tasks remain

## License and publication review

Application source license: **GPL-3.0-only**; see [LICENSE](LICENSE). Cody authorized this license change and confirmed the other license information. Publisher/maintainer branding does not replace third-party authorship. Existing third-party notices are preserved; see [notices](THIRD_PARTY_NOTICES.md) and [the recorded release review](docs/REVIEW_BEFORE_PUBLICATION.md).

This public repository contains the locally prepared release. Technical checks do not constitute legal clearance or a promise that a workflow renders, fits memory or performs well. Graph execution, hardware suitability and performance of the contest workflow remain untested. The setup and test commands do not publish a demo video, create a social post or submit the contest entry.
