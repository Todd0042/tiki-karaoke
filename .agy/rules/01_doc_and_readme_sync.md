# Rule: Mandatory Documentation, README & Configuration Sync

## Objective
Prevent documentation rot. Every user-facing or technical modification to the Tiki Karaoke codebase must be immediately and accurately reflected in `README.md` and `config.example.json` in the same turn.

## Protocol
1. **Dual-Section README Integrity**:
   - `README.md` is structured in two distinct sections:
     1. **Part 1 — Consumer Overview & Quick Start** (plain-English features, party flow, visual tour, and installation).
     2. **Part 2 — Consolidated Technical Documentation & Architecture Reference** (in-depth DSP pipeline, WebSockets, database algorithms, storage profiles, and configuration schema).
   - If an addition or change affects user experience (e.g. queue controls, guest songbook options, TV stage UI), update **both** Part 1 and Part 2.
   - If an addition or change affects internal architecture (e.g. audio DSP nodes, LUFS target, DB queries, new CLI flags, or WebSocket event names), document the exact technical specification in Part 2.
2. **Configuration Synchronization**:
   - Every key added, modified, or deprecated in `config.py` (`DEFAULT_CONFIG`) MUST be immediately synchronized with `config.example.json`.
   - The default value and type in `config.example.json` must exactly match `DEFAULT_CONFIG`.
3. **No Unfinished Doc Drift**:
   - Never mark a feature or refactoring task complete if documentation updates are left as "future work". Docs and code are committed together.
