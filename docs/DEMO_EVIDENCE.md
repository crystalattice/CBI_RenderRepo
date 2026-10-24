# Reviewed workflow behavior and limits

The reviewed source was demonstrated locally on 2026-10-22. Explore → human picker import → fresh detail/plan review → reuse-only installation → Open into a new actual Comfy editor tab completed. The later real-browser captioned capture measures **110.000 seconds**, with SHA-256 `375ec5cbdf1cc20d5113d4de1a741960481b224a1abb71476f902308b24eed2f`. Captures, raw transcripts, session IDs and private paths are deliberately excluded from this source release. This is a concise account of observed behavior, not an independent reproduction certificate for every future target.

Included unchanged recipe SHA-256: `04e34c77bf2304f9cfea972db0c02e3fef3ffae88e59406e0121a58d7c48b9cd`. The same bytes are in workflows/ and tests/fixtures/. The local install and export preserved these bytes.

Graph facts: text-to-image intent using Z Image Turbo; positive text prompt and no input image; negative conditioning via ConditioningZeroOut rather than a separate negative prompt. The effective outer prompt is:

> Latina female with thick wavy hair, harbor boats and pastel houses behind. Breezy seaside light, warm tones, cinematic close-up.

The original also retains one trailing space. Outer controls override inner defaults: width 1024, height 1024, seed 196112838694424, steps 8 and the three selected files. Processing: text encoding → empty latent → sampling → VAE decode → SaveImage, prefix `z-image-turbo`. No resize/crop or image-input processing is established.

Expanded executable types: CLIPLoader, VAELoader, ConditioningZeroOut, VAEDecode, UNETLoader, CLIPTextEncode, EmptySD3LatentImage, ModelSamplingAuraFlow, KSampler, SaveImage. Annotation/wrapper nodes are excluded. On the demonstrated target those ten types were visible and registered in live object_info; this release's minimized schema fixture is not proof of another target's support.

| Exact selected file | Demonstrated target observation |
| --- | --- |
| z_image_turbo_int8_convrot.safetensors | Existing local file reused; package fingerprint matched |
| qwen_3_4b_fp4_flux2.safetensors | Existing local file reused; package fingerprint matched |
| ae.safetensors | Existing local file reused; package fingerprint matched |

Observed install: **three local reuses, zero downloads, no node/Python installation**. No weights are distributed here. Markdown/BF16 links are references, not the selected model files. The JSON supplies no upstream expected hashes. A local package fingerprint comparison does not prove upstream model identity. A separate audit found publisher metadata agreeing with two recorded local fingerprints; that technical audit did not independently establish the exact encoder’s upstream identity, and those observations do not rewrite the portable recipe.

**Graph execution, hardware suitability and performance remain untested.** Open does not Run. A new machine may import/analyze this recipe with all three models missing; do not infer availability, authorize a download, or promise exact model acquisition from this source tree. Cody’s confirmation of the other license information is recorded in the [release review](REVIEW_BEFORE_PUBLICATION.md); it does not change these technical evidence limits.
