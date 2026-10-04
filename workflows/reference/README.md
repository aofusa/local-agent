# workflows/reference

`ComfyUI_Chroma1-HD_T2I-workflow.json` is the official Chroma1-HD text-to-image workflow (ComfyUI UI format),
copied unmodified from [lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD)
(`ComfyUI_Chroma1-HD_T2I-workflow.json`, retrieved 2026-10-04). It is licensed under the
[Apache License 2.0](https://huggingface.co/lodestones/Chroma1-HD) by its authors.

It is a reference only: LangGraph never submits it. `scripts/build_workflows.py` builds the `workflows/flux/`
templates from the same node choices (CLIPLoader type chroma, T5TokenizerOptions, ModelSamplingAuraFlow shift 1.0,
euler / beta, Flux VAE, EmptySD3LatentImage). Differences are listed in
`docs/chroma-hd-support-work-instruction.md` §11.3.
