# ADR 0001: Install through a private virtual environment

- Status: Accepted
- Date: 2026-08-05

## Context

A script symlink uses whichever Python is on PATH and does not ensure Textual is installed.
Installing dependencies into a system interpreter can conflict with externally managed
Python environments. Cauth should run from a source checkout without modifying system
packages or placing a virtual environment in the repository.

## Decision

The installer creates a private virtual environment under the user's local data directory
and a launcher under the user's local binary directory. The launcher invokes that
interpreter against the checkout. `CAUTH_VENV` and `CAUTH_BIN` can override those locations.

## Consequences

Source updates take effect after restarting Cauth, without a build or reinstall. Moving
or deleting the checkout breaks its launcher until installation is repeated from the new
location. Dependencies remain separate from both system Python and repository contents.
