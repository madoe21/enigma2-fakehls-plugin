# Release pipeline

## Version convention

| Branch      | VERSION looks like | Who sets it |
|-------------|--------------------|-------------|
| `develop`   | `X.Y.Z-buildN`     | `build-develop` raises `N` on every push |
| `main`      | `X.Y.Z`            | `prepare-release` strips the suffix in the release PR |
| `hotfix/*`  | `X.Y.Z-HOTFIX`     | `aiflow hotfix` |

`main` never carries a pre-release suffix.

## develop: continuous builds

`build-develop.yml` runs on every push to `develop`:

1. raise `-buildN` in `VERSION`,
2. build the IPK with that exact version,
3. upload it as a run artifact (14 days),
4. commit the new `VERSION` back to `develop` — only if the build was green.

The bump commit is marked `[skip-bump] [skip ci]` so it cannot re-trigger itself.
Nothing on `develop` is ever tagged, released or pushed into the feed.

## main: releases

A release is cut by opening a PR `develop` → `main` (minor) or `hotfix/*` → `main` (patch).

`prepare-release.yml` runs while that PR is open: it strips `-buildN` / `-HOTFIX` from
`VERSION` and pushes the clean version **onto the release branch**, so `main` only ever
receives a clean `X.Y.Z`. (`main` is PR-protected and GitHub Actions cannot be granted a
ruleset bypass on a user-owned repo, so nothing may be committed to `main` afterwards.)

On the merge, `release.yml`:

1. refuses to run if `VERSION` on `main` still carries a suffix,
2. skips everything if `vX.Y.Z` is already tagged,
3. builds the IPK, tags `vX.Y.Z`, publishes the GitHub Release with the IPK attached,
4. dispatches `plugin-released` to `madoe21/enigma2-madoe21-feed` (failing loudly when
   `FEED_DISPATCH_TOKEN` is missing) and waits until the published feed index serves the
   new version,
5. bumps `develop` to `X.(Y+1).0-build1`.

`chore/*` → `main` never releases: `VERSION` is unchanged, so the tag already exists and
the release job stops at the tag check.

## Required secret

`FEED_DISPATCH_TOKEN` — PAT with `repo` scope on `madoe21/enigma2-madoe21-feed`, used for
the `repository_dispatch` that rebuilds the feed.

## Branch protection

Rulesets `protect-main` and `protect-develop`:

| | `main` | `develop` |
|---|---|---|
| merge only via PR | yes | no — the build job pushes the counter directly |
| `verify` must pass | yes (PR check) | PR check only, not enforced server-side |
| force push | blocked | blocked |
| deletion | blocked | blocked |

`develop` cannot be PR-only: `build-develop` and the post-release bump push `VERSION` with
`GITHUB_TOKEN`, and a user-owned repo cannot list the GitHub Actions app as a ruleset bypass
actor (`422 Actor GitHub Actions integration must be part of the ruleset source or owner
organization`). For humans, PR-only on `develop` stays enforced by the `pre-push` hook.
Moving the repos under an organisation would allow the strict variant.
