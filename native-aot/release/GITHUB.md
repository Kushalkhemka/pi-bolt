# GitHub source and candidate artifacts

Use the clean `source-export.py` output as the repository root. It includes Apache-2.0 project code, pinned original upstream notices, exact engine patches, the npm lock and trained bytecode order. Engine checkout directories and binary output stay ignored. It is a source/export companion, not a proven complete offline LGPL relinking bundle.

```sh
python3 native-aot/release/source-export.py --output /absolute/fresh/github-source
cd /absolute/fresh/github-source
git init
git add .
git status --short
# Review, commit, add your chosen GitHub remote, then push.
```

No repository, remote, commit or GitHub Release is created by these scripts. Keep `native-aot/dist/`, npm dependencies, research receipts, credentials and private settings out of Git. Use Release assets for the candidate tarball, archive SHA256 and an optional evidence bundle after source/license distribution review. The public source workflow tests packaging and Photon guards; it does not certify runtime behavior.

Manual M5 workflow setup:

- Use a dedicated trusted self-hosted runner with labels `macOS`, `ARM64`, `m5`, `macos27`, Python3.12+ and built-in Apple tools. Base M5/macOS27 is the verified host contract.
- Configure environment `m5-candidate` with your normal environment review rules.
- Set repository/environment variables `PI_BOLT_ARTIFACT_DIR` to the validated, Photon-fixed artifact; `PI_BOLT_SOURCE_WORKSPACE` to its full original pinned source/notice workspace; `PI_BOLT_TOOL_CACHE` to a directory containing executable native `fd` and `rg` for offline tests.
- Dispatch `Validate M5 unsigned candidate` manually from a reviewed ref. It snapshots the existing validated runtime, rejects drift in source transform/compiler hashes, performs relocated source-denied acceptance, creates an archive and retains results as workflow artifacts. It does not compile the whole engine automatically or publish a Release.

Workflow actions are pinned to verified official repository commits. The native workflow has no pull-request trigger and does not receive signing secrets. It is prepared and locally source-reviewed; actual GitHub execution has not yet occurred.
