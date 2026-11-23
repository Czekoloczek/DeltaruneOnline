# Patcher

Scripts that turn **your own** game and mod files into the DeltaruneOnline build. Nothing from the game or the
mod is stored here; only our own code and the rules for where it goes.

## Chapter 5 network test (Phase 1)

| File | What it is |
|---|---|
| `build.py` | Runs the whole build: checks your files, applies Local Multiplayer v19, adds our code. |
| `ch5/net_test.csx` | UndertaleModTool script: safety checks, adds `obj_dro_net`, hooks it into `obj_time`. |
| `ch5/net_test/*.gml` | Our GameMaker code. Each file name is the code entry it becomes in `data.win`. |

### Tools you need

- Python 3.10 or newer
- [xdelta3](https://github.com/jmacd/xdelta-gpl/releases) (the same tool Delta Patcher uses inside)
- The UndertaleModTool command-line tool (`UndertaleModCli`), built from
  [UndertaleModTool](https://github.com/UnderminersTeam/UndertaleModTool) with the .NET 10 SDK:
  `dotnet build UndertaleModCli/UndertaleModCli.csproj -c Release -o <folder>`
- Your DELTARUNE 1.05 (Steam) and Yomsman's Local Multiplayer v19 from GameBanana

### Build

```
python patcher/build.py --game "<DELTARUNE folder>" --mod "<Local Multiplayer v19 folder>" \
    --utmt "<folder>/UndertaleModCli.dll" --out build
```

The result is `build/chapter5_windows/data.win`. Add `--make-patch` to also get a small `.xdelta` file that turns
the original Chapter 5 `data.win` into the test build, for a friend who has their own copy of the game.

The script stops with a message, and writes nothing, if your `data.win` is a different version or if the code
we hook into has changed.

Tested on Linux with UndertaleModTool at commit `fbd4e79` (2026-11-21). Expected result for the current code:
the decompiled build differs from Local Multiplayer v19 only in the new `obj_dro_net` / `scr_dro_net` entries and
four new lines at the end of `gml_Object_obj_time_Create_0`.
