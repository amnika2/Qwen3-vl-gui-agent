"""Public native-control tools used by the frozen application tasks."""


CONTROLS = {
    "sweeper-049": [("upload", 'input[type="file"]', "Add files")],
    "sweeper-102": [("upload", 'input[name="import_file"]', "Bookmark import file")],
    "sweeper-083": [("select", 'select[name="payer"]', "Bill payer")],
    "sweeper-025": [("select", 'cp-fieldsmanager .uk-modal.uk-open select:visible', "Field type")],
    "sweeper-037": [("select", 'select[name="comment"]', "Comments filter"),
                    ("select", 'select[name="trackback"]', "Trackbacks filter")],
    "sweeper-001": [("upload", 'input[name="id_photo"]', "ID Photo")],
    "sweeper-009": [("select", 'select[name="permalink"], select#permalink', "SEO Permalink")],
    "sweeper-043": [("upload", '#importfileinput', "Import document")],
    "sweeper-074": [("select", '#location_id_from', "Transfer source location"),
                    ("select", '#location_id_to', "Transfer destination location"),
                    ("select", '#location-filter', "Stock location filter")],
}


def public_controls(case_id):
    return [{"action": action, "selector": selector, "label": label}
            for action, selector, label in CONTROLS.get(case_id, [])]
