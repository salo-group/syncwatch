# Continuous integration

GitHub Actions is the CI orchestrator for SyncWatch. Repository-owned scripts under `tools/ci/` and `tools/dependencies/` run the same validation and build commands locally and on runners. The five CI deliverables below are part of the [pull request plan](pull-request-plan.md). CI01-CI03, shared contract checks, and regression tests are implemented. The validator source package exercises dependency infrastructure; other component graphs, integration, and release jobs remain planned. See [development setup](../development.md) for executable commands. Repository merge enforcement is configured and verified separately in GitHub.

## Delivery order

| Deliverable | Placement | Responsibilities |
|---|---|---|
| CI01 | Before PR01 | Repository checks, workflow policy, a stable aggregate status, and supported merge-rule enforcement |
| CI02 | After native shells in PR02 | macOS arm64 and Windows x64 formatting, builds, tests, and unsigned artifacts |
| CI03 | Before dependency integrations | Component source manifests, reproducible dependency build jobs, input digests, and cache isolation |
| CI04 | After synchronized playback in PR18 | Automated integration and a separate controlled path for interactive device/provider acceptance |
| CI05 | After packaging in PR23 | Trusted signed/notarized candidate builds and release eligibility checks |

Every later component PR adds its real jobs and test commands to the existing workflows. CI03 supplies build infrastructure; PR03, PR05, PR08, and PR10 supply the respective component manifests and functioning dependency builds. Unimplemented components are not reported as passing.

## Workflow responsibilities

| Workflow | Triggers | Work performed |
|---|---|---|
| Repository checks | Pull requests and pushes to `main` | Whitespace, English documentation and links, design JSON/token references, Git submodule metadata, workflow policy, shared schema/fixture validation, and CI/contract regression tests |
| Native checks | Relevant pull requests and pushes to `main` | Swift and C# formatting, builds, tests, generated-resource consistency, and component checks as introduced |
| Dependency builds | Relevant source/pin/toolchain changes; trusted scheduled or manual rebuilds | Build the affected complete source graphs, verify manifests/notices, and exercise cold builds |
| Integration | Relevant implementation changes; scheduled or manual runs where appropriate | Controlled Syncplay and metadata services, native/helper integration, deterministic fixtures, failures, and cleanup |
| Device acceptance | A trusted manual trigger for a selected revision | Interactive rendering, hardware decoding, real camera/microphone, account access, Meet capabilities, and combined media load |
| Release candidates | A trusted selected commit or release-candidate tag | Build, verify, sign/notarize, and retain candidate packages with immutable input/output digests |

If a merge queue is enabled, include its `merge_group` event in required automated checks. Select jobs from the changes and component manifests, while always producing the aggregate required status. Changes to CI scripts, dependency manifests, toolchains, or shared contracts trigger their affected dependents. See [GitHub workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

## Required result semantics

Use a stable `ci/required` check for merge decisions. Its required-job set follows the components implemented for that revision and the actual change impact. An intentionally irrelevant job can be excluded from that set; a selected job that fails, is canceled, or is unexpectedly skipped cannot satisfy it. Runner allocation, provisioning, authentication, and dependency failures remain visible failures or incomplete work.

The current workflow always requires repository validation, CI regression tests, shared contracts, macOS native checks, Windows native checks, dependency planning, and the selected dependency matrix. The validator source package remains a baseline on every event. Other ready components are selected from their declared inputs/watched paths; unavailable components are reported explicitly. Unknown comparison history selects all ready components. Scheduled and explicitly cold manual runs rebuild all ready components without cache reuse.

Test aggregation with deliberately failing fixtures and documentation-only changes. Avoid workflow-level path filtering that prevents the required status from being emitted. Configure branch protection or a repository ruleset only after the real status exists, and verify enforcement where repository permissions and account features permit it. Workflow YAML alone does not enforce merge policy.

Automated integration and physical/provider acceptance have distinct results. Synthetic devices, mocked capabilities, hosted compilation, and a loaded Meet page cannot satisfy real-call or hardware requirements. Final acceptance identifies the tested commit and exact candidate artifact digests.

## Runners and toolchains

- Native jobs target macOS arm64 and Windows x64. Select explicit supported runner images, record the image/toolchain versions, and assert the actual architecture at runtime. Pin Swift/Xcode compatibility, the .NET SDK, and other component toolchains in repository configuration as those components are introduced.
- Use hosted or isolated build runners for automated changes. Keep interactive acceptance on separately managed trusted devices; do not register a personal development machine as an unrestricted pull-request runner.
- Use bounded timeouts and per-change concurrency. Cancel superseded automated runs while preserving the evidence for the revision selected for release acceptance.

Read-only repository permissions are the default for pull-request jobs, and signing or meeting credentials are unavailable to them. Do not execute proposed application code with the privileged `pull_request_target` context. Follow [GitHub's secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).

## Source builds and caches

All library and build-tool sources follow the [submodule policy](dependencies.md), including transitive dependencies. Provision declared SDKs/tools, initialize exact source revisions, then run offline component build steps with implicit downloads disabled. Bootstrap checkout using runner Git/shell capabilities; source-based reusable actions or helpers must also follow the source policy, without floating remote references.

Cache identity includes the full source graph, build flags, target architecture, toolchain/SDK versions, build-script version, and artifact schema. A cache hit does not prove reproducibility: execute clean scheduled/manual rebuilds and rebuild affected components after relevant input changes. Verify artifact inputs and digests before reuse, and isolate untrusted artifacts/caches from trusted signing jobs.

The [dependency build infrastructure](../dependency-builds.md) implements exact-revision source initialization, Git archive SHA-256, input keys, bounded artifact verification, and reusable jobs. Only completed successful canonical main workflows can supply a shared artifact cache. Pull-request results cannot enter that trust scope. Every source-graph PR must add its real recipe, complete source inventory, download controls, and cache-free validation; the framework does not establish source closure for an unimplemented component.

Store build artifacts, sanitized test reports, and relevant screenshots as CI artifacts with a bounded retention policy. Keep generated outputs and raw session data outside Git. Dependency artifacts carry their source/build manifests and notices; they are not substituted for source submodules.

Native archives use the first-party action in `.github/actions/upload-native/` and Node's built-in HTTPS, filesystem, and hashing APIs. It implements GitHub.com's [Results artifact service interface](https://github.com/actions/toolkit/blob/main/packages/artifact/src/generated/results/api/v1/artifact.ts): create an archive artifact, upload its bytes to signed storage, and finalize its size/digest. The runner supplies its job-scoped runtime identity. This adapter targets GitHub.com, with no GitHub Enterprise Server compatibility claim. Transport errors fail the job; credentials, signed URLs, and service response bodies are excluded from diagnostics. Regression tests cover transport failures and credential suppression; live upload/download checks are required when changing this adapter.

The current retention limit is seven days, capped by the repository's limit. Each native archive includes a stage-level XML report and a manifest identifying the exact source and payload digests. See [native artifact contents and limitations](../native-development.md#native-ci-and-artifacts).

## Candidate builds and release acceptance

Pull-request builds produce unsigned candidates. CI05 invokes the PR23 packaging commands from a trusted selected revision, with environment-scoped signing credentials and only the permissions needed for that job. Verify source manifests, notices, signatures, and package digests. Missing credentials or failed signing produces an explicit failure.

PR24 runs final acceptance against those exact packages and reports automated and physical/provider evidence separately. Successful hosted checks alone do not satisfy G1-G5. Candidate generation does not automatically publish a release or deploy the metadata service; those are separate authorized operations.

## CI acceptance

Before considering CI established for a lane, demonstrate its actual GitHub execution, a meaningful failing case, correct propagation to its required result, and a rerun for the intended revision. Document reproducible commands and remaining limitations in the associated implementation review. Do not replace a failed or unavailable job with an unconditional success status.

Use the [GitHub workflow syntax reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) when implementing workflow dependencies, permissions, conditions, and artifacts.
