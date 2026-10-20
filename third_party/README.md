# Third-party components

The UI embeds a Chinese glyph subset of [Fusion Pixel Font](https://github.com/TakWolf/fusion-pixel-font), copyright (c) 2022 TakWolf, under the SIL Open Font License 1.1. The subset is `app/src/fonts/fusion-pixel-12-ui.woff2`; the full notice is retained in `app/src/fonts/fusion-pixel-OFL.txt` and copied to portable bundles under `dependency-licenses/fusion-pixel/OFL.txt`. It is used for pixel-theme titles and controls; story text and replies retain the normal reading font. This public UI font is independent of all private character assets.

The standalone product is implemented in app/src and scripts/yuki_*.py. Open-LLM-VTuber was read as a reference for model loading, emotion tags and history structure; its UI/backend are not imported. Its MIT notice is retained in Open-LLM-VTuber-MIT.txt as provenance for the earlier integration.

The Live2D model requires Cubism Core and Cubism Web Framework. The existing SDK is retained under app/vendor/live2d, together with LICENSE.md, redistribution list, and source copyright headers. The two raw-JSON parameter annotations in utils/cubismjsonextension.ts are corrected to accept native JSON for TypeScript checking; runtime behavior is unchanged.

Runtime uses the existing PyQt5/QtWebEngine, FastAPI, Uvicorn and OpenAI SDK installations. Build dependencies are locked in app/package-lock.json. Keep necessary SDK components and notices when deleting the reference application checkout. The new product includes no example characters or reference backgrounds.

Optional game text capture invokes the official Textractor 5.2.0 CLI as a separate local process. Textractor is GPL-3.0: https://github.com/Artikash/Textractor. The installer downloads its official release and checks a pinned SHA256; its binaries, injected libraries, dependencies and reference checkout remain excluded from this repository. No Textractor engine source has been copied into the application. The original UTF-16 CLI framing is documented in https://github.com/Artikash/Textractor/blob/v5.2.0/host/CLI/main.cpp. Runtime tools retain their upstream licensing.
