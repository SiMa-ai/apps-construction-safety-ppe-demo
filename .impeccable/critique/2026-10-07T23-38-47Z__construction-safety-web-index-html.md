---
target: construction safety dashboard
total_score: 26
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 1
target_identity: "file:/workspace/apps-construction-safety-ppe-demo/construction_safety/web/index.html"
target_fingerprint: "sha256:86d562d1e7dfe1261c4957e4870831c9a3028b3c406dbf0fe6f31a435756df23"
target_path: /workspace/apps-construction-safety-ppe-demo/construction_safety/web/index.html
timestamp: 2026-10-07T23-38-47Z
slug: construction-safety-web-index-html
closed: true
---
Method: dual-agent (A: /root/design_review · B: /root/evidence_review)

Independent pre-polish review of construction_safety/web/index.html and its live dashboard.

| Heuristic | Score / 4 | Finding |
| --- | --- | --- |
| System status | 2 | Dashboard connection and video availability are easily confused. |
| Real-world match | 2 | Source, channel, and position numbering diverge. |
| User control | 3 | Pause, clear, cancel, and discard available. |
| Consistency | 3 | Shared controls work; routing terminology diverges. |
| Error prevention | 3 | Constrained inputs and deletion confirmation. |
| Recognition | 3 | Visible camera controls and route summary. |
| Efficiency | 3 | Direct settings and keyboard coordinate editing. |
| Minimalist design | 2 | Diagnostic detail delays incident review. |
| Error recovery | 2 | Playback and missing evidence lack guidance. |
| Help | 3 | Useful contextual instructions. |
| Total | 26/40 | Acceptable; hierarchy and status need refinement. |

The interface is specific to construction monitoring through its annotated video, zone geometry, and incident evidence. Those are its principal strengths. Diagnostic detail competes with the safety-review workflow, especially on mobile.

Priority issues:
- P1: Distinguish dashboard connectivity from playback. Add explicit unavailable/connecting viewer states and recovery guidance. Suggested command: polish.
- P2: Keep the requested meters but disclose diagnostic explanations and downloads. Add a direct route to incident review. Suggested command: distill.
- P2: Use consistent channel numbering in settings and overview. Suggested command: clarify.
- P2: Reduce reset emphasis and strengthen incident evidence hierarchy. Suggested command: polish.

Alex (frequent operator) must scroll past diagnostics to reach findings. Jordan (first-time presenter) must translate source/channel/position identifiers. Sam (keyboard user) benefits from coordinate controls but needs clearer unavailable-state feedback. The trust valley is a black or frozen camera alongside a global Connected label. Completed collection is visually quiet while reset is the strongest action.

Detector evidence: three CLI warnings: overused-font at dashboard.css:3 and index.html:0, and broken-image at index.html:0. The image is assigned dynamically in a closed dialog; the static warning is a false positive, though missing-image recovery is worth implementing. Preserve the incumbent font for this refinement. Browser scan also flagged long lines; repeated injected overlays caused false positives for dark glow/occlusion. No persistent human-visible overlay remains. The temporary detector server was stopped.

Browser assessment: no application exceptions or horizontal overflow at desktop, 390px, or 320px. Remaining limitations: no Safari/Firefox or screen-reader audio validation. UI validation does not establish end-to-end WebRTC performance.

Questions skipped: critique and polish were already requested; the established workflow supplies sufficient direction.
