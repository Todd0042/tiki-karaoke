# Rule: Real-World Verification Over Synthetic Unit Tests

## Objective
Prevent false confidence from shallow or mocked unit tests. Ensure real-world operational reliability across the FastAPI server, Web Audio DSP, WebSocket coordinators, and multi-display stage loops.

## Context
Headless GitHub Actions runners lack hardware audio sinks, display servers, and live network tunnels, making headless unit tests notoriously brittle and prone to false negatives. Furthermore, mock-heavy tests frequently pass 100% while real-world server startup, browser connection, or audio routing completely fail.

## Protocol
1. **GitHub Actions CI Scope**:
   - Continuous Integration workflows (`.github/workflows/ci.yml`) strictly focus on deterministic linting, configuration drift validation (`scripts/validate_config.py`), and packaging integrity (`makepkg --printsrcinfo`).
   - Synthetic unit tests are intentionally omitted from GitHub Actions to prevent runner hangs and false alarms.
2. **Local Operational Verification**:
   - Before declaring any feature, refactor, or bugfix complete, agents and developers must execute **real-world verification** locally:
     1. **Process Startup & Port Binding**: Launch `python3 main.py --help` or test server initialization to verify cleanly imported dependencies and schema migration.
     2. **Config Drift Check**: Run `python3 scripts/validate_config.py` to ensure zero drift between code, example configs, and docs.
     3. **UI & Route Health**: Verify that HTML templates, static bundles, and WebSocket handshakes resolve without 404s, 500s, or browser console exceptions.
3. **Packaging Sanity**:
   - When bumping versions or modifying packaging metadata, verify that `makepkg --printsrcinfo` in `packaging/aur/` produces a bit-for-bit identical match with `packaging/aur/.SRCINFO`.
