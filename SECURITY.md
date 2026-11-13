# Security and execution boundaries

window-Stand User can observe and interact with Windows applications. Treat model output as untrusted and review requested actions before approving them.

## Desktop companion path

- Host input and focus operations request approval for the current action. Approval does not override separate risk permissions or target/focus checks.
- Background workspace tools are bounded to their designated directory. Legacy shell, unrestricted file and batch-input tools are blocked in the companion execution path.
- Pointer movement alone does not revoke approval. Use Stop or Ctrl+Alt+Esc to stop a task; changes to the target scene may still invalidate an action.
- Timers store task descriptions and do not preserve desktop approvals or coordinates. At execution time they re-observe and retain their configured environment. Interrupted operations are not automatically replayed.

These guarantees describe the desktop companion workflow. Direct library/CLI use and custom integrations may expose a different tool set; do not assume the same interactive approval broker exists in every entry point.

## Optional virtual machine

Only the project-owned VM may be managed by its lifecycle controls. Guest actions use a bounded JSON protocol; unsupported or failed guest actions do not fall back to host input. Model credentials remain on the host.

The VM uses explicit shared folders, including a writable output directory. NAT networking is not a guarantee that host or LAN services are unreachable. General-purpose virtualization is not a guarantee against malicious guest software. Windows guest installation and live agent execution remain pending end-to-end validation.

## Data and credentials

`.windows-use/` contains settings, Windows-protected API keys, history, timers, imported artwork and optional VM data. Do not publish it. Necessary task text, UI observations and enabled screenshots are sent to configured model providers. Windows-protected keys are bound to the local user; copies are not portable secrets backups.

Provisioning uses a dedicated random guest password. Guest installation media and guest auto-login may contain that password in recoverable form. It is not a password vault and must not reuse a personal account password.

## Reporting

Do not include API keys, private screenshots, databases, installer media or personal task traces in public issues. For security-sensitive reports, use GitHub private vulnerability reporting if the repository offers it; otherwise first ask the maintainer for a private contact channel without disclosing exploit details publicly.

For ordinary reproducible bugs, provide the version, Windows version, execution mode and a minimal redacted example. No broad security audit or cross-application compatibility guarantee is claimed.
