# Custom log formats

Put team-specific log-format files (`*.yaml`) in this folder, then restart VeriMind
(`stop` then `start`). They are loaded in addition to the built-in formats. A file named like a
built-in format (for example `vcs.yaml`) replaces it.

How to write one: [docs/log-formats.md](../docs/log-formats.md). A good starting point is a copy of
[src/verimind/engine/formats/inhouse.yaml](../src/verimind/engine/formats/inhouse.yaml).
