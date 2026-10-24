# Notices and attribution

Publisher/maintainer: **Chippie Bear Industries, LLC**. This identifies maintenance responsibility; it does not replace existing authorship or claim ownership of third-party material.

CBI-owned application source is licensed **GPL-3.0-only**, as authorized by Cody. The root LICENSE contains GNU GPL version 3, with the Free Software Foundation attribution preserved. Existing project SPDX declarations now say GPL-3.0-only. No blanket new copyright header was added, and third-party notices were not relicensed or overwritten.

- **Workflow templates:** the Z-Image JSON is a Comfy-Org workflow template adapted by CBI. The upstream MIT notice, copyright 2023-present Comfy Org, is retained in [third_party/workflow_templates-MIT.txt](third_party/workflow_templates-MIT.txt). Original JSON bytes and source notes remain intact. The other real-export regression fixtures and Wan example are retained for the test suite. Cody confirms the license information for the supplied workflows and other release contents; this is recorded owner confirmation, not a new independent provenance audit. Third-party workflow material retains its applicable terms.
- **PyYAML:** required runtime dependency, separately installed. Its MIT notice credits Ingy döt Net and Kirill Simonov; retained in [third_party/PyYAML-MIT.txt](third_party/PyYAML-MIT.txt). Its code/wheels are not vendored.
- **ComfyUI:** separate runtime prerequisite, with its own GPL v3 license and dependency notices. It is not bundled. The former GPL-2.0-only project declaration has been replaced by GPL-3.0-only at Cody's direction.
- **Test/optional dependencies:** aiohttp, jsonschema, Pillow and optional comfy-sdk distributions retain their own licenses and notices. They are installed externally, not copied into this tree. Mocked SDK tests do not establish real provider execution.
- **Assets:** inline UI vectors/CSS and procedural starter images retain their existing sources and authorship. Cody confirms the other license information. Real-export regression fixtures preserve original source notes. The minimized object_info fixture contains schema examples, not a private machine catalog or proof of target support.

No model weights, Comfy runtime, browser binaries, external adapter source, user images, recordings or third-party package distributions are included. Owner confirmation of licensing does not change the technical evidence: local package fingerprint matches do not establish upstream model identity, and graph execution, hardware suitability and performance remain untested.
