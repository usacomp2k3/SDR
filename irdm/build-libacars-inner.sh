#!/usr/bin/env bash
# Runs inside debian:bookworm. Called by build-libacars.sh.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
TAG="${LIBACARS_TAG:-v2.2.0}"

apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  ca-certificates git cmake make gcc g++ pkg-config \
  zlib1g-dev libxml2-dev

if [[ ! -d /src/.git ]]; then
  rm -rf /src/lost+found
  find /src -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  git clone --depth 1 --branch "$TAG" https://github.com/szpajder/libacars.git /src
fi

cmake -S /src -B /tmp/build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/out
cmake --build /tmp/build -j"$(nproc)"
cmake --install /tmp/build

SO=""
for cand in /opt/out/lib/libacars-2.so /opt/out/lib/x86_64-linux-gnu/libacars-2.so /opt/out/lib64/libacars-2.so; do
  if [[ -e $cand ]]; then SO=$cand; break; fi
done
if [[ -z $SO ]]; then
  echo "ERROR: install did not produce libacars-2.so" >&2
  find /opt/out -name 'libacars*' >&2 || true
  exit 1
fi
echo "installed $SO"
ldd "$SO"
echo BUILD_OK
