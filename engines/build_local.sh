#!/usr/bin/env sh
# Build the native flow_sim engine for THIS machine and drop it in engines/.
# Mirrors .github/workflows/build-engines.yml exactly, so the binary is
# interchangeable with a CI-built one. Run it from anywhere:  engines/build_local.sh
#
# macOS: needs the Xcode command-line tools (`xcode-select --install`) for clang++.
# Linux: needs clang (GCC <=13 ICEs on salabim++'s coroutines, so we require clang).
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/.." && pwd)
cd "$root"

os=$(uname -s)
case "$os" in
  Darwin)
    out="engines/flow_sim-macos-universal"
    echo "==> Building macOS universal (arm64 + x86_64)"
    clang++ --version | head -1
    clang++ -std=c++20 -O2 -arch arm64 -arch x86_64 \
      -Icpp/salabim++ -Icpp/simulation++ -Icpp/engine -Icpp/third_party \
      cpp/engine/main.cpp -o "$out"
    ;;
  Linux)
    out="engines/flow_sim-linux-x86_64"
    echo "==> Building Linux x86_64 (clang, static libstdc++)"
    clang++ --version | head -1
    clang++ -std=c++20 -O2 -static-libgcc -static-libstdc++ \
      -Icpp/salabim++ -Icpp/simulation++ -Icpp/engine -Icpp/third_party \
      cpp/engine/main.cpp -o "$out"
    ;;
  *)
    echo "Unsupported OS '$os'. On Windows run engines\\build_local.bat instead." >&2
    exit 1
    ;;
esac

chmod +x "$out"
echo "==> Smoke test (must print a line starting with @@DONE)"
"./$out" flow_designer/sample_flow_rate.json | grep -q '^@@DONE'
echo "==> OK: built and smoke-tested $out"
echo
echo "Now commit it:"
echo "    git add -f $out"
echo "    git commit -m 'engines: add $(basename "$out") (local build)'"
echo "    git push"
