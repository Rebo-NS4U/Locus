# Blueprint review

Reviewed with Blueprint Studio 1.1.4 on 2026-11-04.

- **Missing CI: confirmed.** `.github/workflows/linux-checks.yml` now installs
  the Linux package, runs `pip check` and the unit suite with offscreen Qt, and
  builds a wheel on Python 3.12 and 3.14. Repository access is read-only and
  Actions are pinned to reviewed commit IDs.
- **13 tests without obvious assertions: false positives.** All flagged files
  contain Python assertion calls. The complete local unit suite passed 124
  tests. Keep the existing behavioral assertions.

Blueprint's framework-specific analysis does not cover this Python/Qt app.
Its score is not a measure of phone compatibility or test coverage. Unit tests
use doubles; the separate native GUI smoke tests and physical-device checks
remain necessary for changes to those behaviors.

No runtime source changed. The installed application needs no update or
restart. The workflow becomes active when this commit is pushed.
