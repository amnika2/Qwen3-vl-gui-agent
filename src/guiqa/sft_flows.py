"""Action-space reference flows for SFT data collection.

Each flow is a list of callables(page) -> action dict, executed by
sft_data.sft_collect with the same execute()/capture() path as the agent loop.
Coordinates come from live element bounding boxes, normalized like model output.
"""

from typing import NamedTuple

from .browser import VIEWPORT

WIDTH, HEIGHT = VIEWPORT["width"], VIEWPORT["height"]


class SftFlow(NamedTuple):
    steps: list
    input_step: int
    submit_step: int
    review_steps: tuple = ()
    navigation_steps: tuple = ()


def normalized(box):
    x = round((box["x"] + box["width"] / 2) / WIDTH * 1000)
    y = round((box["y"] + box["height"] / 2) / HEIGHT * 1000)
    return [min(999, max(0, x)), min(999, max(0, y))]


def click_at(selector):
    def make(page):
        return {"action": "click", "coordinate": normalized(page.locator(selector).bounding_box())}
    return make


def click_locator(make_locator):
    def make(page):
        return {"action": "click", "coordinate": normalized(make_locator(page).bounding_box())}
    return make


def click_text(selector, text):
    return click_locator(lambda page: page.locator(selector).filter(has_text=text))


def scroll_to(selector):
    def make(page):
        box = page.locator(selector).bounding_box()
        pixels = int(box["y"] + box["height"] / 2 - HEIGHT / 2)
        return {"action": "scroll", "pixels": pixels} if abs(pixels) > 40 else {"action": "wait", "seconds": 1}
    return make


def press(key):
    return lambda page: {"action": "press", "key": key}


def type_text(text):
    return lambda page: {"action": "type", "text": text}


def emlog_profile(fixture, build):
    """sweeper-041: replace the profile nickname and save."""
    steps = [click_at("input[name=name]"), press("Control+a"), type_text(fixture["expected"]),
             click_at("#submit_form"), press("Control+r")]
    return SftFlow(steps, input_step=3, submit_step=4)


def appointments_provider(fixture, build):
    """sweeper-040: open the provider row, replace one field, save and reopen."""
    row = lambda page: page.locator(".provider-row").filter(has_text="jane@pippins.test")
    steps = [click_locator(row), scroll_to(fixture["selector"]), click_at(fixture["selector"]),
             press("Control+a"), type_text(fixture["expected"]), scroll_to("#save-provider"),
             click_at("#save-provider"), press("Control+r"), click_locator(row),
             scroll_to(fixture["selector"])]
    return SftFlow(steps, input_step=5, submit_step=7)


FLOWS = {"sweeper-041": emlog_profile, "sweeper-040": appointments_provider}


def pluxml_users(fixture, build):
    steps = [click_at(fixture["selector"]), press("Control+a"), type_text(fixture["input"]),
             click_at("input[name=update]"), press("Control+r"), click_at("a.logout"),
             scroll_to("a[href$='core/admin/']"), click_at("a[href$='core/admin/']"),
             click_at("input[name=login]"), press("Control+a"), type_text(fixture["test_username"]),
             click_at("input[name=password]"), press("Control+a"), type_text(fixture["test_password"]),
             click_at("input[type=submit]")]
    if fixture["kind"] == "name":
        steps.extend([click_at("a[href='parametres_base.php']"), click_at("a[href='parametres_users.php']")])
    return SftFlow(steps, input_step=3, submit_step=4)


FLOWS["sweeper-150"] = pluxml_users


def dokuwiki_text(fixture, build):
    """sweeper-034 (supplementary): replace a wiki page body, save, reopen the rendered page."""
    if "scenario_id" in fixture:
        from .sft_scenarios import scenario_flow
        return scenario_flow(fixture)
    steps = [click_at(fixture["selector"]), press("Control+a"), type_text(fixture["input"]),
             click_at("#edbtn__save"), press("Control+r")]
    return SftFlow(steps, input_step=3, submit_step=4)


def flatpress_text(fixture, build):
    """sweeper-053 (supplementary): replace a static page body, save, reopen the editor."""
    if "scenario_id" in fixture:
        from .sft_scenarios import scenario_flow
        return scenario_flow(fixture)
    steps = [scroll_to(fixture["selector"]), click_at(fixture["selector"]), press("Control+a"), type_text(fixture["input"]),
             scroll_to("#save"), click_at("#save"),
             click_locator(lambda page: page.locator(
                 f"a[href*='action=write'][href*='page={fixture['page_id']}']").first)]
    return SftFlow(steps, input_step=5, submit_step=6)


def wondercms_menu(fixture, build):
    """sweeper-196: open Settings → Menu, reorder a top-level item, reopen and verify.

    Clicking the arrow triggers a synchronous POST (target=menuItemOrder) followed by an
    automatic page reload, so the flow re-opens the modal and Menu tab to observe the result.
    """
    arrow = ".menu-item-down" if fixture["direction"] == "down" else ".menu-item-up"
    steps = [
        click_at(".settings.button"),
        click_at('ul.nav-tabs a[href="#menu"]'),
        click_at(f'{arrow}[data-menu="{fixture["menu"]}"]'),
        click_at(".settings.button"),
        click_at('ul.nav-tabs a[href="#menu"]'),
    ]
    return SftFlow(steps, input_step=2, submit_step=3)


def typesetter_text(fixture, build):
    """sweeper-185: edit a theme Area string via EditText, save, reopen the homepage.

    The seeded Shoremark theme exposes visible ``#edit-header`` / ``#edit-sidenote`` links
    (full-page navigation to ``Admin_Theme_Content/Text?cmd=EditText&key=...``). Saving is a
    synchronous POST that lands on the admin shell; the flow returns to the homepage through
    the breadcrumb Homepage link. The target workflow edits twice to exercise the
    wrong-customlang-key bug on the second save.
    """
    go_home = click_locator(lambda page: page.locator("#admin_breadcrumbs a").first)
    edit = [click_at(fixture["edit"]), click_at("input[name=value]"),
            press("Control+a"), type_text(fixture["input"]), click_at("input.gpsubmit"), go_home]
    if "first" not in fixture:
        return SftFlow(edit, input_step=4, submit_step=5)
    first_edit = [click_at(fixture["edit"]), click_at("input[name=value]"),
                  press("Control+a"), type_text(fixture["first"]),
                  click_at("input.gpsubmit"), go_home]
    return SftFlow(first_edit + edit, input_step=10, submit_step=11, review_steps=(0, 6))


FLOWS["sweeper-034"] = dokuwiki_text
FLOWS["sweeper-053"] = flatpress_text
FLOWS["sweeper-196"] = wondercms_menu
FLOWS["sweeper-185"] = typesetter_text


