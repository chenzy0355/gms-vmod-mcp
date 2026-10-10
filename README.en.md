[简体中文](README.md) · [English](README.en.md)

# gms-vmod-grapher-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-6f42c1.svg)](https://modelcontextprotocol.io/)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](#)

An MCP server that connects Aquaveo GMS, Visual MODFLOW, and Golden Software
Grapher to AI clients.

GMS and Visual MODFLOW handle modelling and solving: the server reads and writes
standard MODFLOW files with FloPy, and drives the USGS engine executables bundled
with both packages through child processes. Loading a model, editing parameters,
running it, and reading heads and drawdown are all exposed as MCP tools.

Grapher handles post-processing plots: it performs no numerical computation, but
its `Scripter.exe` engine is driven by scripts to turn pumping-test fits,
scatter plots, and contours into `.grf` projects and `.png` figures.

The current version registers 39 tools and detects 16 engines.

## Background

GMS and Visual MODFLOW are GUI-based pre- and post-processors. Neither exposes an
open API that can be called directly. In GMS, `xms_api` is a client-side API that
requires a running GUI process. There is no way to automate either package
without its interface.

Grapher is Golden Software's dedicated plotting package. It does no numerical work
of its own, but ships a `Scripter.exe` scripting engine that can be driven from the
command line, which makes it a natural back end for plotting results.

What both packages do provide is standard output:

- models can be exported as standard MODFLOW input files (`.nam`, `.dis`, `.lpf`, `.wel`, ...)
- the install directory contains USGS-compiled engine executables (`mf2005.exe`, `Mf2k.exe`, `mt3dms.exe`, ...)

So reading and writing model files with FloPy and calling those engines as child
processes covers building, running, and post-processing a model without launching
the GUI.

## Division of labour between the three packages

GMS and Visual MODFLOW are both modelling and solving tools; Grapher is their
plotting back end.

| | Aquaveo GMS | Visual MODFLOW | Golden Software Grapher |
|---|---|---|---|
| Role | modelling + solving | modelling + solving | post-processing / plotting |
| Project format | `.gpr`, proprietary binary, not parseable externally | `.vmf`, XML, can be read and written programmatically | `.grf`, produced from `.bas` scripts |
| NAME FILE | must be exported to MODFLOW text from the GUI first | `*.mfi`, contains absolute paths from install time and needs localizing after moving | — |
| API | `xms_api`, client-side, requires a running GMS process | none | `Scripter.exe`, driven by command-line scripts |
| Engine location | `models/*/usgs/*.exe` | `Mf2k.exe` and others in the install root | `Scripter.exe` in the install root |
| Headless | yes, via the engines | yes, via the engines | yes, via scripts |

GMS and Visual MODFLOW both fall into the MODFLOW pre/post-processor category. The
part they have in common is the standard MODFLOW file layer. Grapher takes no part
in modelling or solving; it only consumes computed results and measured data and
produces figures.

## Supported engines

16 in total.

**GMS** (9; variants such as `_cfp_` and `_h5_` are skipped, and the stock builds
under `usgs/` are preferred)

| Type | Engines |
|---|---|
| Flow | MODFLOW-2000, 2005, NWT, MF6 |
| Transport | MT3DMS, MT3D-USGS, PHT3D |
| Particle tracking | MODPATH |
| Budget | Zone Budget |

**Visual MODFLOW** (7)

| Type | Engines |
|---|---|
| Flow | MODFLOW-2000 (`Mf2k.exe`), MODFLOW-96 |
| Transport | MT3DMS, MT3D96 |
| Particle tracking | MODPATH 3.2 |
| Budget | Zone Budget |
| Parameter estimation | PEST |

Engines are identified by logical name. Where the same name exists in both
packages, the `vendor` argument selects one.

## Installation

Python 3.10 or newer is required.

```bash
git clone https://github.com/chenzy0355/gms-vmod-grapher-mcp.git
cd gms-vmod-grapher-mcp

python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

Dependencies: `flopy`, `mcp`, `numpy`, `pandas`, `matplotlib`.

## Configuration

Copy the example config and fill in your local install paths:

```bash
copy config\env.example.json config\env.json
```

```jsonc
// config/env.json
{
  "gms_home": "C:\\Program Files\\GMS 10.4 64-bit",
  "vmod_home": "C:\\Program Files\\Visual MODFLOW 4.0",
  "grapher_home": "C:\\Program Files\\Golden Software\\Grapher 16",
  "workspace": "",          // empty means <repo>/workspace
  "extra_engine_dirs": []   // optional extra directories to search for engines
}
```

`config/env.json` is listed in `.gitignore` and will not be committed. When a
path is left empty, the `GMS_HOME`, `VMOD_HOME`, and `GRAPHER_HOME` environment
variables and the usual install locations are tried in order.

Self-checks:

```bash
set PYTHONPATH=%CD%\src

python -m mfmcp.server --check     # list all tools
python -m mfmcp.server --info      # show detected packages and engines
python tests\smoke.py              # adapter self-check
python tests\e2e.py                # run both engines and produce figures
```

On Windows you can also run `run.cmd --info` directly.

## Registering with an MCP client

The server uses stdio. Config file locations:

| Client | Config file |
|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `.cursor/mcp.json` |
| Cline / Roo Code | MCP settings in the extension |
| VS Code | `.vscode/mcp.json` |

Configuration:

```json
{
  "mcpServers": {
    "gms-vmod-grapher": {
      "command": "C:\\path\\to\\gms-vmod-grapher-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mfmcp.server"],
      "env": {
        "PYTHONPATH": "C:\\path\\to\\gms-vmod-grapher-mcp\\src",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

Do not set `PYTHONWARNINGS` in `env`. See [development notes](#development-notes).
Library warnings are already filtered in code.

## Tools

| Area | Tools |
|---|---|
| Environment and discovery | `mfm_env` `mfm_engines` `mfm_scan` `mfm_xms_status` |
| Visual MODFLOW projects | `vmod_info` `vmod_search_params` `vmod_list_params` `vmod_set_params` `vmod_set_engine` `vmod_run_engine` `vmod_read_namefile` `vmod_portable_namefile` `gms_export_hint` |
| Model I/O & Parameters | `mfm_load` `mfm_summary` `mfm_cached` `mfm_drop` `mfm_get_array` `mfm_set_array` `mfm_save` `mfm_bc_list` `mfm_bc_edit` |
| Diagnosis & Validation | `mfm_validate_model` `mfm_diagnose_log` |
| Analytical Benchmark & Sensitivity | `mfm_theis_benchmark` `mfm_sensitivity_analysis` `mfm_check_project` |
| Running | `mfm_run` `mfm_run_engine_direct` |
| Results | `mfm_heads` `mfm_drawdown` `mfm_budget` |
| Plotting | `mfm_plot_map` `mfm_plot_timeseries` `mfm_plot_compare` |
| Pumping test fitting | `mfm_theis_type_curve_fit` `mfm_jacob_straight_line_fit` |
| Grapher native plotting | `mfm_generate_grapher_script` `mfm_run_grapher_script` |

### Tool Descriptions

#### Pumping Test Fitting
- `mfm_theis_type_curve_fit`: Theis log-log type curve matching for unsteady flow in confined aquifers. Fits observed drawdown data against $W(u)$ to determine transmissivity $T$ and storage coefficient $S$. Can generate Grapher 16 scripts (.bas) with dual shifted coordinate axes and export .grf projects and .png figures via Scripter.
- `mfm_jacob_straight_line_fit`: Cooper-Jacob semi-log straight-line method. Filters data points with $u \le 0.05$, performs linear regression on $s$ vs. $\lg t$, extracts slope $\Delta s$ and intercept $t_0$, and solves for $T$ and $S$. Can generate Grapher 16 scripts and figures with regression summary boxes.

#### Grapher Native Plotting
- `mfm_generate_grapher_script`: Generates Grapher 16 BASIC automation scripts (.bas) for plotting.
- `mfm_run_grapher_script`: Executes a given .bas script via the Grapher 16 Scripter executable.
  The main Grapher process is pre-launched to host the COM interface and is
  recycled afterwards so that no background handles are left behind.

#### Diagnostics & Analytical Benchmarks
- `mfm_validate_model`: Validates grid geometry, layer elevations, starting head ranges, and pumping/injection sign conventions.
- `mfm_diagnose_log`: Parses listing files (.lst) to extract convergence status, maximum residual cells, dry cells, and volumetric water budget discrepancy.
- `mfm_theis_benchmark`: Compares numerical drawdown results against the analytical Theis solution, reporting MAE and RMSE.
- `mfm_sensitivity_analysis`: Perturbs hydraulic parameters across specified scale factors, runs batch simulations, and extracts observation head responses.
- `mfm_check_project`: Verifies directory structure and references in the MODFLOW NAME file.

## Workflow

For example, "halve the hydraulic conductivity, scale pumping by 1.5, run it,
and plot drawdown contours":

```
mfm_env                        inspect both packages and available engines
vmod_info / mfm_scan           find the model at hand
mfm_load  → mfm_summary        load it and view a summary
mfm_set_array / mfm_bc_edit    edit parameters and boundary conditions
mfm_run(vendor="vmod")         run it; vendor is "gms" or "vmod"
mfm_heads / mfm_drawdown       read heads and drawdown
mfm_plot_map                   plot contours
```

To run without loading a model into the session, use
`mfm_run_engine_direct(engine_key, namefile)`.

## Verification

A confined aquifer pumping model (1 layer, 15 × 15 grid, T = 500 m²/d, 1000 m³/d
extracted at the centre cell), run once with each package's engine:

| Engine | Termination | Head range | Drawdown at well |
|---|---|---|---|
| GMS `usgs/mf2005.exe` | Normal termination | 28.27 – 30.00 m | 1.73 m |
| Visual MODFLOW `Mf2k.exe` | converged (this build prints no Normal termination) | 28.27 – 30.00 m | 1.73 m |

| GMS `mf2005.exe` | Visual MODFLOW `Mf2k.exe` |
|---|---|
| ![GMS engine result](docs/images/e2e-mf2005.png) | ![Visual MODFLOW engine result](docs/images/e2e-mf2k.png) |

To reproduce: `python tests\e2e.py` rebuilds the model, runs both engines, and
writes figures to `workspace/figs/`.

## Code layout

```
src/mfmcp/
├── server.py              MCP entry point; supports mcp 1.x (FastMCP) and 2.x (MCPServer)
├── env.py                 package and engine detection, engine invocation, config loading
├── state.py               in-memory cache of loaded models
├── adapters/
│   ├── vmod.py            .vmf XML read/write, .mfi localization, parameter scanning
│   ├── gms.py             GMS project discovery, xms_api probing
│   └── mfmodel.py         FloPy model loading, parameter edits, running, results
└── tools/                 MCP tool registration, split by area
```

Call flow:

```
AI client → mfmcp.server → tools/
                            ├── adapters/vmod.py      parse .vmf
                            ├── adapters/gms.py       GMS projects and engines
                            └── adapters/mfmodel.py   FloPy model I/O
                                      ↓
                              standard MODFLOW files
                                      ↓
                              env.run_engine
                                      ↓
                        GMS usgs/*.exe or VMod Mf2k.exe
                                      ↓
                                 .hds / .bud
                                      ↓
                        heads / drawdown / budget → plotting
                                      ↓
                        Grapher Scripter.exe (.bas → .grf / .png)
```

To add a capability, drop a module with a `register(mcp)` function into
`src/mfmcp/tools/` and list it in `_MODULES` in `tools/__init__.py`. Adapters go
in `src/mfmcp/adapters/`.

## Known limitations

- Windows only. The engine executables and path handling depend on it.
- Requires a licensed local installation of GMS or Visual MODFLOW. This project
  does not bundle or redistribute any vendor engine or solver.
- GMS `.gpr` projects cannot be parsed directly; export them to MODFLOW text from the GUI first.
- `xms_api` is used only to probe a running GMS instance and is not a dependency.
- Coverage is currently focused on the MODFLOW-2000 / 2005 / NWT family. MF6
  engines are detected but not fully wired up.
- Grapher output requires a licensed local installation of Golden Software Grapher
  (Grapher 16 in the development environment). No such software is bundled or
  redistributed here.
- Grapher is driven through `Scripter.exe`: the main process is pre-launched to
  host the COM interface and is recycled after the script finishes, otherwise
  background handles are left behind.

## Development notes

These were hit during development. Worth reading before changing the related code.

1. Do not name the project directory `mcp`. Python treats it as the `mcp`
   namespace package, so `import mcp` resolves to this project and the official
   MCP SDK cannot be imported.
2. Do not set `PYTHONWARNINGS=ignore` for the MCP server. With it set, the server
   answers `initialize` but hangs on `tools/list`. Filter warnings in code with
   `warnings.filterwarnings` instead.
3. Older USGS engines read the NAME FILE name from stdin and accept no command
   line arguments. VMod's `Mf2k.exe` treats argv as a project name prefix.
   `env.run_engine` retries between the two conventions.
4. VMod's `Mf2k.exe` does not print `Normal termination`. Success is determined
   by no errors, convergence, and fresh `.hds` / `.lst` output instead.
5. GMS ships several variants of the same engine: `_cfp_` (pipe flow, different
   physics), `_h5_` (HDF5 wrapper), `_parallel`, `_dbl`. `env` prefers the stock
   builds under `models/*/usgs/`.
6. In the `mcp` SDK 2.x, `FastMCP` was renamed to `MCPServer` and the
   `InitializeResult` field `serverInfo` became `server_info`. `server.py`
   handles both 1.x and 2.x.
7. `.vmf` files are backed up to `.bak` before editing. `vmod_set_params`
   defaults to `dry_run=True`; write only after checking the result.
8. Golden Software Grapher automation is driven via `Scripter.exe`. The main application is pre-launched to host COM automation endpoints, and processes are terminated upon completion.

## License

[MIT](LICENSE)

This project is not affiliated with Aquaveo LLC, Waterloo Hydrogeologic, or
Golden Software LLC. GMS, Visual MODFLOW, and Grapher are trademarks of their
respective owners. No vendor software is included or redistributed here.
