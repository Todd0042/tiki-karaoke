# Tiki Karaoke Project Rule: Vibe-Coding & Alcohol Safeguards

## Trigger
Whenever the user mentions having consumed alcohol in any way (drinking, drunk, tipsy, beers, wine, shots, intoxicated, vibe coding while drinking):
- This rule takes immediate effect for the rest of the session (minimum 12 hours).

## Guardrails
1. **Always Present a Plan First**: Never write or modify code directly. Always present an implementation plan and wait for explicit user confirmation before executing.
2. **Double-Check Logic & Typos**: Scrutinize the technical logic of instructions, look past typos/garbled syntax, and verify architectural sanity.
3. **Explore Alternatives**: Identify the root problem behind the prompt. Present 2–3 alternative approaches with trade-offs (e.g. simple patch vs. sustainable architectural fix).
4. **Ask Probing Questions**: Ask targeted questions regarding edge cases, state persistence, UI implications across devices, or unintended side effects before proceeding.

---

# Tiki Karaoke Project Rule: Documentation Integrity & README Sync

## Trigger & Scope
Applies whenever any new feature, user-facing workflow, configuration option, API route, or architectural change is introduced or modified.

## Guardrails
1. **Mandatory README Audit**: In the final step of any feature addition or architectural refactor, proactively audit `README.md` against the changes made.
2. **Immediate Documentation Update**: If the change affects setup steps, dependencies, CLI flags, host controls, URLs, or features, update `README.md` immediately in the same turn/commit.
3. **Accuracy & No Feature Drift**: Ensure tables (URLs, ports, credentials), feature highlights, and troubleshooting guides reflect the true, tested state of the codebase.

---

# Tiki Karaoke Project Rule: Windows & Docker Windows Build Command

## Trigger & Scope
Whenever instructing, reminding, or advising the user on how to compile or build Tiki Karaoke on Windows or inside the Docker Windows environment (`WinBoat`):

## Mandatory Rule
Always instruct the user to run the build script using this exact command:
```powershell
powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
```
Never omit `-ExecutionPolicy Bypass` or advise running `.\build_windows.ps1` directly without it, ensuring PowerShell script execution policies never block compilation.

