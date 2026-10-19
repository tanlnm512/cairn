#!/bin/sh
# Render packaging/homebrew/cairn.rb.template into a Homebrew formula, filling
# the version and the sha256 of each release tarball.
# Usage: render_homebrew_formula.sh <template> <out-file> <version> <dist-dir>
# Expects cairn-cli-<version>-<target>.tar.gz for all four release targets in
# <dist-dir>.
set -eu

template="$1"
out="$2"
version="$3"
dist="$4"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

tarball() { echo "$dist/cairn-cli-$version-$1.tar.gz"; }

sha_aarch64_apple=$(sha256 "$(tarball aarch64-apple-darwin)")
sha_x86_64_apple=$(sha256 "$(tarball x86_64-apple-darwin)")
sha_aarch64_musl=$(sha256 "$(tarball aarch64-unknown-linux-musl)")
sha_x86_64_musl=$(sha256 "$(tarball x86_64-unknown-linux-musl)")

sed \
  -e "s/@VERSION@/$version/g" \
  -e "s/@SHA256_AARCH64_APPLE_DARWIN@/$sha_aarch64_apple/g" \
  -e "s/@SHA256_X86_64_APPLE_DARWIN@/$sha_x86_64_apple/g" \
  -e "s/@SHA256_AARCH64_UNKNOWN_LINUX_MUSL@/$sha_aarch64_musl/g" \
  -e "s/@SHA256_X86_64_UNKNOWN_LINUX_MUSL@/$sha_x86_64_musl/g" \
  "$template" > "$out"
