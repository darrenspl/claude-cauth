# Legacy browser-profile management

The default Cauth workflow uses long-lived setup-tokens. This compatibility mode manages
saved browser-login credential files and identity metadata. Open it with `cauth legacy`;
legacy CLI commands use `cauth legacy <command>`. Use `cauth legacy --help` for commands.

Legacy selection and the identity installed in Claude can differ. A file copy does not
prove authentication. The terminal UI performs a bounded official Claude request before
reporting verification success; CLI switching remains an explicitly unverified local
file operation. Expired or revoked credentials may require browser renewal.

Before switching, Cauth saves the outgoing account's refreshed credentials back to its
stored profile. Writes use atomic replacement and preserve unrelated Claude settings.
Operations retain private recovery material when interrupted. Restore can replace later
external changes, so review the recovery prompt and close other sessions first.

Browser login requires the terminal UI to exit before the official client starts. Failed
or cancelled renewal preserves prior state when recovery succeeds. A saved profile that
fails verification remains available for renewal. Forget removes saved copies and rotation
backups without signing out the currently installed account.

## Limits

- Legacy file changes can affect other Claude processes using the same credential files.
  Cauth cannot lock out writes by the official client; shared-file races remain possible.
- Environment authentication overrides may bypass browser files. Check the UI warnings
  instead of assuming the installed identity is the one a request uses.
- Recovery snapshots contain credentials. Keep them private and outside the repository.
- macOS Keychain storage and native Windows/macOS behavior need platform-specific checks;
  Linux fixture tests do not establish compatibility with every credential backend.
- Long-lived tokens cannot be derived from legacy refresh tokens. Use the default UI's
  renewal/migration action to run the official setup-token authorization for an alias.
