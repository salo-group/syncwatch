# Dependency builds

`tools/dependencies/` provides the shared source-build, selection, and cache infrastructure. It uses Python 3.12 or newer and Node.js 22 or newer with no installed packages. The reusable GitHub job uses the runner's Node 24 runtime. Python is selected from the build host; each recipe declares its compatibility check and records the full actual version in its input key.

The current registered build creates an importable Python source package for fastjsonschema 2.21.2 on Linux x64, macOS arm64, and Windows x64. Its runtime graph has no third-party dependencies. The driver checks every packaged Python file, imports the built package, compiles the local schemas, exercises valid acceptance fixtures, and rejects an invalid envelope with socket access blocked. The complete semantic conformance oracle remains the separate contract job. This source package is not a wheel and does not invoke setuptools or download upstream development extras.

CEF/CefSharp, the design generator, the Syncplay runtime, and libVLC are explicitly **unavailable** until their source-graph PRs register real builds. This infrastructure does not close their source or runtime acceptance gates.

## Commands

Run from the checkout root:

```sh
python3 tools/dependencies/build.py check
python3 tools/dependencies/build.py plan
python3 -m unittest discover -s tools/dependencies/tests -v
node --test tools/dependencies/tests/cache.test.mjs
python3 tools/dependencies/build.py build --component fastjsonschema --platform macos-arm64 --cold
python3 tools/dependencies/build.py build --component fastjsonschema --platform macos-arm64
```

Use `linux-x64` on Linux. On Windows, use `python` and `windows-x64`. The second build verifies and reuses the matching local cache; `--cold` ignores it and rebuilds from source. The command fails on a corrupt cache instead of concealing the corruption with a rebuild. Delete the affected untracked cache file before retrying, or explicitly request a cold rebuild. No library package manager is run.

Build outputs, source snapshots, and request metadata are under `artifacts/dependencies/<component>/<platform>/`. Local caches are under `.local/dependency-cache/`. Both directories are ignored. `prepare` performs source/toolchain verification and writes `request.json`; the GitHub action uses it for trusted cache discovery before invoking the same `build` command.

## Component manifests

[`components.json`](../tools/dependencies/components.json) is the versioned registry. A ready component declares:

- Source roots and every transitively consumed source as an exact Git submodule under `3rdparty/`, including revision, license, notice paths, and dependency edges. Pins must match the Git index and HTTPS registrations. Every source must be reachable from a root.
- Reasons for unused upstream nested submodules. Consumed nested Git links must also have separately pinned source entries; initialization never recursively downloads undeclared development dependencies.
- First-party input files/directories, watched paths, and whether the component is required as a baseline on every revision. The validator remains a baseline because shared contract checks depend on it.
- Per-platform toolchain probes and anchored compatibility patterns, explicit environment and flags, an argument-array build command, and a bounded timeout. Toolchain provisioning belongs to the component's source/toolchain PR; a missing or incompatible toolchain fails preparation.

An unavailable entry has a reason and its planned deliverable, without a build command. It is displayed as unavailable in the plan and GitHub summary. It never appears as a successful component build.

Recipes receive `SYNCWATCH_DEPENDENCY_SOURCES`, `SYNCWATCH_DEPENDENCY_OUTPUT`, and JSON `SYNCWATCH_DEPENDENCY_FLAGS`. Sources are Git archive snapshots of the exact pins, with ambient Git checkout line-ending conversion disabled. Ignored worktree caches and generated files are excluded; dirty source checkouts are rejected before checkout changes. Drivers write generated outputs to the designated payload/scratch tree. The framework copies declared notices into the payload, requires real build output, and rejects symbolic links in packaged outputs.

The framework supplies offline controls for common package managers, disables Git lazy fetch and credential prompts, and points HTTP proxies at an unavailable local endpoint. Build children receive OS discovery variables and the declared environment, excluding runner credentials and ambient compiler flags. These controls are not an OS network sandbox. Each component driver must explicitly disable its own automatic dependency downloads and demonstrate a cache-free build from its complete declared graph. The source-package driver additionally blocks Python sockets during validation. The framework cannot infer an upstream dependency graph from a manifest assertion; graph completeness remains part of source review and G3 acceptance.

## Identity and verification

The SHA-256 input key includes:

- The complete component specification, selected platform, source URLs/revisions/trees, and SHA-256 of each exported Git archive.
- Content digests and Git modes of first-party inputs, dependency tooling, cache/upload adapters, and workflow definitions.
- Actual toolchain probe output, host architecture/release, runner image/version, declared flags/environment, offline controls, and artifact schema version.

An unrelated repository commit does not change the key when these inputs remain identical. Source, flags, scripts, toolchain, architecture, and image changes invalidate it. Missing comparison history selects all ready components; changes to framework/workflow/toolchain definitions select every ready component. Watched paths include deleted paths; rename detection is disabled for change selection so both old and new paths participate. Cheap baseline verification still runs on documentation-only changes; expensive future components can be omitted when irrelevant.

`artifact.zip` contains `build-manifest.json` and the payload, including source notices. The manifest records the exact input object/key, file SHA-256 and portable modes, original build commit/tree, currently verified commit/tree, local modification indicators, run identity, and cache result. Reuse retains the original producer identity and records the revision that verified reuse. Local modified inputs are hashed and identified; hosted builds reject tracked changes before and after building.

Restoration checks the expected key and full input object, successful result, exact file inventory, checksums, bounds, portable paths, modes, duplicates, case collisions, and symbolic links before extracting any file. Outputs are bounded at 256 MiB compressed and 1 GiB expanded. Larger component artifacts require a reviewed format/bounds update. Failed recipes retain a manifest-only failure artifact when preparation and packaging are possible. Preparation failures, cancellation, or hard timeouts can prevent retention. Upload failure fails the job.

## Hosted jobs and cache trust

The repository workflow validates manifests and produces a matrix for [the reusable dependency job](../.github/workflows/dependency-build.yml). Selected jobs use explicit Linux/macOS/Windows runner labels and assert their real OS/architecture. Every selected result participates in `ci/required`; a failed, canceled, or skipped selected matrix fails the aggregate. The reusable job is bounded at 270 minutes, with per-recipe limits of at most four hours. The current validator recipe has a five-minute limit.

Normal runs may restore an exact-key cache. A weekly scheduled run and manual dispatch with `cold=true` bypass caches and rebuild all ready components. To verify hosted reuse, dispatch the workflow on `main` with `cold=false` after a successful main build. Any image/toolchain/input change correctly produces a miss.

The first-party Node adapter uses the [GitHub Actions artifact REST API](https://docs.github.com/en/rest/actions/artifacts) and the existing repository upload adapter. It requires only `contents: read` and `actions: read`. Cache artifacts are accepted only from completed successful canonical-repository `main` runs of the repository workflow, triggered by push, schedule, or manual dispatch. Forks, pull-request runs, other branches/workflows, expired artifacts, and failed/incomplete runs cannot supply a shared cache. Signed-storage requests never receive the repository token; downloaded bytes must match GitHub's SHA-256 before local payload verification.

Only main push/scheduled/manual jobs publish `syncwatch-dependency-cache-<input-key>`. PRs retain their own result artifacts and write only a run-specific local scope; they cannot publish a cache trusted by consumers. Local command-line caches use a separate local scope. A bounded lookup may miss an old cache and trigger a cold build. API/authentication, download, or checksum failures remain errors. Retention is at most seven days. Cache verification does not establish bit-for-bit reproducibility, binary signing, or release eligibility.
