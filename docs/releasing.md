# Releasing

Maintainers with push to `main`. A release is `vX.Y.Z`. Pushing the tag runs
the hub deploy ([../deploy/README.md](../deploy/README.md)); publishing the
GitHub release on that tag runs the PyPI publish. The order below is the
order that works.

## Before you start

- [ ] `main` is green.
- [ ] Bump the version in `pyproject.toml`, run `uv lock`, commit. The bump
      rewrites `uv.lock`, which is an arena image input, so every release
      carries a re-pin, and the images have to be built from the bumped
      tree: an arena built before the bump carries the old lock. Budget
      about ten minutes.
- [ ] Re-pin, and classify before you pin. This is also what to do when
      `arena/Dockerfile`, `nle-base/Dockerfile`, `uv.lock`,
      `src/nethackers/arena/` or `src/nethackers/contracts/` changed since
      the last `v*` tag; CI says "arena inputs changed since `<tag>` without
      re-pinning `ARENA_IMAGE`" on the PR.
      1. Dispatch `.github/workflows/sandbox-images.yml` on the branch. It
         builds and pushes the three images, then its own re-pin step stops
         on purpose: `scripts/repin_images.py` refuses a digest that is not
         in `ARENA_MAJOR_BY_DIGEST`, and a digest built minutes ago never is.
         A run red only at "Re-pin `_image_pins.py`" did its job; don't
         re-run it.
      2. Take the four refs from the run's job summary (`ARENA_IMAGE` there
         is already the `linux/amd64` leg). Classify the arena digest in
         `src/nethackers/arena_version.py`: a rebuild that cannot move a
         score gets a line at the current `ARENA_MAJOR`; one that can bumps
         it, which retires the verified corpus from every board until it is
         re-measured (nothing is deleted; [verifier.md](verifier.md)). The
         entry's comment says what the rebuild changed and why scores did or
         did not move; no gate checks that judgment.
      3. Write the pins and commit both files together:

         ```bash
         PYTHONPATH=src python3 scripts/repin_images.py \
           --arena "$ARENA_IMAGE" --mutator "$MUTATOR_IMAGE" \
           --nle-base "$NLE_BASE_IMAGE" --mutator-inputs "$MUTATOR_INPUTS"
         ```

- [ ] The mutator re-pin, if CI made one, has landed on the branch as a bot
      commit (a PR from a fork gets none: its image is published and pinned
      by the run on `main` after the merge). The publish job recomputes the
      mutator fingerprint and refuses a pin that does not match the tree
      before anything is uploaded.
- [ ] Both pinned image digests exist in GHCR; the publish job waits up to
      20 minutes for them and then fails.

## Release

- [ ] Merge to `main` FIRST. The PyPI job asserts tag == version, and PyPI
      never accepts a version twice, so a wrong number burns one.
- [ ] Tag and push:

      ```bash
      git tag vX.Y.Z && git push origin vX.Y.Z
      ```

      This builds the hub image and flips production to it by digest,
      health-checked, with automatic rollback. To keep a release off prod,
      put `[skip hub-deploy]` in the tagged commit's message; the check
      matches the whole message, so don't mention the marker in a commit you
      do want deployed.
- [ ] Publish:

      ```bash
      gh release create vX.Y.Z --title "vX.Y.Z" --generate-notes
      ```

      Publishing is release-triggered, not tag-triggered. If the tag does
      not exist yet, this creates and pushes it, which fires the deploy too;
      tag in the step above instead.

## Verify

- [ ] `curl --fail https://nethackers.dunnolab.ai/healthz` returns
      `{"status":"ok","auth":"github"}` and the masthead shows the new
      version.
- [ ] `uv tool install nethackers==X.Y.Z` in a clean environment, then
      `nethackers --version`. The publish job runs the same canary and waits
      up to five minutes for PyPI to serve the version.

## If it goes wrong

- A bad hub deploy: `deploy-hub.sh rollback` over the tailnet
  ([../deploy/README.md](../deploy/README.md#roll-back)); the deploy job
  rolls back on its own when the health check fails.
- The publish job timed out waiting for GHCR: re-run it with
  `workflow_dispatch` and `release_ref` set to the tag; the release already
  exists.
- A bad PyPI upload cannot be replaced. Bump the patch and release again.
- A wrong tag can be deleted only before anything consumed it, and the tag
  push itself starts the deploy, so that window is minutes.
