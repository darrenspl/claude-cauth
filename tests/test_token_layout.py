"""The account list and primary controls stay usable at common terminal sizes."""

import asyncio

import pytest

import tokenauth
import tokenui


@pytest.mark.parametrize("size", [(100, 30), (80, 24), (40, 15)])
@pytest.mark.parametrize("count", [1, 4])
def test_compact_account_layout_and_consistent_controls(size, count):
    for number in range(count):
        tokenauth.save(f"account-{number}", f"sk-ant-oat01-FIXTURE-layout-{number}")

    async def drive():
        app = tokenui.TokenApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screen = app.screen
            buttons = [screen.query_one("#" + name, tokenui.Button)
                       for name in ("create", "import", "launch", "cleanup")]
            assert len({button.region.y for button in buttons}) == 1
            assert max(button.region.width for button in buttons) - min(button.region.width for button in buttons) <= 1
            assert all(button.region.x >= 0 and button.region.right <= size[0] for button in buttons)
            assert all(button.region.bottom <= size[1] - 1 for button in buttons)
            assert all(button.content_region.width >= len(str(button.label)) for button in buttons)
            if size[0] < 50:
                assert [str(button.label) for button in buttons] == ["New", "Paste", "Run", "Clean"]
            listing = screen.query_one("#tokens", tokenui.ListView)
            assert listing.region.height >= 3
            assert listing.region.bottom <= screen.query_one("#token-hint").region.y
            item = listing.highlighted_child
            assert item.query_one(".account-title", tokenui.Label).render().plain.startswith("> account-0")
            assert item.query_one(".account-detail").region.x > item.query_one(".account-title").region.x
            await pilot.press("question_mark")
            assert isinstance(app.screen, tokenui.TokenTextScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, tokenui.TokenHome)
            assert app._exception is None

    asyncio.run(drive())
