#!/usr/bin/env bash
# Usage: check_one_manifest.sh <image ref> <os/arch>
#
# Exits 0 only if <image ref> names ONE image manifest for <os/arch>. It rejects
# any index, including the one buildx pushes by default for a single-platform
# build: that platform's manifest plus a provenance attestation. A client
# resolves an index to its own platform, and on Apple Silicon that is arm64
# NetHack, which plays a different game per seed than the amd64 arena scores.
# The mutator workflows run this before they trust or pin a mutator image.
# Needs docker buildx and jq; prints the reason for a rejection.
set -euo pipefail

ref="$1"
want="$2"

media=$(docker buildx imagetools inspect --raw "$ref" | jq -r .mediaType)
case "$media" in
  application/vnd.oci.image.manifest.v1+json|application/vnd.docker.distribution.manifest.v2+json) ;;
  *)
    echo "$ref is not one image manifest (mediaType: $media)"
    exit 1
    ;;
esac

got=$(docker buildx imagetools inspect "$ref" --format '{{json .Image}}' | jq -r '.os + "/" + .architecture')
if [ "$got" != "$want" ]; then
  echo "$ref is a $got image, not $want"
  exit 1
fi
