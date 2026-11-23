# Security

## Reporting a vulnerability

Please report security problems privately, not in a public issue: open the
[Security tab](https://github.com/Czekoloczek/DeltaruneOnline/security) and choose **Report a vulnerability**.
Say what an attacker can do, and how to reproduce it.

This is a hobby project run by two people, with no bug bounty. We answer as soon as we can, and we credit you in
the fix unless you prefer not.

## What is in scope

- `drnet.dll` (`native/drnet.c`): it receives UDP packets from the other players, so anything a malicious player
  or a stranger on the network can do with crafted packets (crash, memory corruption, code execution, reading
  files) is in scope.
- The patcher (`patcher/`) and the scripts in `tools/`.
- The GitHub workflows in `.github/workflows/`.

Not in scope: DELTARUNE itself, the Local Multiplayer mod and UndertaleModTool; report those to their authors.
Desyncs and other game bugs are normal issues.

## Supported versions

Only the latest `main` gets fixes.
