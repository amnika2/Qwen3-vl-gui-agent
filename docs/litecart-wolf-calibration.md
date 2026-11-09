# LiteCart and Wolf CMS calibration

The source archives, pinned commits and archive SHA-256 values are recorded in
`docs/litecart-wolf-source-manifest.json`. Original archives remain under
`vendor/source-archives/`; benchmark rows in `cases/*/private.json` are unchanged.
Every accepted run records the SHA-256 of the installed business files in
`private/instance/business-source.json` and checks them against its pinned checkout.

## Deployment and observed compatibility issues

LiteCart runs with PHP 8.3 and an instance-owned MariaDB process on port 13221.
Its installer fetches replacement files over `stream_socket_client` even when
ordinary HTTP clients use the configured proxy. The instance-local PHP ini and
auto-prepend file disable that external fetch during collection, keeping the
pinned application source intact. They also stop dashboard update widgets from
waiting for external network calls. Application business files are unchanged.

The first country-selection pilot exposed an automation error: the native select
contains Åland Islands between Afghanistan and Albania. The observer rejected
that incorrect submission as inconclusive. Corrected keyboard indices were
calibrated with the actual submitted ISO code before final collection.

Wolf CMS runs with PHP 7.4 and an instance-owned MariaDB process on port 13222.
The bundled Kses sanitizer uses the removed PHP 5 regex `/e` modifier. A success
message initially appeared on build B while the title remained `About us` after
reopening. SQL logging confirmed that the update omitted the sanitized title;
the original logs remain in `docs/wolf-save-diagnosis.log`,
`docs/wolf-sql-diagnosis*.log` and `runs/wolf-sql-diagnosis2/sql.log`.

The application's native `allow_html_title=on` setting is now applied equally to
both builds. Requested titles are plain text. Multi-field tasks use Breadcrumb,
which is outside the incompatible title sanitizer. No business source was patched.
Post-change script calibration `litecart-wolf-ccb9e8df` verified build B's exact
persisted title and breadcrumb after reopening, and build A's intended rejection
of a new page with a duplicate sibling slug.

## Labels and retained calibration runs

LiteCart labels use the actual legal POST, completed save and reopened geo-zone
fields and country/city rows. A missing pending country is a failure only after
the correct country was submitted and the same saved geo-zone was reopened.

Wolf uses `business_outcome_v1`. Editing page 3 without changing its own slug must
save; its actual self-collision error is a failure. Creating another child of
the same parent with slug `about-us` must be rejected; that actual duplicate
error is a pass. Rejected saves keep `submission_succeeded=false` and
`final_record_visible=false`. Successful saves require exact values from the
reopened page, independently of the success message.

The old Wolf batch `litecart-wolf-c665ddcf` predates the business review profile.
The profile calibration batch `litecart-wolf-608e9271` predates the corresponding
operation prompt and additional title-input review screenshots. Both completed
12/12 observed business checks and are retained as calibration, outside the final
exports. All earlier failed deployment and pilot runs are also retained.

## Coverage and verification

Each application has 12 distinct complete scenarios with ten training and two
validation scenarios. Variations include list/editor starting points, field
order, multiple fields, new/existing records, French and Chinese values, and
intended validation rejection. The train/validation boundary is a complete
scenario; matching input variants are not repeated across builds or partitions.

LiteCart's final batch is `litecart-wolf-12fb9a41`, exported to
`datasets/sft-litecart-12fb9a41`: 198 samples (186 operations and 12 reviews),
163 train / 35 validation, with 116 bundled images and 3 fail / 9 pass reviews.
The public verifier passed; details are in that dataset's `verification.json`.

Wolf's final batch is `litecart-wolf-9c21067e`, exported to
`datasets/sft-wolfcms-9c21067e`: 154 samples (142 operations and 12 reviews),
128 train / 26 validation, with 104 bundled images and 3 fail / 9 pass reviews.
Its operation and review prompts both use `business_outcome_v1`. Review images
include each field's actual typed state and the observed outcome. The public
verifier passed all image, label, history, split and source-hash checks.

The targeted observer tests pass in `docs/litecart-wolf-tests.log`. They cover
incorrect submitted country, task-dependent scoring of a real duplicate error,
truthful evidence flags under the business profile, and the requirement to verify
persisted values after a success message. Chinese screenshots were visually
checked after the common Noto CJK font installation.

Final machine-readable dataset paths, counts, accepted batches and verification
records are written to `docs/litecart-wolf-sft-status.json` after export.
