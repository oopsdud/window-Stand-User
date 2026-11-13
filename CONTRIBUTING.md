# Contributing

Use Windows x64 for desktop and UI Automation development. Clone this repository and keep personal runtime data out of commits.

```powershell
uv sync --locked --extra desktop --extra dev
uv run pytest tests/test_timers.py tests/test_execution.py tests/test_vm.py frontend/tests
```

Use existing local mocks/offscreen widgets for tests. Real desktop input, live model calls and VM lifecycle experiments should be separate, explicit checks. Report which validation was simulated and which was performed on an actual guest.

## Pull requests

- Describe the user-visible problem and resulting behavior.
- Include focused validation and any known limitations.
- Preserve host approval checks and the prohibition on automatic guest-to-host fallback.
- Keep setup dependencies beside the checkout; avoid introducing system-wide changes or startup services by default.
- Update both README languages when changing onboarding or advertised features.
- Preserve upstream and third-party license notices.

To regenerate documentation screenshots:

```powershell
uv run python scripts/capture_readme.py
```

This uses actual widgets with fixture data and never needs a model API key. Review screenshots before committing them. The publication allow/exclusion categories are documented in [docs/PUBLISHING.md](docs/PUBLISHING.md).
