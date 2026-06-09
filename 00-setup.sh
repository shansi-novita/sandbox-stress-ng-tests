#!/usr/bin/env bash
# Phase 1 — install all benchmark tools needed by phases 2 & 3.
# Idempotent: re-running only installs what's missing.
# Supports apt (Debian/Ubuntu) and apk (Alpine); lmbench/UnixBench are built from source.
#
# Usage:  ./00-setup.sh            # install everything
#         SKIP_SOURCE=1 ./00-setup.sh   # only package-manager tools, skip lmbench/UnixBench build
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"

section "Phase 1: tool setup"

# Packaged tools we want available. (lmbench & UnixBench handled separately below.)
APT_PKGS="sysbench stress-ng fio ioping iperf3 netperf openssl p7zip-full wrk schbench rt-tests build-essential git curl dnsutils"
APK_PKGS="sysbench stress-ng fio ioping iperf3 openssl p7zip wrk build-base git curl bind-tools"

install_apt() {
  log "apt-get update + install"
  export DEBIAN_FRONTEND=noninteractive
  sudo apt-get update -y >/dev/null 2>&1 || apt-get update -y >/dev/null 2>&1 || true
  # Install per-package so one missing pkg doesn't abort the rest.
  for p in $APT_PKGS; do
    if dpkg -s "$p" >/dev/null 2>&1; then continue; fi
    sudo apt-get install -y "$p" >/dev/null 2>&1 || apt-get install -y "$p" >/dev/null 2>&1 \
      || warn "apt could not install $p"
  done
}

install_apk() {
  log "apk add"
  for p in $APK_PKGS; do
    apk info -e "$p" >/dev/null 2>&1 && continue
    sudo apk add --no-cache "$p" >/dev/null 2>&1 || apk add --no-cache "$p" >/dev/null 2>&1 \
      || warn "apk could not install $p"
  done
}

if have apt-get; then
  install_apt
elif have apk; then
  install_apk
else
  warn "no supported package manager (apt/apk) found — install tools manually"
fi

# --- lmbench (latency microbenchmarks: lat_syscall, lat_proc, lat_ctx, lat_mem_rd) ---
LMBENCH_BIN_DIR=""
build_lmbench() {
  if have lat_syscall; then log "lmbench already present"; return 0; fi
  [[ "${SKIP_SOURCE:-0}" == "1" ]] && { warn "SKIP_SOURCE set — skipping lmbench build"; return 0; }
  have git || { warn "git missing — cannot build lmbench"; return 0; }
  local src="${_PERF_ROOT}/.tools/lmbench"
  log "building lmbench into ${src}"
  rm -rf "$src"
  if ! git clone --depth 1 https://github.com/intel/lmbench "$src" >/dev/null 2>&1; then
    warn "lmbench clone failed (no network?) — phase 3 will skip lmbench cases"; return 0
  fi
  ( cd "$src" && make build >/dev/null 2>&1 ) || warn "lmbench build had errors"
  # Binaries land under bin/<arch>/ ; add to PATH via symlinks in .tools/bin
  local found
  found="$(find "$src/bin" -name lat_syscall -type f 2>/dev/null | head -1)"
  if [[ -n "$found" ]]; then
    LMBENCH_BIN_DIR="$(dirname "$found")"
    mkdir -p "${_PERF_ROOT}/.tools/bin"
    ln -sf "$LMBENCH_BIN_DIR"/* "${_PERF_ROOT}/.tools/bin/" 2>/dev/null || true
    log "lmbench built -> ${LMBENCH_BIN_DIR}"
  else
    warn "lmbench binaries not found after build"
  fi
}
build_lmbench

# --- UnixBench (B10 composite score) ---
build_unixbench() {
  if [[ -x "${_PERF_ROOT}/.tools/byte-unixbench/UnixBench/Run" ]]; then log "UnixBench present"; return 0; fi
  [[ "${SKIP_SOURCE:-0}" == "1" ]] && return 0
  have git || return 0
  local src="${_PERF_ROOT}/.tools/byte-unixbench"
  log "building UnixBench into ${src}"
  rm -rf "$src"
  if ! git clone --depth 1 https://github.com/kdlucas/byte-unixbench "$src" >/dev/null 2>&1; then
    warn "UnixBench clone failed — B10 will skip"; return 0
  fi
  ( cd "$src/UnixBench" && make >/dev/null 2>&1 ) || warn "UnixBench build had errors"
}
build_unixbench

# Persist a tools PATH fragment that later phases source automatically via common.sh consumers.
echo "export PATH=\"${_PERF_ROOT}/.tools/bin:${_PERF_ROOT}/.tools/byte-unixbench/UnixBench:\$PATH\"" \
  > "${_PERF_ROOT}/.tools/env.sh"

section "Phase 1: setup complete"
log "tools PATH fragment written to ${_PERF_ROOT}/.tools/env.sh"
log "verify with: source ${_PERF_ROOT}/.tools/env.sh && ./01-baseline-env.sh"
