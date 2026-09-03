#!/usr/bin/env bash
# Build libacars-2.so against Debian 12 (matches irdm's bookworm image).
# The host DragonOS .so is ICU 74; irdm only has ICU 72 — do not bind-mount
# /usr/local/lib/libacars-2.so into irdm.
#
#   bash ~/acars-hub/irdm/build-libacars.sh
#
# Does not restart any feeder. After this succeeds, recreate only irdm:
#   sg docker -c 'cd ~/acars-hub && docker compose -f docker-compose.yml -f docker-compose.irdm.yml up -d irdm'
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/irdm/lib"
PREFIX="$ROOT/irdm/libacars-prefix"
SRC_DIR="$ROOT/irdm/libacars-src"
TAG="${LIBACARS_TAG:-v2.2.0}"
IMAGE="${LIBACARS_BUILD_IMAGE:-debian:bookworm}"

mkdir -p "$DEST" "$PREFIX"

d() { sg docker -c "$*"; }

echo "==> pulling $IMAGE (build only; does not touch irdm)"
d "docker pull $(printf %q "$IMAGE")"

echo "==> building libacars $TAG for bookworm → $DEST"
mkdir -p "$SRC_DIR"
d "docker run --rm \
  -e LIBACARS_TAG=$(printf %q "$TAG") \
  -v $(printf %q "$SRC_DIR"):/src \
  -v $(printf %q "$PREFIX"):/opt/out \
  -v $(printf %q "$ROOT/irdm/build-libacars-inner.sh"):/build.sh:ro \
  $(printf %q "$IMAGE") \
  bash /build.sh"

# Normalize to irdm/lib/ regardless of cmake libdir
SO=""
for cand in \
  "$PREFIX/lib/libacars-2.so.2" \
  "$PREFIX/lib/x86_64-linux-gnu/libacars-2.so.2" \
  "$PREFIX/lib64/libacars-2.so.2"
do
  if [[ -f "$cand" ]]; then SO=$cand; break; fi
done
if [[ -z "$SO" ]]; then
  echo "ERROR: libacars-2.so.2 not found under $PREFIX" >&2
  find "$PREFIX" -name 'libacars*' >&2 || true
  exit 1
fi
cp -a "$SO" "$DEST/libacars-2.so.2"
ln -sfn libacars-2.so.2 "$DEST/libacars-2.so"
chmod 644 "$DEST/libacars-2.so.2"
ls -l "$DEST"

echo "==> verifying the .so loads in the irdm image (new container; does not touch live irdm)"
d "docker run --rm \
  -v $(printf %q "$DEST"):/opt/libacars:ro \
  -e LD_LIBRARY_PATH=/opt/libacars \
  --entrypoint bash \
  ghcr.io/jkrasuk/docker-gr-iridium-toolkit:latest \
  -c 'ldd /opt/libacars/libacars-2.so; ldd /opt/libacars/libacars-2.so | grep -q \"not found\" && exit 1; cd /opt/iridium-toolkit && pypy3 -c \"from libacars import libacars; print(libacars.version)\" && echo LOAD_OK'"

echo
echo "Ready. Recreate only irdm to switch the reassembler:"
echo "  sg docker -c 'cd $ROOT && docker compose -f docker-compose.yml -f docker-compose.irdm.yml up -d irdm'"
echo "Then: sg docker -c 'docker exec irdm pypy3 -c \"from libacars import libacars; print(libacars.version)\"'"
