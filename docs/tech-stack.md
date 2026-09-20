# Technology and architecture

| Layer | Choice | Purpose |
|---|---|---|
| Runtime | Python 3 | Standard-library storage, subprocesses, and CLI |
| Terminal UI | Textual 8.x | Screens, account lists, workers, and forms |
| Storage | Private JSON under the user's configuration directory | Tokens and selection commit atomically |
| Authentication | Official Claude executable | Interactive token creation and bounded verification |
| Tests | pytest with fictional credentials | Isolated storage, subprocess, shell, and terminal behavior |
| Installation | Private virtual environment and launcher | Source-checkout execution without changing system Python |

The token engine and token UI implement the default workflow. The legacy engine and UI
retain browser-profile compatibility. Diagnostic events use an allowlist that excludes
token values and pasted text.

The default launcher sets OAuth only for the Claude child process. It does not rewrite
live browser credentials. Setup-token creation owns the terminal after the TUI exits;
Cauth accepts a manual hidden paste afterward. Legacy writes remain atomic and must
preserve unrelated Claude state.

There is no Cauth server, telemetry, or direct model HTTP client. Authentication requests
go through the official executable. Real tokens and account state remain outside the
repository. Platform-specific filesystem and terminal behavior needs native verification.
