# engines/ — bundled native `flow_sim` binaries

These executables are **built and committed automatically** by the
`.github/workflows/build-engines.yml` GitHub Action whenever the engine or
simulation sources change on the `claude/cpp-engine` branch. Nothing here is
edited by hand.

| file | platform | how it's built |
|------|----------|----------------|
| `flow_sim-linux-x86_64` | Linux x86-64 | Clang, `-static-libstdc++` |
| `flow_sim-macos-universal` | macOS (arm64 + x86-64) | Apple Clang, universal binary |
| `flow_sim-windows-x86_64.exe` | Windows x64 | MSVC, `/MT` (static runtime) |

The flow designer's engine picker (Simulation Settings → Engine: C++) auto-selects
the file matching the host platform, or lets you point at one with **Select
executable**. Run it directly — same contract as `flow_designer/sim_runner.py`:

```
flow_sim-<platform> path/to/flow.json
```

## macOS: first run

The binaries are not notarized, so macOS Gatekeeper blocks a double-click the
first time. Right-click the file → **Open** → **Open** once; afterwards it runs
normally (the designer launches it the same way).
