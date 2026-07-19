# Release process

## Engineering release

1. Work on a `codex/` release branch and keep the backend suite, lint, Compose configuration and
   Android build green.
2. Open a pull request to `main`; require the CI workflow and a human review before merge.
3. Merge the pull request, bump both backend and Android versions, then create an annotated
   `vX.Y.Z` tag on that exact `main` commit and push the tag. The workflow rejects a tag that does
   not match both artifact versions, then rebuilds the backend wheel and debug APK and publishes
   both assets to the GitHub release.
4. Record the commit, tag, model/weight SHA-256 and deployment configuration in the release log.

## Field-release gate

Publishing a GitHub release is not authorization for field use. The owner must separately
complete [RELEASE_GATE.md](RELEASE_GATE.md): model licence/evaluation, verified calibration,
device qualification, supervised protocol, consent/privacy and a named stop authority.

## Rollback

Use the last approved tag and pinned model artifact. Stop affected sessions first; do not roll a
phone forward or back while it is actively assisting a participant. A model or safety incident
reopens the field-release gate and adds a replay regression before another session.
