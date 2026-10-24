# Public repository and contest handoff

The source release is public at [github.com/crystalattice/CBI_RenderRepo](https://github.com/crystalattice/CBI_RenderRepo), on the `main` branch. The repository contains the CBI_RenderRepo application, its ComfyUI integration and workflow files, GPL-3.0-only license, setup instructions, tests and preserved third-party notices. Model weights and the ComfyUI runtime are not included.

GitHub history records repository setup, source upload and release maintenance. The private development history was not imported. The release manifest inventories the exact published source files and records the verification results below.

An anonymous clone of public commit `6adf3bdb1dbe9e375169895c591a091a588558f0` passed the README setup/test path with Python 3.13.16: Python 375 passed/1 skipped, Node 70 passed/1 skipped, and the no-model import/export check passed. This did not install the extension into a separate live ComfyUI instance or execute the contest graph.

## Remaining contest steps

- Optionally verify installation and opening from the public checkout in a separate live ComfyUI installation; the earlier browser acceptance used the local target and was not repeated from this clone.
- Publish the demo video of no more than two minutes. The current captioned take is 110 seconds, has no voiceover, and does not show GitHub. Its app behavior and workflow remain accurate: the public follow-up changed release documentation and ignore rules, not application behavior or workflow bytes. A new take could show the public checkout and add the narration you described. Graph execution, hardware suitability and performance remain untested.
- Publish a social post linking the video and including `#OpenSourceHiggsfield`.
- Cody must personally review and accept the entrant terms, confirm the one-submission requirement, provide the final links and complete the contest form.

The contest rules allow a local ComfyUI integration; a hosted try-it endpoint is optional. Do not describe model fingerprint reuse as upstream identity verification, or opening the graph as proof that it executes successfully.
