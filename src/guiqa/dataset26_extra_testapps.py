"""Visible validation and form-prefill tests for the two remaining test apps."""


def wger_flow(r, url, fixture):
    from .dataset26_webapps import check, finish

    page = r.page
    r.goto(url + "/en/user/preferences")
    old_email = page.locator('input[name="email"]').input_value()
    old_height = page.locator('input[name="height"]').input_value()
    initial = r.index
    for name in ("email", "first_name", "last_name", "height"):
        r.fill(page.locator(f'input[name="{name}"]'), fixture[name])
    # The focused number input consumes wheel events and changes Height.
    r.press("Tab")
    before = r.index
    form = page.locator("form.wger-form")
    r.click(form.locator('input[type="submit"], button[type="submit"]'), navigation=True)
    r.scroll(-1600)
    body = page.locator("body").inner_text()
    error = "already in use" in body or "Please correct" in body
    response = r.index
    r.goto(url + "/en/user/preferences")
    email = page.locator('input[name="email"]').input_value()
    height = page.locator('input[name="height"]').input_value()
    expected_email = fixture["email"] if fixture["preservation"] else old_email
    expected_height = fixture["height"] if fixture["preservation"] else old_height
    checks = [check("email_after_reload", expected_email, email, email == expected_email, r.index),
              check("height_after_reload", expected_height, height, height == expected_height, r.index),
              check("visible_validation_error", not fixture["preservation"], error,
                    error != fixture["preservation"], response)]
    if fixture["preservation"]:
        for name in ("first_name", "last_name"):
            actual = page.locator(f'input[name="{name}"]').input_value()
            checks.append(check(name + "_after_reload", fixture[name], actual, actual == fixture[name], r.index))
    return finish(r, checks, [initial, before, response, r.index],
                  f"Validation error visible={error}; reopened Email={email!r}, Height={height!r}.")


def evershop_flow(r, url, fixture):
    from .dataset26_webapps import check, finish

    page = r.page
    r.click(page.get_by_role("link", name="Products", exact=True))
    r.fill(page.locator('#productGridFilter input[name="keyword"]'), fixture["product"])
    r.press("Enter")
    r.click(page.get_by_role("link", name=fixture["product"], exact=True))
    if fixture["preservation"]:
        quantity = page.locator('input[name="qty"]')
    else:
        row = page.locator("tr").filter(has_text=fixture["sku"])
        r.click(row.get_by_role("link", name="Edit", exact=True))
        quantity = page.locator('input[name="variant_qty"]')
    quantity.wait_for(state="visible")
    r.click(quantity)
    actual = quantity.input_value()
    checks = [check("quantity", fixture["expected"], actual, actual == fixture["expected"], r.index)]
    return finish(r, checks, [r.index], f"Visible quantity input for {fixture['product']}: {actual!r}.")


FLOWS = {"sweeper-192": wger_flow, "sweeper-045": evershop_flow}
