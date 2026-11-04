# Blueprint review

Reviewed with Blueprint Studio 1.1.4 on 2026-11-04.

- **Missing CI: confirmed.** `.github/workflows/linux-checks.yml` now installs
  the Linux package, runs `pip check` and the unit suite with offscreen Qt, and
  builds a wheel on Python 3.12 and 3.14. Repository access is read-only and
  Actions are pinned to reviewed commit IDs.
  The runner also installs `desktop-file-utils`, required by the launcher's
  desktop-entry validation test.
- **13 tests without obvious assertions: false positives.** All flagged files
  contain Python assertion calls. The complete local unit suite passed 124
  tests. Keep the existing behavioral assertions.

Blueprint's framework-specific analysis does not cover this Python/Qt app.
Its score is not a measure of phone compatibility or test coverage. Unit tests
use doubles; the separate native GUI smoke tests and physical-device checks
remain necessary for changes to those behaviors.

## Follow-up improvements

- The large main-window module is separated by responsibility into window
  layout, route controls, editing, library/file actions, map interaction,
  reusable widgets and the background worker. Existing window methods keep
  their bodies and decorators, and public imports from `app` remain available.
- `tox.ini` provides isolated unit-test and lint environments. Ruff checks
  runtime mistakes without rewriting existing formatting.
- CI checks Ruff, runs the unit suite and builds the package containing the
  new modules. Local GUI smoke tests cover the real composed window and route
  library recovery with fake phone/network responses.

The external scan helper originally omitted `sourceMetrics` despite passing
source text. Corrected scans now compute those metrics like Blueprint's
directory reader. Vendored `idevice.h` and Leaflet CSS still trigger file-size
findings; do not rewrite third-party components just to improve that score.
The single comment marker is the word `hack` in Leaflet CSS describing a tile
rendering workaround; it does not identify an unfinished Locus feature.
