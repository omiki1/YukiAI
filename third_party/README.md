# Third-party components

The standalone product is implemented in app/src and scripts/yuki_*.py. Open-LLM-VTuber was read as a reference for model loading, emotion tags and history structure; its UI/backend are not imported. Its MIT notice is retained in Open-LLM-VTuber-MIT.txt as provenance for the earlier integration.

The Live2D model requires Cubism Core and Cubism Web Framework. The existing SDK is retained under app/vendor/live2d, together with LICENSE.md, redistribution list, and source copyright headers. The two raw-JSON parameter annotations in utils/cubismjsonextension.ts are corrected to accept native JSON for TypeScript checking; runtime behavior is unchanged.

Runtime uses the existing PyQt5/QtWebEngine, FastAPI, Uvicorn and OpenAI SDK installations. Build dependencies are locked in app/package-lock.json. Keep necessary SDK components and notices when deleting the reference application checkout. The new product includes no example characters or reference backgrounds.
