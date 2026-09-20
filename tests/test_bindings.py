"""Every advertised key must actually do something.

`q` on the home screen was bound, shown in the footer, and did nothing at all, because
its action was defined on the App rather than on the screen and Textual does not resolve
it that way. The failure was silent: no error, no log line, just a dead key on the screen
users spend all their time on.

This walks every Screen and ModalScreen in tui.py and asserts each BINDINGS entry
resolves to an action that exists on that class.
"""

from __future__ import annotations

import inspect

import pytest

textual = pytest.importorskip("textual", reason="TUI tests need Textual installed")

from textual.screen import Screen  # noqa: E402

import tui  # noqa: E402


def screen_classes():
    for name, obj in vars(tui).items():
        if inspect.isclass(obj) and issubclass(obj, Screen) and obj.__module__ == "tui":
            yield name, obj


def test_there_are_screens_to_check():
    """Guards against the walk silently finding nothing and passing vacuously."""
    found = dict(screen_classes())
    assert len(found) >= 6, f"expected the six screens plus modals, found {sorted(found)}"


@pytest.mark.parametrize("name,cls", list(screen_classes()), ids=lambda v: getattr(v, "__name__", v))
def test_every_binding_resolves_to_an_action_on_its_screen(name, cls):
    for binding in cls.BINDINGS:
        action = getattr(binding, "action", None) or (
            binding[1] if isinstance(binding, tuple) else None
        )
        assert action, f"{name} has a binding with no action"

        # Actions can carry arguments, e.g. "push_screen('x')". Only the name matters.
        method = "action_" + action.split("(")[0].strip()
        assert hasattr(cls, method), (
            f"{name} binds a key to '{action}' but has no {method}(). "
            "Textual will not find it on the App, so the key does nothing while the "
            "footer still advertises it."
        )


def test_the_home_screen_can_actually_quit():
    """The specific regression. Pinned by name so it cannot come back."""
    assert hasattr(tui.HomeScreen, "action_quit")


def test_every_screen_offers_a_way_out():
    """A screen with no escape route traps the user."""
    for name, cls in screen_classes():
        actions = {
            (getattr(b, "action", None) or "").split("(")[0].strip() for b in cls.BINDINGS
        }
        assert actions & {"back", "quit", "dismiss_false", "dismiss_none", "confirm"}, (
            f"{name} has no binding that leaves the screen"
        )
