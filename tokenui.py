"""Default terminal UI for long-lived OAuth tokens, with legacy-profile migration."""

from __future__ import annotations

from dataclasses import dataclass
import threading

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

import cauth
import tokenauth
from tui import CSS, AliasInputModal, ConfirmModal, RecoveryScreen, SafeApp


@dataclass(frozen=True)
class Request:
    action: str
    alias: str | None = None


class TokenHome(Screen):
    _checking = False
    _refresh_scheduled = False
    BINDINGS = [
        Binding("a", "add", "Add"), Binding("i", "import_token", "Import", show=False),
        Binding("l", "renew", "Renew"), Binding("c", "launch", "Launch"),
        Binding("v", "verify", "Verify"), Binding("r", "remove", "Forget", show=False),
        Binding("e", "rename", "Rename", show=False),
        Binding("d", "recovery", "Cleanup", show=False), Binding("b", "legacy", "Old profiles", show=False),
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "help", "Help"),
        Binding("g", "logs", "Logs", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="token-status", markup=False)
        yield ListView(id="tokens")
        yield Static("Enter Select · c Launch · ? Help", id="token-hint", markup=False)
        with VerticalScroll(id="token-feedback"):
            yield Static("", id="token-result", markup=False)
        with Horizontal(id="token-actions"):
            yield Button("Create", id="create")
            yield Button("Import", id="import")
            yield Button("Launch", id="launch", variant="primary")
            yield Button("Cleanup", id="cleanup")
        yield Footer()

    def on_mount(self):
        self.refresh_accounts()
        listing = self.query_one("#tokens", ListView)
        listing.border_title = "Accounts"
        listing.focus()

    def on_resize(self, event):
        labels = ("New", "Paste", "Run", "Clean") if event.size.width < 50 else (
            "Create", "Import", "Launch", "Cleanup"
        )
        for name, label in zip(("create", "import", "launch", "cleanup"), labels):
            for button in self.query("#" + name):
                button.label = label

    def on_screen_resume(self):
        self.refresh_accounts()

    def refresh_accounts(self):
        if not self.is_attached or self.app.closing or self._refresh_scheduled:
            return
        self._refresh_scheduled = True
        self.call_after_refresh(self._refresh_accounts)

    async def _refresh_accounts(self):
        try:
            if not self.is_attached or self.app.closing:
                return
            listing = next(iter(self.query("#tokens")), None)
            if listing is None or not listing.is_attached:
                return
            data = tokenauth.public_status()
            try:
                legacy = cauth.load_accounts()["accounts"]
            except (cauth.CauthError, OSError):
                legacy = {}
            highlighted = self.alias()
            await listing.clear()
            if not listing.is_attached or self.app.closing:
                return
            aliases = sorted(set(data["accounts"]) | set(legacy))
            items = []
            for alias in aliases:
                info = data["accounts"].get(alias)
                title = alias + (" · Selected" if alias == data["active"] else "")
                detail = (
                    "OAuth token · " + cauth.check_description(info["last_check"])
                    if info is not None else "Browser login · l Create token"
                )
                item = ListItem(Label("  " + title, classes="account-title", markup=False),
                                Label(detail, classes="account-detail", markup=False))
                item.alias = alias
                item.account_title = title
                items.append(item)
            if items:
                await listing.extend(items)
            if not listing.is_attached or self.app.closing:
                return
            target = highlighted if highlighted in aliases else data["active"]
            if aliases:
                listing.index = aliases.index(target) if target in aliases else 0
            active_info = data["accounts"].get(data["active"], {})
            count = len(data["accounts"])
            self.query_one("#token-status", Static).update(
                f"Selected: {data['active'] or 'none'}\n"
                f"{count} saved {'account' if count == 1 else 'accounts'} · "
                + cauth.check_description(active_info.get("last_check"))
            )
            self.query_one("#launch", Button).disabled = not bool(data["active"])
            self.query_one("#token-hint", Static).update(
                "Enter Select · c Launch · ? Help" if aliases else "Create or import your first account."
            )
        except (cauth.CauthError, OSError) as exc:
            self.query_one("#token-status", Static).update(f"{exc}\nPress d to reset.")
        finally:
            self._refresh_scheduled = False

    def alias(self):
        item = self.query_one("#tokens", ListView).highlighted_child
        return getattr(item, "alias", None)

    @on(ListView.Highlighted, "#tokens")
    def highlight(self, event):
        for item in event.list_view.children:
            title = getattr(item, "account_title", None)
            if title is not None:
                item.query_one(".account-title", Label).update(
                    ("> " if item is event.item else "  ") + title
                )

    def message(self, text):
        self.query_one("#token-result", Static).update(text)

    @on(ListView.Selected, "#tokens")
    def select(self, event):
        try:
            notice = tokenauth.select(event.item.alias)
            self.refresh_accounts()
            self.message(f"Selected {event.item.alias}. Press v to verify. {notice}")
        except (cauth.CauthError, OSError) as exc:
            self.message(str(exc))

    def request(self, generate=True, alias=None):
        def done(name):
            if name:
                self.app.exit(Request("create" if generate else "import", name))
        if alias:
            done(alias)
        else:
            self.app.push_screen(AliasInputModal(
                None, prompt="Name this long-lived token. Reusing a name replaces that token after saving."
            ), done)

    @on(Button.Pressed, "#create")
    def action_add(self):
        self.request()

    @on(Button.Pressed, "#import")
    def action_import_token(self):
        self.request(generate=False)

    def action_renew(self):
        self.request(alias=self.alias())

    @on(Button.Pressed, "#launch")
    def action_launch(self):
        try:
            tokenauth.selected()
            self.app.exit(Request("launch"))
        except (cauth.CauthError, OSError) as exc:
            self.message(str(exc))

    def action_remove(self):
        alias = self.alias()
        if not alias:
            return
        def done(ok):
            if ok:
                try:
                    tokenauth.remove(alias)
                    self.refresh_accounts()
                    self.message(f"Removed token for {alias}. Existing Claude sessions are unchanged.")
                except (cauth.CauthError, OSError) as exc:
                    self.message(str(exc))
        self.app.push_screen(ConfirmModal(f"Delete the stored long-lived token for {alias}? No backup is retained.", "Forget token"), done)

    def action_rename(self):
        alias = self.alias()
        if alias:
            self.app.push_screen(AliasInputModal(
                None, prompt=f"Rename token {alias}", initial=alias,
                save=lambda name: tokenauth.rename(alias, name)
            ), lambda name: self.refresh_accounts())

    def action_verify(self):
        if not self._checking:
            self._checking = True
            self._verify()

    @work(thread=True, exclusive=True)
    def _verify(self):
        cauth._local_operation.cancel_event = self.app.cancel_probe
        try:
            self.app.call_from_thread(self.message, "Checking the selected token with Claude...")
            result = tokenauth.verify()
            self.app.call_from_thread(self.refresh_accounts)
            self.app.call_from_thread(self.message, result.summary + ("\n" + result.output if not result.ok else ""))
        except (cauth.CauthError, OSError) as exc:
            self.app.call_from_thread(self.message, str(exc))
        finally:
            cauth._local_operation.cancel_event = None
            self._checking = False

    @on(Button.Pressed, "#cleanup")
    def action_recovery(self):
        self.app.push_screen(RecoveryScreen())

    def action_legacy(self):
        self.app.exit(Request("legacy"))

    def action_help(self):
        self.app.push_screen(TokenTextScreen("Help",
            "Accounts\n"
            "Enter selects an account; c launches Claude with that selection.\n"
            "a creates a long-lived token; i imports one; l replaces the highlighted token.\n"
            "v checks the selected token; e renames; r forgets; d opens cleanup; g shows logs.\n\n"
            "Use plain claude with the selected token:\n"
            "Bash: cauth install-shell bash, then open a new terminal\n"
            "Zsh: cauth install-shell zsh, then open a new terminal\n"
            "PowerShell: cauth shell-init powershell | Out-String | Invoke-Expression\n"
            "Fish: cauth shell-init fish | source\n"
            "cauth run works immediately without shell setup.\n"
            "b opens old browser profiles. Tokens do not reveal account email or plan.\n\n"
            "New setup-tokens have a documented one-year lifetime. Imported token age is unknown.\n"
            "Already-running Claude sessions retain their original token."
        ))

    def action_logs(self):
        try:
            self.app.push_screen(TokenTextScreen("Diagnostics", tokenauth.diagnostics.read_recent(20) or "No diagnostic events recorded yet."))
        except OSError as exc:
            self.message(f"Could not read diagnostic logs: {type(exc).__name__}")

    def action_quit(self):
        self.app.cancel_probe.set()
        self.app.exit()


class TokenTextScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back"), Binding("q", "back", "Back")]

    def __init__(self, heading, text):
        super().__init__()
        self.heading, self.text = heading, text

    def compose(self):
        yield Header()
        yield Static(self.heading, classes="screen-title")
        with VerticalScroll(id="body"):
            yield Static(self.text, classes="body-text", markup=False)
        yield Footer()

    def action_back(self):
        self.app.pop_screen()


class TokenApp(SafeApp):
    ENABLE_COMMAND_PALETTE = False
    CSS = CSS + """
#token-status { height: auto; max-height: 6; margin: 0 1; padding: 0 1; border: round $primary; }
#tokens { height: 1fr; min-height: 3; margin: 0 1; border: round $panel; border-title-color: $text-muted; }
#token-hint { height: 1; margin: 0 2; color: $text-muted; }
#token-actions { height: 3; margin: 0 1; }
#token-actions Button { width: 1fr; min-width: 8; padding: 0; }
#token-feedback { height: auto; max-height: 5; margin: 0 2; }
#token-result { height: auto; color: $text-muted; }
"""
    TITLE = "Claude OAuth"

    def __init__(self):
        super().__init__()
        self.cancel_probe = threading.Event()
        self.selected_alias = None
        self.last_action = ""

    def on_mount(self):
        self.push_screen(TokenHome())

    def on_unmount(self):
        self.cancel_probe.set()


def run_tui():
    while True:
        app = TokenApp()
        request = app.run()
        if getattr(app, "return_code", 0):
            return 1
        if request is None:
            print(tokenauth.launch_instructions())
            return 0
        try:
            if request.action in ("create", "import"):
                tokenauth.login(request.alias, generate=request.action == "create")
            elif request.action == "launch":
                tokenauth.run([])
            elif request.action == "legacy":
                from tui import run_tui as legacy_ui
                legacy_ui()
        except (cauth.CauthError, OSError) as exc:
            print(f"{exc}")
        except (KeyboardInterrupt, EOFError):
            print("Cancelled. Stored token unchanged.")
        try:
            input("Press Enter to return to Cauth. ")
        except (KeyboardInterrupt, EOFError):
            pass
