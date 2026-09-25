# Bundled model-viewer runtime

- Runtime: `model-viewer-4.3.1.min.js`, downloaded from the official Google-hosted 4.3.1 URL: <https://ajax.googleapis.com/ajax/libs/model-viewer/4.3.1/model-viewer.min.js>
- Upstream project and Apache-2.0 license: <https://github.com/google/model-viewer/tree/v4.3.1>
- The minified bundle includes Lit code marked BSD-3-Clause ([upstream](https://github.com/lit/lit/tree/v3.3.3)) and Three.js code marked MIT ([upstream](https://github.com/mrdoob/three.js)). Their license texts are preserved alongside the bundle and copied to each generated viewer output directory.
- SHA-256 of the vendored runtime: `283b0672384614b4847636c306fc93fe4b1fcadc76d668b4e47f0ca76bcf033b`
- SHA-256 of the upstream license texts: Apache-2.0 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`; Lit BSD-3-Clause `c94b94b40b85a5083dea825ae844b1cedb40dd425685362c135e143265dd6cd4`; Three.js MIT `8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc`.

`viewer_assets.py` copies the runtime and notices next to each generated HTML/GLB. The viewer should be served from that directory over localhost HTTP; WebGL and local-file browser restrictions still apply.
