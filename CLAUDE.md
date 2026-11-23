# Notes for AI agents and reviewers

Claude Code, the Claude review workflow and CodeRabbit read this file. How the project works is in
[docs/DESIGN.md](docs/DESIGN.md); the rules for contributors are in [README.md](README.md#contributing).

## Keep the docs in step with the code

A pull request that changes what the project does, its settings, how to build or test it, or how far it is,
also updates the docs that describe it, in the same pull request:

- `README.md`: status, usage, roadmap, contributing;
- `docs/DESIGN.md`;
- every `dronline.ini` key: `config/dronline.ini.example` and the settings comment at the top of
  `gml/scr_dronline.gml`;
- the help text and header comments of the scripts in `tools/`, `patcher/` and `native/`.

The [wiki](https://github.com/Czekoloczek/DeltaruneOnline/wiki) (building, playing with a friend, settings,
troubleshooting, FAQ) is a separate repository, so a pull request cannot change it. The pull request description
lists the wiki pages that need an update after the merge.

**Reviewers:** check whether the change leaves any of these out of date. Name the file or wiki page, the
passage, and what it should say now.

## Never add game or mod files

No `data.win`, `.xdelta` patches, decompiled game or mod code, sprites, audio or built output, not even in
tests or examples. The patcher holds only rules that it applies to each player's own files.
