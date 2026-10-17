# Validation strategy

Validation establishes the product requirements and integration gates on both supported platforms. Source checkout, document checks, unit tests, and physical media acceptance provide different evidence and must be reported separately.

## Repository checks

Run from the repository root:

```sh
python3 tools/ci/check_repository.py
python3 -m unittest discover -s tools/ci/tests -v
git submodule update --init --checkout --depth=1 -- 3rdparty/fastjsonschema
python3 tools/contracts/check_contracts.py
python3 -m unittest discover -s tools/contracts/tests -v
git diff --check
```

These checks require Git, Python 3.12 or newer, and the declared validator source submodule. They do not require another workspace, a device profile, or an undeclared application. See [shared contracts](contracts.md) for wire limits, contextual acceptance, and the validation boundary.

Submodules must be initialized at their recorded Git revisions with no unexplained changes. Verify that external library entries use Git link mode `160000`. A clean checkout with `git submodule update --init --recursive` must obtain the pinned sources. This source-availability check does not establish dependency closure or a successful component build.

## Executable component checks

The [CI architecture](continuous-integration.md) schedules these checks. CI01 establishes repository checks, CI02 builds/tests both native clients, CI03 validates source dependency builds, CI04 runs controlled integration and separates physical acceptance, and CI05 produces trusted signed candidates. Every executable component PR adds its checks to the available CI jobs in the same change.

With the first implementation of each component, add its dependency manifest, test framework, formatter/linter configuration, and exact setup/build/test commands. Cover shared contracts and platform behavior using the cases in the [implementation plan](delivery-plan.md).

Design generation must reproduce identical resources and reject invalid tokens. Domain tests must cover lifecycle, ordering, and cancellation. Bridge tests must cover malformed, partial, duplicated, stale, and oversized messages. Metadata tests must cover authorization, snapshots, room isolation, and recovery.

## Native and provider acceptance

- Validate native video rendering, overlays, input, fullscreen, tracks, settings, preview isolation, and hardware decoding on both platforms.
- Validate Google Meet account access, two-way media, controls, capture release, and declared capabilities in the chosen application-owned hosts.
- Exercise simultaneous calling and synchronized playback on two physical devices with the same identified media fixtures.
- Verify local preference persistence, filesystem grants, permissions, helper cleanup, and clean-machine packaging.
- Compare native component states against the shared design specification in dark/light themes and all supported UI languages.

Acceptance evidence belongs with the relevant implementation change or release. Include reproducible commands or steps, versions, observed behavior, and unresolved failures. Keep credentials, meeting links, private media, device configuration, raw session output, and development diaries out of the source tree.

## Release condition

Every product requirement must have a passing acceptance result or an explicitly approved scope decision. G1-G5 in the [architecture](README.md) remain open until backed by the appropriate runtime and visual evidence. Successful document validation is not runtime acceptance.
