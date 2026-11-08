# Continuous integration

Repository CI always produces the `ci/required` aggregate result.
Repository validation, regression tests, shared contracts and dependency planning run on every event.
The dependency planner selects an explicit validation mode from the verified Git comparison.

## Validation modes

| Mode | Eligible changes | Dependency builds | Native checks |
| --- | --- | --- | --- |
| Documentation | Only regular `README.md` or Markdown files below `docs/`, with no declared component input changed | Verified fastjsonschema baseline on all three target runners | Not selected; reported as skipped |
| Full | Any other change, mixed changes, unavailable history or empty comparison | The complete selected component matrix | Swift/macOS, C#/Windows, Kotlin/Linux and the Windows service result |

Documentation mode applies only to pull requests and known `main` push comparisons.
Manual, scheduled and merge-group runs use full validation.
Cold runs always use full validation.
Symlinks, executable files, invalid paths and comparisons exceeding 512 paths also require full validation.
Declared component inputs, verification inputs, source paths and watched paths override the Markdown allowance.

## Required results

The aggregate validates the actual job results and the successful planner's mode output.
In documentation mode, only macOS, Windows, Linux, Windows native regressions and metadata jobs may be skipped.
Repository checks, tests, contracts, planning and baseline dependency builds must succeed.
Failed, cancelled, missing and unexpected results cannot satisfy the aggregate.
Missing or invalid selection metadata fails validation.
Full validation requires every selected job to succeed.

The validated dependency matrix is partitioned by platform without changing its entries.
Each native application waits for its own platform producers.
The aggregate requires all three producer groups, including groups that another platform does not consume.
Windows native regression tests run in an isolated Windows job beside application assembly.
They remain mandatory in full validation.
The Windows service result and aggregate require both Windows jobs.

The workflow uses job conditions without workflow-level path filters.
The aggregate runs after failed or skipped prerequisites to inspect their actual results.
This preserves a stable required check for every comparison.
See [job dependencies](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs)
and [job conditions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idif).

## Acceptance boundaries

A documentation result does not qualify a native package, playback or a call.
Full native CI remains separate from real desktop, hardware and provider acceptance.
Application behavior still requires its applicable three-platform and six-direction checks.
Read-only permissions, source verification, cache isolation and bounded job timeouts apply in both modes.

## Source preparation reuse

CI can reuse explicit source archives prepared earlier in the same job.
It rechecks Git pins, clean checkouts, source inventories, original blobs, file modes, notices and toolchains.
Ordinary local builds and cold CI builds retain fresh source exports.

Git object caches contain object data only, without Git configuration, hooks, references or alternates.
They are keyed by pinned sources and export rules.
Restoration creates a new repository, checks object integrity offline and checks out the exact pin before normal source verification.
Only successful canonical main workflows can supply these caches.
One platform publishes each source key after its dependency checks succeed.
Fresh cache archives pass an offline restore check before publication.
Oversized optional object caches retain the fresh source build.
If the cache API is unavailable, CI fetches the original pinned sources and performs the same checks.
Corrupt archives, invalid metadata and failed Git verification remain fatal.
Temporary connection failures can retry the same read-only cache request twice.
Writes and received archive data are never replayed by this retry.
