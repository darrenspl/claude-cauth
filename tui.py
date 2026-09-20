"""Account-centered Textual UI. Browser login owns the terminal only after app exit."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

import cauth

CSS = """
Screen { background: $surface; }
#banner { height: auto; max-height: 6; margin: 0 1; padding: 0 1; border: round $primary; }
#warnings { height: auto; max-height: 2; margin: 0 2; color: $warning; }
#filter { height: 3; margin: 0 1; }
#accounts, #list { height: 1fr; min-height: 3; margin: 0 1; border: round $panel; }
#hint { height: auto; max-height: 2; margin: 0 2; color: $text-muted; }
.screen-title { height: auto; padding: 0 2; text-style: bold; }
.body-text { height: auto; padding: 0 2; }
#body, #help-body { height: 1fr; padding: 0 1; }
.help-head { text-style: bold; color: $accent; margin-top: 1; }
.probe-result { height: auto; padding: 0 1; margin: 0 1; border: round $panel; }
.probe-running { border: round $warning; }
.probe-pass { border: round $success; }
.probe-fail { border: round $error; }
#result-scroll { height: 1fr; min-height: 4; }
#progress { height: auto; padding: 0 2; color: $warning; }
ListView { background: transparent; }
ListItem { height: auto; padding: 0 1; border-bottom: solid $panel; }
ListItem Label { height: auto; width: 1fr; }
.account-title { text-style: bold; }
.account-detail { margin-left: 2; color: $text-muted; }
ListItem.-highlight { background: $boost; }
ListItem.-highlight .account-title { color: $accent; }
Button { height: 3; min-width: 12; border: tall $panel; margin: 0; }
Button:focus { text-style: bold underline; border: tall $accent; }
#add-buttons { height: auto; margin: 1 2; }
#add-buttons Button { width: 100%; }
#modal-frame { width: 62; max-width: 100%; height: auto; max-height: 95%; padding: 0 1; border: thick $primary; background: $surface; }
#modal-buttons { height: auto; }
#modal-buttons Button { width: 1fr; min-width: 8; }
#alias-error { height: auto; color: $error; }
#recovery-actions { height: auto; margin: 1; }
#recovery-actions Button { width: 100%; }
"""


def tier_or_unknown(state: cauth.LiveState) -> str:
    return state.tier or "unknown"


@dataclass(frozen=True)
class LoginRequest:
    return_to: Optional[str]
    alias: Optional[str] = None


def run_claude_login(email: str | None = None) -> int:
    print("\nOpening Claude sign-in. Ctrl+C cancels and restores your previous login.")
    print(
        "Open the printed URL if needed. Paste the complete code at the hidden-input prompt.\n"
    )
    return cauth.run_claude_login(email) if email else cauth.run_claude_login()


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "confirm", "Confirm"),
        Binding("n", "dismiss_false", "Cancel"),
        Binding("escape", "dismiss_false", "Cancel"),
    ]

    def __init__(self, question: str, confirm_label: str = "Confirm") -> None:
        super().__init__()
        self.question, self.confirm_label = question, confirm_label

    def compose(self) -> ComposeResult:
        with Center():
            with VerticalScroll(id="modal-frame"):
                yield Static(self.question, markup=False)
                yield Static("y confirms · n / Esc cancels")
                with Horizontal(id="modal-buttons"):
                    yield Button("Cancel", id="cancel")
                    yield Button(self.confirm_label, variant="primary", id="confirm")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    @on(Button.Pressed, "#confirm")
    def action_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_dismiss_false(self) -> None:
        self.dismiss(False)


class AliasInputModal(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "dismiss_none", "Cancel")]

    def __init__(
        self,
        email: str | None,
        prompt: str | None = None,
        initial: str = "",
        save: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.email, self.prompt, self.initial, self.save = email, prompt, initial, save

    def compose(self) -> ComposeResult:
        with Center():
            with VerticalScroll(id="modal-frame"):
                yield Static(
                    self.prompt
                    or f"Save {self.email or 'this login'} under which alias?",
                    markup=False,
                )
                yield Input(
                    value=self.initial,
                    placeholder="work, personal, client-acme",
                    id="alias",
                )
                yield Static(
                    "1–64 characters. Use a portable filename; spaces are allowed."
                )
                yield Static("", id="alias-error", markup=False)
                with Horizontal(id="modal-buttons"):
                    yield Button("Cancel", id="cancel")
                    yield Button(
                        "Save",
                        variant="primary",
                        id="save",
                        disabled=not self.initial.strip(),
                    )

    def on_mount(self) -> None:
        field = self.query_one("#alias", Input)
        field.focus()
        field.action_end()

    @on(Input.Changed, "#alias")
    def _changed(self) -> None:
        self.query_one("#save", Button).disabled = not self.query_one(
            "#alias", Input
        ).value.strip()

    @on(Input.Submitted, "#alias")
    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        try:
            alias = cauth.validate_alias(self.query_one("#alias", Input).value)
            if self.save:
                self.save(alias)
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#alias-error", Static).update(str(exc))
            self.query_one("#alias", Input).focus()
            return
        self.dismiss(alias)

    @on(Button.Pressed, "#cancel")
    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class AccountList(ListView):
    def add_accounts(
        self, aliases: list[str], active: str | None, selected: str | None = None
    ) -> None:
        data = cauth.load_accounts()["accounts"]
        for alias in aliases:
            info = data[alias]
            title = f"{alias} · {'Installed' if alias == active else 'Saved'}"
            details = f"{info.get('email') or 'unknown'} · {cauth.LiveState(None, info.get('tier'), None).tier}"
            if info.get("organization"):
                details += f" · {info['organization']}"
            labels = [
                Label("  " + title, classes="account-title", markup=False),
                Label(details, classes="account-detail", markup=False),
            ]
            labels.extend(
                Label(note, classes="account-detail", markup=False)
                for note in cauth.saved_account_notes(alias)
            )
            check = info.get("last_check")
            if isinstance(check, dict):
                labels.append(
                    Label(cauth.check_description(check), classes="account-detail", markup=False)
                )
            item = ListItem(*labels)
            item.alias = alias
            item.account_title = title
            self.append(item)
        if aliases:
            self.index = aliases.index(selected) if selected in aliases else 0

    @on(ListView.Highlighted)
    def _mark_selection(self, event: ListView.Highlighted) -> None:
        for item in self.children:
            title = getattr(item, "account_title", None)
            if title is not None:
                for label in item.query(".account-title"):
                    label.update(
                        ("> " if item is self.highlighted_child else "  ") + title
                    )

    def selected_alias(self) -> str | None:
        return getattr(self.highlighted_child, "alias", None)


class HomeScreen(Screen):
    BINDINGS = [
        Binding("s", "switch", "Switch"),
        Binding("a", "add", "Add"),
        Binding("r", "remove", "Delete"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
        Binding("l", "renew", "Renew"),
        Binding("v", "verify", "Check live"),
        Binding("e", "rename", "Rename", show=False),
        Binding("x", "refresh", "Reload", show=False),
        Binding("slash", "filter", "Find", show=False),
        Binding("d", "recovery", "Recovery", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="banner", markup=False)
        yield Static(id="warnings", markup=False)
        yield Input(placeholder="Find an account (/)", id="filter")
        yield AccountList(id="accounts")
        yield Static(id="hint", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_view()
        self.query_one("#accounts", AccountList).focus()

    def on_screen_resume(self) -> None:
        self.refresh_view()

    def action_refresh(self) -> None:
        self.refresh_view()
        self.notify("Reloaded local files. Use v for a new check.")

    def refresh_view(self) -> None:
        try:
            self._render_state()
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#banner", Static).update("Account data needs recovery")
            self.query_one("#warnings", Static).update(str(exc))
            self.query_one("#hint", Static).update("d Recovery · ? Help · q Quit")

    def _render_state(self) -> None:
        data, status = cauth.load_accounts(), cauth.account_status()
        label = status["installed"] or (
            "Unsaved login" if status["unsaved"] else "Not signed in"
        )
        self.query_one("#banner", Static).update(
            f"Installed: {label} · {status['email'] or 'unknown'} · {status['tier']}\n"
            f"Checked: {cauth.check_description(status['last_check'])}"
        )
        notices = cauth.env_conflicts() + cauth.auth_state_warnings(status["installed"])
        if status["mismatch"]:
            notices.insert(
                0,
                "Saved selection differs from live identity. Installed identity is shown above.",
            )
        if cauth.pending_login() or (cauth.CONFIG_DIR / ".transaction.json").exists():
            notices.insert(
                0, "An unfinished operation needs attention. Press d for Recovery."
            )
        notices += cauth.runtime_notices()
        self.query_one("#warnings", Static).update(
            (
                notices[0]
                + (f" (+{len(notices) - 1} notes)" if len(notices) > 1 else "")
                + " · ? details"
            )
            if notices
            else ""
        )
        listing = self.query_one("#accounts", AccountList)
        selected = (
            getattr(self.app, "selected_alias", None)
            or listing.selected_alias()
            or status["installed"]
        )
        listing.clear()
        term = self.query_one("#filter", Input).value.casefold()
        aliases = [
            alias
            for alias, info in sorted(data["accounts"].items())
            if term
            in " ".join(
                [alias, info.get("email") or "", info.get("organization") or ""]
            ).casefold()
        ]
        listing.add_accounts(aliases, status["installed"], selected)
        if not data["accounts"]:
            hint = "a Sign in / save current login to get started"
        elif not aliases:
            hint = "No matches. Clear the search to see saved accounts."
        else:
            hint = (
                getattr(self.app, "last_action", "")
                or "Enter switches selected · l renews selected · v checks installed login"
            )
        self.query_one("#hint", Static).update(hint)

    @on(Input.Changed, "#filter")
    def _filter_changed(self) -> None:
        self.refresh_view()

    @on(Input.Submitted, "#filter")
    def _focus_list(self) -> None:
        self.query_one("#accounts", AccountList).focus()

    @on(ListView.Highlighted, "#accounts")
    def _highlight(self, event: ListView.Highlighted) -> None:
        alias = getattr(event.item, "alias", None)
        if alias:
            self.app.selected_alias = alias

    def selected(self) -> str | None:
        return self.query_one("#accounts", AccountList).selected_alias()

    @on(ListView.Selected, "#accounts")
    def _selected(self, event: ListView.Selected) -> None:
        alias = getattr(event.item, "alias", None)
        if alias:
            self.app.push_screen(SwitchScreen(alias, start=True))

    def action_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_switch(self) -> None:
        self.app.push_screen(SwitchScreen(self.selected()))

    def action_add(self) -> None:
        self.app.push_screen(AddScreen())

    def action_renew(self) -> None:
        alias = self.selected()
        if alias:
            self.app.push_screen(AddScreen(renew_alias=alias))
        else:
            self.action_add()

    def action_rename(self) -> None:
        self.app.push_screen(RenameScreen(self.selected()))

    def action_remove(self) -> None:
        self.app.push_screen(RemoveScreen(self.selected()))

    def action_verify(self) -> None:
        self.app.push_screen(VerifyScreen())

    def action_recovery(self) -> None:
        self.app.push_screen(RecoveryScreen())

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self.app.exit()


class ProbeScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back / cancel"),
        Binding("q", "back", "Back / cancel"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._probe_running = False
        self.cancel_event = threading.Event()
        self._started = 0.0

    def _start(self) -> None:
        self._probe_running = True
        self.cancel_event.clear()
        self._started = time.monotonic()
        self.set_interval(0.25, self._tick)

    def _tick(self) -> None:
        if self._probe_running:
            elapsed = int(time.monotonic() - self._started)
            self.query_one("#progress", Static).update(
                f"{'Cancelling and restoring…' if self.cancel_event.is_set() else 'Checking with Claude…'} {elapsed}s / 90s · Esc cancels"
            )

    def _finish(self, summary: str) -> None:
        self._probe_running = False
        self.query_one("#progress", Static).update(
            "Esc returns · result retained on Home"
        )
        self.app.last_action = summary
        if getattr(self.app, "_quit_after_probe", False):
            self.app.exit()

    def action_back(self) -> None:
        if self._probe_running:
            self.cancel_event.set()
            self._tick()
        else:
            self.app.pop_screen()


class SwitchScreen(ProbeScreen):
    def __init__(self, alias: str | None = None, start: bool = False) -> None:
        super().__init__()
        self.initial, self.autostart = alias, start
        self.discard_unsaved = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Switch account", classes="screen-title")
        yield Static(
            "Select and Enter. Saves the outgoing login, installs your choice, then checks it. A failed check restores the previous account when available.",
            classes="body-text",
        )
        yield AccountList(id="list")
        yield Static("", id="progress", markup=False)
        with VerticalScroll(id="result-scroll"):
            yield Static(
                "Select an account to switch and test it.",
                id="probe-status",
                classes="probe-result",
                markup=False,
            )
        yield Footer()

    def on_mount(self) -> None:
        try:
            data = cauth.load_accounts()
            listing = self.query_one("#list", AccountList)
            listing.add_accounts(
                sorted(data["accounts"]),
                cauth.account_status()["installed"],
                self.initial,
            )
            listing.focus()
            if not data["accounts"]:
                self.query_one("#probe-status", Static).update(
                    "No saved accounts. Go Back, then press a to sign in or save your current login."
                )
            if self.autostart and self.initial:
                self.call_after_refresh(self.switch, self.initial)
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#probe-status", Static).update(str(exc))

    @on(ListView.Selected)
    def _selected(self, event: ListView.Selected) -> None:
        alias = getattr(event.item, "alias", None)
        if alias:
            self.switch(alias)

    def switch(self, alias: str) -> None:
        if self._probe_running:
            return
        try:
            cauth.require_file_auth()
            if cauth.account_status()["unsaved"] and not self.discard_unsaved:
                self.app.push_screen(
                    ConfirmModal(
                        "The current login is not saved. Cancel and use Add → Store current to protect it.\n\nDiscard this unsaved login and switch?",
                        "Discard",
                    ),
                    lambda ok: self._discard_and_switch(alias) if ok else None,
                )
                return
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#probe-status", Static).update(str(exc))
            return
        self.app.selected_alias = alias
        self._start()
        self.query_one("#list", AccountList).disabled = True
        self.query_one("#probe-status", Static).update(
            f"TESTING: {alias}. Installing and checking; up to 90 seconds."
        )
        self._run_switch_probe(alias)

    def _discard_and_switch(self, alias: str) -> None:
        self.discard_unsaved = True
        self.switch(alias)

    @work(thread=True, exclusive=True)
    def _run_switch_probe(self, alias: str) -> None:
        cauth._local_operation.cancel_event = self.cancel_event
        try:
            outcome = (
                cauth.switch_with_probe(alias, discard_unsaved=True)
                if self.discard_unsaved
                else cauth.switch_with_probe(alias)
            )
            self.app.call_from_thread(self._show_probe_outcome, outcome)
        except (cauth.CauthError, OSError) as exc:
            self.app.call_from_thread(self._show_probe_exception, alias, str(exc))
        finally:
            cauth._local_operation.cancel_event = None

    def _show_probe_exception(self, alias: str, message: str) -> None:
        self.query_one("#probe-status", Static).update(
            f"Could not switch {alias}.\n{message}\nOpen Recovery if the operation could not be restored."
        )
        self._finish_probe_ui(message)

    def _show_probe_outcome(self, outcome: cauth.SwitchProbeOutcome) -> None:
        lines = [
            f"{outcome.probe.summary} Target: {outcome.target_alias}",
            outcome.probe.output,
        ]
        if outcome.restored_alias:
            lines.append(
                f"Cauth restored '{outcome.restored_alias}'. The target saved profile was not overwritten."
            )
        elif outcome.rollback_error:
            lines.append(f"Recovery required: {outcome.rollback_error}")
        else:
            lines.append(
                f"Installed selection remains '{outcome.target_alias}'; authentication is recorded separately."
            )
        if outcome.probe.kind == "sign_in_required":
            lines.append(f"Go Back and press l to renew {outcome.target_alias}.")
        status = self.query_one("#probe-status", Static)
        status.set_classes(
            "probe-result " + ("probe-pass" if outcome.ok else "probe-fail")
        )
        status.update("\n".join(lines))
        self._finish_probe_ui(f"{outcome.target_alias}: {outcome.probe.summary}")

    def _finish_probe_ui(self, summary: str = "") -> None:
        self.query_one("#list", AccountList).disabled = False
        self._finish(summary)


class VerifyScreen(ProbeScreen):
    BINDINGS = ProbeScreen.BINDINGS + [Binding("v", "verify", "Run again")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Check installed login", classes="screen-title")
        yield Static(
            "Checks the installed OAuth login. Claude may refresh its token; Cauth saves the check result. Shell overrides are reported separately.",
            classes="body-text",
        )
        yield Static("", id="progress", markup=False)
        with VerticalScroll(id="result-scroll"):
            yield Static(
                "TESTING: up to 90 seconds.",
                id="verify-status",
                classes="probe-result",
                markup=False,
            )
        yield Footer()

    def on_mount(self) -> None:
        self.call_after_refresh(self.action_verify)

    def action_verify(self) -> None:
        if not self._probe_running:
            self._start()
            self._run_validation()

    @work(thread=True, exclusive=True)
    def _run_validation(self) -> None:
        cauth._local_operation.cancel_event = self.cancel_event
        try:
            report = cauth.validate_active()
            self.app.call_from_thread(self._show_report, report)
        except (cauth.CauthError, OSError) as exc:
            self.app.call_from_thread(self._show_failure, str(exc))
        finally:
            cauth._local_operation.cancel_event = None

    def _show_failure(self, message: str) -> None:
        self.query_one("#verify-status", Static).update("Could not check.\n" + message)
        self._finish(message)

    def _show_report(self, report: cauth.ValidationReport) -> None:
        lines = [
            report.probe.summary,
            f"Installed: {report.alias or 'unsaved / not signed in'}",
            f"{report.email or 'unknown'} · {report.tier}",
            *report.deadlines,
            *("NOTE: " + message for message in report.warnings),
            report.probe.output,
        ]
        if report.probe.kind == "sign_in_required":
            lines.append(
                "Go Back and press l to renew a saved account, or a to sign in."
            )
        self.query_one("#verify-status", Static).update("\n".join(lines))
        self._finish(report.probe.summary)


class AddScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back"), Binding("q", "back", "Back")]

    def __init__(
        self, renew_alias: str | None = None, return_to: str | None = None
    ) -> None:
        super().__init__()
        self.renew_alias, self.return_to = renew_alias, return_to

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Renew account" if self.renew_alias else "Add account",
            classes="screen-title",
        )
        with VerticalScroll(id="body"):
            yield Static(
                f"Renew {self.renew_alias}. Confirm the browser signs in as the expected account."
                if self.renew_alias
                else "Store current saves your existing login. Sign in opens Claude’s browser flow.\n\nAfter saving a new login, Cauth returns to your previous saved account. Cancelled or failed login restores the previous login.",
                classes="body-text",
                markup=False,
            )
            with Vertical(id="add-buttons"):
                yield Button(
                    "Store current",
                    id="store",
                    variant="primary",
                    disabled=bool(self.renew_alias),
                )
                yield Button(
                    "Renew in browser" if self.renew_alias else "Sign in / add another",
                    id="login",
                    variant="success",
                )
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#login" if self.renew_alias else "#store", Button).focus()

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        if cauth.pending_login():
            self._cancel_pending()
        else:
            self.app.pop_screen()

    @on(Button.Pressed, "#store")
    def _store(self) -> None:
        try:
            if not cauth.live_credentials_have_complete_oauth_tokens():
                raise cauth.CauthError("No usable login to save. Choose Sign in first.")
            self._ask_alias(self.return_to)
        except (cauth.CauthError, OSError) as exc:
            self.notify(str(exc), severity="error", timeout=12)

    def _update_existing(self, alias: str) -> None:
        try:
            landed = cauth.add_then_return(alias, self.return_to)
            cauth.complete_login()
            self.app.last_action = f"Updated {alias} with the current login. Installed: {landed}."
            self.app.pop_screen()
        except (cauth.CauthError, OSError) as exc:
            self.notify(str(exc), severity="error")

    def _ask_alias(self, return_to: str | None = None) -> None:
        self.return_to = return_to
        try:
            state = cauth.account_status()
            email = state["email"]
            existing = state["installed"]
        except (cauth.CauthError, OSError) as exc:
            self.notify(str(exc), severity="error")
            return
        if existing:
            self._update_existing(existing)
            return
        landed = None

        def persist(alias: str) -> None:
            nonlocal landed
            if alias in cauth.load_accounts()["accounts"] and alias != existing:
                raise cauth.CauthError(
                    "That alias belongs to another saved account. Choose another name."
                )
            landed = cauth.add_then_return(alias, self.return_to)
            cauth.complete_login()

        def saved(alias: str | None) -> None:
            if alias:
                self.app.last_action = f"Saved {alias}. Installed: {landed}."
                self.app.pop_screen()
            elif cauth.pending_login():
                self._cancel_pending()

        self.app.push_screen(
            AliasInputModal(email, initial=existing or "", save=persist), saved
        )

    def _cancel_pending(self) -> None:
        def restore(ok: bool) -> None:
            if not ok:
                self._ask_alias(self.return_to)
                return
            try:
                cauth.restore_login()
                self.app.last_action = "Cancelled addition. Previous login restored."
                self.app.pop_screen()
            except (cauth.CauthError, OSError) as exc:
                self.notify(str(exc), severity="error")

        self.app.push_screen(
            ConfirmModal(
                "Discard the newly signed-in, unsaved login and restore your previous login? Cancel keeps the naming form open.",
                "Restore previous",
            ),
            restore,
        )

    @on(Button.Pressed, "#login")
    def _login(self) -> None:
        try:
            state = cauth.account_status()
            if state["unsaved"] and not self.renew_alias:
                self.notify(
                    "Save the current login first, then sign in as another account.",
                    severity="warning",
                    timeout=12,
                )
                self._store()
                return
            self.app.push_screen(
                ConfirmModal(
                    f"Hand the terminal to Claude sign-in?\n\nExpected account: {self.renew_alias or 'your new account'}.\nPrevious login is restored on failure or cancellation. After saving, return to {state['installed'] or 'the new account'}.\nOther Claude sessions may share these files.",
                    "Sign in",
                ),
                lambda ok: self.request_login() if ok else None,
            )
        except (cauth.CauthError, OSError) as exc:
            self.notify(str(exc), severity="error")

    def request_login(self) -> None:
        try:
            pending = cauth.begin_login(self.renew_alias)
            self.app.exit(LoginRequest(pending["return_to"], self.renew_alias))
        except (cauth.CauthError, OSError) as exc:
            self.notify(str(exc), severity="error", timeout=12)


class RenameScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back"), Binding("q", "back", "Back")]
    title_text = "Rename account"

    def __init__(self, alias: str | None = None) -> None:
        super().__init__()
        self.initial = alias

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.title_text, classes="screen-title")
        yield Static(
            "Select a saved account and press Enter. Esc returns.", classes="body-text"
        )
        yield AccountList(id="list")
        yield Static("", id="hint", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        try:
            data = cauth.load_accounts()
            listing = self.query_one("#list", AccountList)
            listing.add_accounts(
                sorted(data["accounts"]),
                cauth.account_status()["installed"],
                self.initial,
            )
            listing.focus()
            if not data["accounts"]:
                self.query_one("#hint", Static).update(
                    "No saved accounts. Go Back and press a to add one."
                )
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#hint", Static).update(str(exc))

    @on(ListView.Selected)
    def _selected(self, event: ListView.Selected) -> None:
        alias = getattr(event.item, "alias", None)
        if alias:
            self.choose_alias(alias)

    def choose_alias(self, alias: str) -> None:
        def save(new: str) -> None:
            cauth.rename_account(alias, new)
            self.app.selected_alias = new

        def done(new: str | None) -> None:
            if new:
                self.app.last_action = f"Renamed {alias} to {new}."
                self.app.pop_screen()

        self.app.push_screen(
            AliasInputModal(
                None, f"Rename {alias}. Live login stays unchanged.", alias, save
            ),
            done,
        )

    def action_back(self) -> None:
        self.app.pop_screen()


class RemoveScreen(RenameScreen):
    title_text = "Delete saved credentials"

    def choose_alias(self, alias: str) -> None:
        info = cauth.load_accounts()["accounts"][alias]

        def remove(ok: bool) -> None:
            if not ok:
                return
            try:
                cauth.remove_account(alias)
                self.app.last_action = (
                    f"Deleted {alias} and its rotation backups. Live login unchanged."
                )
                self.app.pop_screen()
            except (cauth.CauthError, OSError) as exc:
                self.query_one("#hint", Static).update(str(exc))

        self.app.push_screen(
            ConfirmModal(
                f"Delete saved credentials for {alias} ({info.get('email') or 'unknown'})?\n\nDeletes its saved profile and rotation backups. Does not log Claude out, including when this is the last saved account. You can save a still-live login again; otherwise browser sign-in is needed.",
                "Delete",
            ),
            remove,
        )


class RecoveryScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back"), Binding("q", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Recovery", classes="screen-title")
        with VerticalScroll(id="body"):
            yield Static(
                "Restore rolls back an interrupted operation or browser login. Rebuild rediscovers complete saved profiles and preserves the old index. Neither action deletes broken profiles. Close other Claude/Cauth sessions first.",
                classes="body-text",
            )
            with Vertical(id="recovery-actions"):
                yield Button("Restore pending operation", id="restore")
                yield Button("Resume naming a new login", id="resume-login")
                yield Button("Rebuild account index", id="rebuild")
                yield Button("Retry reading files", id="retry")
                yield Button("Clear unfinished login", id="clear-login")
                yield Button("Reset all logins", id="reset-logins", variant="error")
            yield Static("", id="recovery-result", markup=False)
        yield Footer()

    @on(Button.Pressed, "#clear-login")
    def _clear_login(self) -> None:
        self.app.push_screen(
            ConfirmModal(
                "Discard the unfinished-login marker? Keeps current credentials and saved accounts. Does not restore an old login. Close other Claude/Cauth sessions first.",
                "Clear unfinished login",
            ),
            lambda ok: self._run_clear_login() if ok else None,
        )

    def _run_clear_login(self) -> None:
        try:
            cauth.clear_unfinished_login()
            self.query_one("#recovery-result", Static).update(
                "Unfinished login cleared. Current credentials kept. Go Back to save or renew."
            )
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#recovery-result", Static).update(str(exc))

    @on(Button.Pressed, "#reset-logins")
    def _reset_logins(self) -> None:
        self.app.push_screen(
            ConfirmModal(
                "Delete ALL saved accounts, credential backups, pending recovery state and the live file login? No recovery copy is kept. Claude settings and conversations stay. Environment tokens and OS keychains are not cleared. Close other Claude/Cauth sessions first.",
                "Reset all logins",
            ),
            lambda ok: self._run_reset_logins() if ok else None,
        )

    def _run_reset_logins(self) -> None:
        try:
            cauth.reset_logins()
            self.app.selected_alias = None
            self.app.last_action = "All file logins cleared. Sign in to start fresh."
            self.query_one("#recovery-result", Static).update(
                "All saved and live file logins cleared. No recovery copy retained. Go Back and choose Add to sign in."
            )
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#recovery-result", Static).update(
                f"Reset could not complete: {exc}. Fix the file error and retry Reset."
            )

    @on(Button.Pressed, "#resume-login")
    def _resume_login(self) -> None:
        self.app.push_screen(
            ConfirmModal(
                "Resume naming the pending new login? Close the original Cauth session first.",
                "Resume",
            ),
            lambda ok: self._run_resume() if ok else None,
        )

    def _run_resume(self) -> None:
        try:
            with cauth.operation_lock():
                pending = cauth.pending_login()
                if not pending or pending.get("phase") != "naming":
                    raise cauth.CauthError(
                        "No completed browser login awaits naming. Restore or retry the interrupted login instead."
                    )
                pending["owner"] = cauth.os.getpid()
                cauth.atomic_write_json(
                    cauth.CONFIG_DIR / ".pending-login.json", pending
                )
            screen = AddScreen(return_to=pending["return_to"])
            self.app.pop_screen()
            self.app.push_screen(screen)
            self.call_after_refresh(screen._ask_alias, pending["return_to"])
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#recovery-result", Static).update(str(exc))

    @on(Button.Pressed, "#restore")
    def _restore(self) -> None:
        self.app.push_screen(
            ConfirmModal(
                "Restore the previous files from the pending snapshot? This replaces changes made to those files since the operation started.",
                "Restore",
            ),
            lambda ok: self._run_restore() if ok else None,
        )

    def _run_restore(self) -> None:
        try:
            cauth.recover_transaction()
            cauth.restore_login()
            self.query_one("#recovery-result", Static).update(
                "Previous state restored. Go Back or Retry."
            )
        except (cauth.CauthError, OSError, ValueError, KeyError) as exc:
            self.query_one("#recovery-result", Static).update(
                f"Restore could not complete: {exc}. Snapshot retained."
            )

    @on(Button.Pressed, "#rebuild")
    def _rebuild(self) -> None:
        self.app.push_screen(
            ConfirmModal(
                "Rebuild the index from complete saved pairs? Existing index is backed up. Last-check history and aliases without complete pairs will not appear in the rebuilt index.",
                "Rebuild",
            ),
            lambda ok: self._run_rebuild() if ok else None,
        )

    def _run_rebuild(self) -> None:
        try:
            skipped = cauth.rebuild_index()
            self.query_one("#recovery-result", Static).update(
                "Index rebuilt. Old index retained. Incomplete profiles retained: "
                + (", ".join(skipped) or "none")
            )
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#recovery-result", Static).update(str(exc))

    @on(Button.Pressed, "#retry")
    def _retry(self) -> None:
        try:
            state = cauth.account_status()
            self.query_one("#recovery-result", Static).update(
                f"Readable. Installed: {state['installed'] or 'no saved identity'}. Go Back to continue."
            )
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#recovery-result", Static).update(str(exc))

    def action_back(self) -> None:
        self.app.pop_screen()


class HelpScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("q", "back", "Back"),
        Binding("question_mark", "back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="help-body"):
            yield Static("Current notes", classes="help-head")
            try:
                notes = (
                    cauth.env_conflicts()
                    + cauth.auth_state_warnings(cauth.account_status()["installed"])
                    + cauth.runtime_notices()
                )
                yield Static("\n\n".join(notes) or "No local warnings.", markup=False)
            except (cauth.CauthError, OSError) as exc:
                yield Static(str(exc), markup=False)
            sections = [
                (
                    "Account states",
                    "Installed describes the live identity. Saved means a profile exists. Verified includes the time of the last successful request; it is not a guarantee of future access. Could not check means authentication is unknown.",
                ),
                (
                    "Keyboard",
                    "Arrow keys select. Enter switches selected. / finds accounts; Enter returns focus to the list. s opens Switch, a adds/saves, l renews selected, e renames, r deletes saved credentials, v checks the installed login, x reloads local files, d opens Recovery, q quits Home. Esc returns or cancels a check.",
                ),
                (
                    "Adding and renewing",
                    "Save an unsaved login before adding another. Browser login temporarily owns this terminal. Codes are hidden. Failed/cancelled login restores the previous login. Naming errors stay editable. After saving, you return to the prior saved account when available. Renew checks the browser identity before updating the selected profile.",
                ),
                (
                    "Checks and cancellation",
                    "Checks use the official Claude client and one Haiku request, up to 90 seconds. Claude may refresh credentials. Esc cancels the child request and restores the previous selection when available. Results are saved with a timestamp. Rate limits, client failures and timeouts do not imply an expired login.",
                ),
                (
                    "Environment overrides",
                    "Verify tests the file-backed OAuth login with API-key and setup-token overrides removed, and reports those overrides. Switch refuses known overrides. Exit Cauth, remove the variable in your shell, then relaunch; changing a shell profile alone does not change a running terminal. POSIX shells use unset; PowerShell uses Remove-Item Env:NAME.",
                ),
                (
                    "Shared files",
                    "Use a plain terminal. Other Claude sessions may read or refresh the same files; Cauth cannot lock Claude itself. Cauth operations are serialized, and interrupted operations retain a protected recovery snapshot. Close other sessions before restoring a snapshot.",
                ),
                (
                    "Forget versus log out",
                    "Forget removes the saved pair and rotation backups, even for the last account. It leaves Claude logged in. Recovery/index backups are diagnostic artifacts, not an account archive. Claude auth logout is a separate operation.",
                ),
                (
                    "Recovery and installation",
                    "Use d if files are damaged or login was interrupted. Retry, restore a pending snapshot, or rebuild the index without deleting profile files. cauth doctor checks the interpreter, TUI import, executable and application path without reading credentials. Rerun the checkout installer to repair installation.",
                ),
                ("Shell commands", cauth.USAGE),
                (
                    "Locations",
                    f"Profiles: {cauth.PROFILES_DIR}\nLive credentials: {cauth.LIVE_CREDENTIALS}\nIdentity: {cauth.LIVE_CLAUDE_JSON}\nmacOS Keychain is not supported; macOS/Windows require native validation.",
                ),
            ]
            for title, body in sections:
                yield Static(title, classes="help-head")
                yield Static(body, markup=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()


class SafeApp(App):
    """Keep framework tracebacks/locals out of the terminal and stop failed handoffs."""

    closing = False

    def exit(self, *args, **kwargs):
        self.closing = True
        return super().exit(*args, **kwargs)

    def _handle_exception(self, error: Exception) -> None:
        # Textual's default fatal handler prints rich tracebacks with local variables.
        # Retain its test exception signal, but log only safe stack locations instead.
        self.closing = True
        self._exit = True
        self._return_code = 1
        self._return_value = None
        if self._exception is None:
            self._exception = error
            self._exception_event.set()
            cauth.diagnostics.failure(error)
            self._exit_renderables.append(
                "Cauth stopped because of an internal error. Details were recorded in the diagnostic log. Run cauth logs, then restart Cauth. No new login was started."
            )
        cancel = getattr(self, "cancel_probe", None)
        if cancel is not None:
            cancel.set()
        self._close_messages_no_wait()


class CauthApp(SafeApp):
    ENABLE_COMMAND_PALETTE = False
    CSS = CSS
    TITLE = "Claude Cauth"
    SUB_TITLE = ""
    BINDINGS = [
        Binding("ctrl+q,ctrl+c", "safe_quit", "Quit", priority=True, show=False)
    ]

    def __init__(self, pending_login: Optional[LoginRequest] = None) -> None:
        super().__init__()
        self.pending_login = pending_login
        self.selected_alias = None
        self.last_action = ""
        self._quit_after_probe = False

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())
        if self.pending_login:
            self.call_after_refresh(self._continue_completed_login)

    def _continue_completed_login(self) -> None:
        request = self.pending_login
        if request is None:
            return
        self.pending_login = None
        screen = AddScreen(return_to=request.return_to)
        self.push_screen(screen)
        self.call_after_refresh(screen._ask_alias, request.return_to)

    def action_safe_quit(self) -> None:
        if isinstance(self.screen, ProbeScreen) and self.screen._probe_running:
            self._quit_after_probe = True
            self.screen.action_back()
        elif cauth.pending_login():
            self.push_screen(RecoveryScreen())
        else:
            self.exit()


def run_tui() -> int:
    pending: Optional[LoginRequest] = None
    while True:
        app = CauthApp(pending)
        result = app.run()
        if getattr(app, "return_code", 0):
            return 1
        if not isinstance(result, LoginRequest):
            return 0
        try:
            with cauth.operation_lock():
                email = (
                    cauth.load_accounts()["accounts"].get(result.alias, {}).get("email")
                    if result.alias
                    else None
                )
                code = run_claude_login(email) if email else run_claude_login()
                if code != 0:
                    cauth.restore_login()
                    print(
                        f"Claude login exited with status {code}. Previous login restored."
                    )
                    pending = None
                else:
                    cauth.mark_live_unregistered()
                    if result.alias:
                        check = cauth.finish_renewal(result.alias, result.return_to)
                        print(check.summary)
                        if not check.ok:
                            print(check.output)
                        cauth.complete_login()
                        pending = None
                    else:
                        saved = cauth.pending_login()
                        if saved:
                            saved["phase"] = "naming"
                            cauth.atomic_write_json(
                                cauth.CONFIG_DIR / ".pending-login.json", saved
                            )
                        pending = result
                        continue
        except (cauth.CauthError, OSError) as exc:
            try:
                cauth.restore_login()
                print(f"Login could not be saved: {exc}. Previous login restored.")
            except (cauth.CauthError, OSError) as restore_error:
                print(f"Login recovery required: {restore_error}. Run cauth recover.")
                return 1
            pending = None
        try:
            input("Press Enter to return to Cauth. ")
        except (EOFError, KeyboardInterrupt):
            pass
