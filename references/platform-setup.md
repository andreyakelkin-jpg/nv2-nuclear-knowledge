# Platform setup and transfer

The plugin package and the normative knowledge base are separate. The plugin may be installed from
GitHub or a Codex marketplace; the receiving user must also receive an authorized copy or mount of the
knowledge-base directory. Do not publish normative source files merely because the plugin repository is
public.

## Supported environments

- Windows 10/11 with Codex and PowerShell 5.1 or newer.
- Ubuntu Linux with Codex and a POSIX shell.
- Python 3.10 or newer with the packages in `scripts/requirements.txt`.

The portable entry point is `scripts/run.py`. `scripts/run.ps1` and `scripts/run.sh` are thin platform
wrappers and must return the same command output.

## First-time setup

Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File PLUGIN_ROOT/scripts/install.ps1 -KbRoot "C:\path\to\knowledge-base"
```

Ubuntu:

```bash
sh PLUGIN_ROOT/scripts/install.sh --kb-root /path/to/knowledge-base
```

Use `-SkipDependencies` or `--skip-dependencies` only when the selected Python environment already has
all declared packages. Set `NV2_NUCLEAR_PYTHON` when Codex must use a specific interpreter.

The installer records the external knowledge-base path in the per-user configuration:

- Windows: `%USERPROFILE%\.codex\nv2-nuclear-knowledge.yaml`
- Linux: `~/.codex/nv2-nuclear-knowledge.yaml`

`NV2_NUCLEAR_CONFIG_PATH` overrides the configuration file and `NV2_NUCLEAR_KB_ROOT` overrides only the
knowledge-base path. `NV2_NUCLEAR_STATE_ROOT` overrides mutable runtime state (routing events, runs, and
document security reports). It defaults to the knowledge-base root for backward-compatible writable desktop
installations. On a server with a read-only corpus it must point to a separate existing writable directory.
Never store machine-specific absolute paths in the plugin repository.

## Verification

Run the read-only diagnostic after installation, upgrades, dependency changes, or database transfer:

```text
RUNNER doctor
```

It checks the plugin manifest, Python version, dependencies, configured database, schema and integrity,
and the routing quality gate. A disabled or failed routing gate remains fail-closed to Sol/high. Do not
weaken that fallback to make diagnostics pass.

## Multi-host operation

Windows and Linux may use independent synchronized copies of the same knowledge base. `kb apply` uses a
non-blocking file lock to prevent overlapping writers that use this plugin on one supported filesystem.
If several hosts point to one network filesystem, still designate one writer: lock propagation depends on
the filesystem and there is no distributed lease or fencing. Concurrent `kb apply` operations from multiple
hosts remain unsupported.
