# ライセンスと取得物

このリポジトリのコードは MIT または Apache-2.0（[LICENSE-MIT](../LICENSE-MIT)、[LICENSE-APACHE](../LICENSE-APACHE)）です。
`agent-chat-ui/` は upstream（[langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui)）の MIT ライセンスで、変更箇所は「UI の変更点」に記載しています。

次のものはこのリポジトリに含めていません。セットアップスクリプトや事前準備で各配布元から利用者の環境へ取得し、それぞれのライセンス・規約に従います。
ライセンスは 2026-10-04 時点で各リポジトリ・モデルページの表示を確認したものです。利用前に配布元で最新の表示を確認してください。

| 名称 | 取得するもの | 取得方法 | ライセンス |
|---|---|---|---|
| [eedali/LM_Connect](https://github.com/eedali/LM_Connect) | ComfyUI カスタムノード（OpenAI 互換の LLM クライアント） | `setup-comfyui.ps1` | リポジトリにライセンスの表示なし |
| [cubiq/ComfyUI_IPAdapter_plus](https://github.com/cubiq/ComfyUI_IPAdapter_plus) | ComfyUI カスタムノード（IP-Adapter） | `setup-comfyui-refs.ps1` | GPL-3.0 |
| [Fannovel16/comfyui_controlnet_aux](https://github.com/Fannovel16/comfyui_controlnet_aux) | ComfyUI カスタムノード（DWPose / 深度などの前処理） | `setup-comfyui-refs.ps1` | Apache-2.0 |
| [xinsir/controlnet-union-sdxl-1.0](https://huggingface.co/xinsir/controlnet-union-sdxl-1.0) | ControlNet Union promax | `setup-comfyui-refs.ps1` | Apache-2.0 |
| [h94/IP-Adapter](https://huggingface.co/h94/IP-Adapter) | IP-Adapter Plus SDXL、画像エンコーダ（OpenCLIP ViT-H/14） | `setup-comfyui-refs.ps1` | Apache-2.0（画像エンコーダの元の [laion/CLIP-ViT-H-14-laion2B-s32B-b79K](https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K) は MIT） |
| [yzd-v/DWPose](https://huggingface.co/yzd-v/DWPose) | DWPose のポーズ推定モデル（ONNX） | `setup-comfyui-refs.ps1` | Apache-2.0 |
| [depth-anything/Depth-Anything-V2-Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small) | 深度推定モデル | `setup-comfyui-refs.ps1` | Apache-2.0 |
| [python:3.12-slim](https://hub.docker.com/_/python) / [rust:1.88-slim](https://hub.docker.com/_/rust) | コード実行用のコンテナイメージ（Debian slim） | `setup-sandbox.ps1` | Python は PSF License、Rust は MIT / Apache-2.0、Debian の各パッケージはそれぞれのライセンス |
| [comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders) | T5-XXL fp8（Chroma のテキストエンコーダ） | `setup-comfyui-chroma.ps1` | Apache-2.0（元の google/t5-v1_1-xxl） |
| [lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD) | VAE（`ae.safetensors` として保存） | `setup-comfyui-chroma.ps1` | Apache-2.0 |
| Chroma1-HD 拡散モデル | [lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD) または Civitai | 事前準備（手動） | Apache-2.0 |
| [huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF) | LLM（Q4_K_S と mmproj。IQ3_M に再量子化して使う） | `setup-llm.ps1`（`tools\models\llm`） | 配布ページで確認 |
| [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI) | ComfyUI 本体 | `setup-comfyui.ps1`（`tools\comfyui`） | GPL-3.0 |
| [PyTorch](https://pytorch.org/)（AMD の ROCm 版 [repo.radeon.com](https://repo.radeon.com/rocm/windows/)、または CUDA / CPU 版） | ComfyUI の Python 環境 | `setup-comfyui.ps1` | BSD-3-Clause（ROCm の各ライブラリはそれぞれのライセンス） |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub)（`hf` コマンド） | Hugging Face のキャッシュへの取得（`hf` が無ければ uv が一時的に用意する） | 各 setup スクリプト | Apache-2.0 |
| [Tor Expert Bundle](https://www.torproject.org/download/tor/) | tor.exe（チャットタブの検索） | `setup-tor.ps1`（`tools\tor`） | BSD-3-Clause |
| [PrismML-Eng/llama.cpp](https://github.com/PrismML-Eng/llama.cpp) | llama-server（Vulkan。27B のルータと検索モデルの実行）、llama-quantize | `setup-llamacpp.ps1`（`tools\llama-prism`） | MIT |
| [prism-ml/Bonsai-8B-gguf](https://huggingface.co/prism-ml/Bonsai-8B-gguf)、[Ternary-Bonsai-8B-gguf](https://huggingface.co/prism-ml/Ternary-Bonsai-8B-gguf)、[Bonsai-4B-gguf](https://huggingface.co/prism-ml/Bonsai-4B-gguf)、[Ternary-Bonsai-2-27B-gguf](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf) | 検索モデル | `setup-search-models.ps1`（`tools\models`） | Apache-2.0 |
| [Override-6/Ternary-Bonsai-2-27B-abliterated-gguf](https://huggingface.co/Override-6/Ternary-Bonsai-2-27B-abliterated-gguf) | 検索の代理リーダー（PTQ1_0） | `setup-search-models.ps1` | Apache-2.0 |
| [mradermacher/Qwen3.5-4B-heretic-GGUF](https://huggingface.co/mradermacher/Qwen3.5-4B-heretic-GGUF)、[Qwen3-1.7B-heretic-GGUF](https://huggingface.co/mradermacher/Qwen3-1.7B-heretic-GGUF)、[Qwen3-0.6B-Heretic-GGUF](https://huggingface.co/mradermacher/Qwen3-0.6B-Heretic-GGUF) | 検索モデル | `setup-search-models.ps1` | Apache-2.0 |
| [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2) | Qwen3-VL-4B fp8（Krea 2 のテキストエンコーダ）、qwen_image_vae | `setup-image-models.ps1` / `.sh`（v0.12.0） | Krea 2 Community License（配布ページで確認） |
| [circlestone-labs/Anima](https://huggingface.co/circlestone-labs/Anima) | Qwen3 0.6B（Anima のテキストエンコーダ）、qwen_image_vae | `setup-image-models.ps1` / `.sh` | circlestone-labs Non-Commercial License（非商用） |
| Wulver（Krea 2 の派生、[Civitai](https://civitai.com/models/2881657)）、Indigo Furry Mix Anima（[Civitai](https://civitai.com/models/2787288)） | 拡散モデル | 事前準備（手動） | それぞれ元のモデルのライセンス（Krea 2 Community License、Anima の非商用ライセンス）と配布ページの条件 |
| [ml-explore/mlx-lm](https://github.com/ml-explore/mlx-lm) | macOS の MLX での推論（`tools/mlx/.venv`） | `setup-mlx.sh`（v0.12.0） | MIT |
| MLX 版の LLM（例 [AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit](https://huggingface.co/AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit)） | macOS で MLX を優先するときのモデル | `setup-mlx.sh --models`（任意） | Apache-2.0（配布ページで確認） |
| [Homebrew の tor](https://formulae.brew.sh/formula/tor)、PrismML fork の macOS arm64 版 | macOS の Tor と llama-server | `setup-tor.sh`、`setup-llamacpp.sh` | BSD-3-Clause、MIT |
| チェックポイント（yiffInHell、Rekemono など）、LoRA | Civitai などから入手 | 事前準備（手動） | 配布ページで確認（生成物や商用利用に条件があることが多い） |

- 例外として、Chroma1-HD の公式ワークフロー `ComfyUI_Chroma1-HD_T2I-workflow.json`（Apache-2.0、[lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD)）は参照用に `workflows/reference/` へ無改変で同梱しています（出典は同フォルダの README）。
- ComfyUI_IPAdapter_plus（GPL-3.0）は ComfyUI のプロセスに読み込まれるノードです。このリポジトリはそのコードを含まず、import もしません（API 形式のワークフロー JSON でノード名を指定するだけです）。
- LM_Connect はリポジトリにライセンスの表示がありません。このリポジトリは再配布せず、利用者の環境へ clone するだけです。
- 生成物の扱いは、使用したチェックポイント・LoRA・LLM の規約と、公開先の規約に従ってください。
