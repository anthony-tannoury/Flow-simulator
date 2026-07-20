# engines/ — bundled native `flow_sim` binaries

The flow designer's engine picker (Simulation → Engine → C++) auto-selects the
file here that matches the host platform, or lets you point at one with **Select
C++ executable**. Run any of them directly — same contract as
`flow_designer/sim_runner.py`:

```
flow_sim-<platform> path/to/flow.json
```

| file | platform | how it's built |
|------|----------|----------------|
| `flow_sim-linux-x86_64` | Linux x86-64 | Clang, `-static-libstdc++` |
| `flow_sim-macos-universal` | macOS (arm64 + x86-64) | Apple Clang, universal binary |
| `flow_sim-windows-x86_64.exe` | Windows x64 | MSVC, `/MT` (static runtime) |

## How these get here

`.github/workflows/build-engines.yml` builds all three on GitHub-hosted runners
whenever the engine / simulation sources change on `claude/cpp-engine`, and
commits them back into this folder — nothing to download. **This requires GitHub
Actions to be runnable on the repository's account.** If a platform's file is
missing here, CI hasn't produced it yet; the designer simply falls back to the
Python engine (or the **Select C++ executable** button) for that platform, so the
app keeps working.

## Building one by hand

You don't need CI — build the binary for your own platform in one command (these
mirror the CI steps exactly). From the repo root:

```sh
# Linux (Clang; GCC ≤13 ICEs on the coroutines)
clang++ -std=c++20 -O2 -static-libgcc -static-libstdc++ \
  -Isalabim++ -Isimulation++ -Iengine -Iengine/third_party \
  engine/main.cpp -o engines/flow_sim-linux-x86_64

# macOS (Apple Clang; universal arm64 + x86-64)
clang++ -std=c++20 -O2 -arch arm64 -arch x86_64 \
  -Isalabim++ -Isimulation++ -Iengine -Iengine/third_party \
  engine/main.cpp -o engines/flow_sim-macos-universal
```

```bat
:: Windows (MSVC x64 Native Tools prompt; GCC won't work, use cl)
cl /std:c++20 /O2 /EHsc /MT /nologo ^
  /I salabim++ /I simulation++ /I engine /I engine\third_party ^
  engine\main.cpp /Fe:engines\flow_sim-windows-x86_64.exe
```

Smoke-test it the way CI does — this must print a line starting with `@@DONE`:

```
engines/flow_sim-<platform> flow_designer/sample_flow_rate.json
```

## macOS: first run

The binaries are not notarized, so macOS Gatekeeper blocks a double-click the
first time. Right-click the file → **Open** → **Open** once; afterwards it runs
normally (the designer launches it the same way).
