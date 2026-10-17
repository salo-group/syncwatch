# Development setup

The repository checks require Git, Python 3.12 or newer, and Node.js 22 or newer. Repository policy checks use the standard library; contract checks load the pinned fastjsonschema submodule directly and install no packages. The manifests in `tools/ci/` and `tools/contracts/` declare their requirements. Editor formatting is defined by `.editorconfig`; `.gitattributes` keeps repository text in LF format.

Run from the repository root:

```sh
python3 tools/ci/check_repository.py
python3 -m unittest discover -s tools/ci/tests -v
node --test tools/ci/tests/artifact_upload.test.mjs
git submodule update --init --checkout --depth=1 -- 3rdparty/fastjsonschema
python3 tools/contracts/check_contracts.py
python3 -m unittest discover -s tools/contracts/tests -v
git diff --check
```

The checks validate first-party UTF-8 text, final newlines, whitespace, Python syntax, English documentation, local links, design JSON, component token references, source submodule registrations, and workflow policy. Upstream submodule content is excluded from first-party text and documentation rules. Source-registration checks inspect Git index entries and do not need dependency downloads. Contract checks additionally verify schemas, domain invariants, acceptance fixtures, framing bounds, and trusted process/generation/room context. See [shared contracts](architecture/contracts.md) for the wire specification and validation boundary.

CI tooling uses standard-library `unittest` and Node's built-in test runner. Tests create isolated temporary repositories and malformed inputs to verify meaningful failures, including broken links, invalid JSON/design values, copied vendor sources, missing Git links, unsafe workflow changes, unsuccessful job results, SDK extraction, and artifact transport. No npm installation is needed.

## Repository workflow

`.github/workflows/repository.yml` runs on pull requests, pushes to `main`, merge-group checks, and manual requests. It performs repository validation, CI tooling tests, shared contract checks/tests, macOS native checks, and Windows native checks as five independent jobs. The final `ci/required` job succeeds only when all five report `success`; missing, failed, canceled, skipped, or unknown results fail.

The workflow uses JSON-compatible YAML, allowing strict validation with the Python standard library. Use that format when editing this workflow. Checkout uses the hosted runner's Git and shell to fetch the exact GitHub workflow revision from this public repository. Only the contract job initializes its required fastjsonschema source, without recursing into upstream development dependencies. After source initialization, contract validation runs offline. Repository jobs use Ubuntu 24.04; native jobs use the declared macOS/Windows images. Each job checks the Python runtime requirement. A repository-owned Node 24 action retains prepared native archives without installing external action packages.

Workflow policy fixes the current job set and required aggregation. Introducing additional workflows or component jobs includes a corresponding policy/test update so their actual results participate in merge checks. Physical media acceptance is added by later deliverables.

Repository merge protection is a GitHub setting, separate from the workflow file. Require the actual `ci/required` status after it has run; preserve existing review and access rules when configuring it. Check a pull request's real merge eligibility and result for the selected revision rather than assuming the file alone enforces protection.

See [native development](native-development.md) for the implemented Swift/macOS and C#/WPF shells, declared SDKs, formatting, build/test commands, retained artifacts, and interactive launch/close checks. Repository policy also verifies native SDK selection, framework targets, local project references, and empty registry sources. Provider integration and real movie/call acceptance remain planned.

## Pull request destination

The canonical repository is `salo-group/syncwatch`; its `main` branch is the target for product pull requests and merges. A fork can publish working branches. Check the base repository explicitly before creating or merging a pull request.

For a checkout with a fork named `origin`, configure the canonical remote and GitHub CLI default:

```sh
git remote add upstream https://github.com/salo-group/syncwatch.git
git fetch upstream
gh repo set-default salo-group/syncwatch
```

Create pull requests with `gh pr create --repo salo-group/syncwatch --base main --head <fork-owner>:<branch>`. Use the same explicit repository for review, check, protection, and merge commands. Merge only after the canonical repository's required checks pass.
