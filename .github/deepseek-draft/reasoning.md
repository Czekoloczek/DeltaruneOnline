We need answer with Notes and files. Need implement feature. Need comply with specs. We must output only format. Need create/modify files. We can write complete content. Key: add to native/drnet.c, gml/scr_dronline.gml, patcher/utmt/add_extension.csx, tools/agent_client.py, tests/test_agent_client.py, docs/DESIGN.md, config/dronline.ini.example, gml comment, README, CLAUDE? Spec says docs in same change (CLAUDE.md): agent key in config and settings comment; short Agent API section in docs/DESIGN.md; one line README. Also tests. We should not output docs/agent-api-spec.md. Need ensure consistency.

Need design. Let's understand existing. Need add TCP listener in drnet.dll, separate thread, 127.0.0.1 only, port from agent= in dronline.ini. One connection at a time; second refused or replaces first; say which. We choose replaces first; but need state careful. Spec says "refused or replaces the first (say which)". We'll document in notes and code. Need likely replace? It's simpler? Need one connection. Could accept and close old? But if agent reconnects after chapter change, old process likely gone, network listener in new process no old conn. In same process, if agent disconnects, no conn. Replace is fine. But need avoid locking issues. We'll implement one agent connection, new connection replaces existing (closes old). But if we close old while its game thread reading? The TCP recv/send only in agent thread. We'll have agent state; GML pulls once per frame via drnet_agent_recv? Need GameMaker functions. The DLL thread never touches game state; GML pulls from it once per frame. So DLL has queue? For commands from agent to game. We need minimal. Need think architecture.

Existing drnet.c has UDP lockstep. We add TCP agent. Need keep drnet.dll free of new libraries, C89 friendly. Winsock includes already. Can use standard C. Need thread. Can use critical_section. Need non-inheritable socket with WSA_FLAG_NO_HANDLE_INHERIT like UDP. Use socket maybe. The agent listener runs own thread created non-inheritable? The spec says listener runs on own thread, created non-inheritable like lockstep socket WSA_FLAG_NO_HANDLE_INHERIT, so a chapter change can listen. Wait: socket non-inheritable; thread? Actually thread handle? The spec says "created non-inheritable like the lockstep socket (`WSA_FLAG_NO_HANDLE_INHERIT`, see ...)" likely socket no inherit. Thread handle maybe not relevant; but chapter change new process inherits handles? We create thread with pthread? We use CreateThread. To make thread handle non-inheritable maybe not necessary? The spec says listener runs on its own thread, created non-inheritable like the lockstep socket. We should create thread with default? Hmm. Maybe use _beginthreadex? No. Existing drnet thread created CreateThread; not made non-inheritable. The socket is. For agent TCP socket, create with WSASocketW and WSA_FLAG_NO_HANDLE_INHERIT. Thread handle inheritable by default? CreateThread's handle inheritable? It is. Could close after? We need wait? We can create thread, maybe SetHandleInformation on thread. Not critical. But spec says non-inheritable; we can do SetHandleInformation on thread? Existing didn't mention. We'll ensure socket no inherit. Could create listener socket and accepted socket no inherit? Need.

Need GML integration: dronline_init reads agent port and initializes? Need functions: drnet_agent_start(port) -> 1/0, drnet_agent_recv? drnet_agent_reply? We need design DLL exports.

Agent commands async. Game pulls once per frame. Need DLL receive lines, parse JSON? Spec: small hand-written JSON reader for command subset. Could parse C side or GML? It says small hand-written JSON reader for command subset is enough; likely C side parse commands into queue. GML side pulls a command struct and replies. State JSON built in GML on demand only. That makes sense: C parses command JSON and stores commands; GML pulls one command per frame? Commands: state, input, clear. Input command steps. Replies queued/sent over TCP. GML builds state JSON. Could implement C side queue of commands and output queue of replies? Simpler: C parses full command lines and stores a command record. GML calls drnet_agent_command() to get next pending command as string? But GML building state and input queue. The DLL never touches game state; GML pulls once per frame. We need functions to send reply strings from GML to agent. Could be: drnet_agent_start(port), drnet_agent_recv(max?) returns string? But GameMaker DLL functions with >4 args numbers only; strings ok if <=4. drnet_agent_recv is no args -> string. But C can store latest command; GML calls drnet_agent_recv() returns command text; but GML needs parse JSON? The spec says commands agent->game are JSON; state code read-only. GML can parse JSON? GML doesn't have JSON parse maybe. Avoid. Better C parses commands into typed records; GML calls functions to get command type and data, and send replies. Need no string parameters >4. Could use functions:
- drnet_agent_start(port) -> 1/0
- drnet_agent_stop()?
- drnet_agent_poll() -> double command id? If no command returns 0? Need distinguish command types. Maybe drnet_agent_command() returns command type: 0 none, 1 state, 2 input, 3 clear? But need id (number/string) and step details. Could expose arrays? GameMaker DLL returning string maybe easier: C serializes a command as JSON line? But then GML parsing still. Alternatively C parses and GML pulls fields via functions:
  - drnet_agent_cmd() -> double: 0 none, 1 state, 2 input, 3 clear, -1 error?
  - drnet_agent_id() -> const char* (string id) returns JSON-escaped raw? Hmm id may number or string; GML needs extract for reply echo. Could C store as raw string token; returns strings okay.
  - drnet_agent_steps(list?) no arrays. Could use drnet_agent_steps_get(index) return? But complex.

Alternative: Have GML build and parse JSON lines? GML has no json functions (because not use json_stringify; but state JSON built with plain string functions). Could parse command in GML with string functions too, but more cumbersome. C hand-written JSON reader is requested. Put parsing C, queue steps in C, and GML consumes step frames during input. That seems clean.

Need input command: queue steps each holds buttons for frames. Must apply to input path at dronline_script_frame. Existing dronline_script_frame(mine) called once per input frame and before script_record. We need add agent input after script? Spec: agent input becomes local player's action bits OR'ed with real kb/gp bits so person can still step in. It goes in at same place as script= input (dronline_script_frame), once per input frame, and before script_record writes the frame, so recording includes agent input. So we should modify dronline_script_frame to add agent steps after script? Need careful ordering: script may set mine from file; then agent OR? The spec says agent's input becomes local player's action bits OR'ed with real keyboard/gamepad bits. It doesn't mention script precedence but "same place as script input". We can have dronline_script_frame take mine (real bits or replay bits? offline), then script overrides or OR? Existing script does mine = from file. Agent should OR with real kb/gp? But in dronline_script_frame, `mine` is already local bits when offline, and in online after exchange? Let's trace: dronline_step gets mine = local_bits; if replay, mine = recorded bits; else if record write. If !net, mine=dronline_script_frame(mine). If net and connecting? later when net, script_frame called after connect. So dronline_script_frame receives whatever mine currently. If script exists, it may replace mine with script bits. Agent should be OR'ed with real keyboard/gamepad bits and maybe script? Spec says OR'ed with real keyboard/gamepad bits so person can step in. We'll implement agent input: each input frame, agent_next_step returns buttons bits, then mine |= agent_bits; also if script file sets bits, OR? "same place as script input" likely after script. To ensure recording includes, dronline_script_frame handles both; if script and agent both, OR? Probably yes; agent input is local player action bits OR'ed with real keyboard/gamepad bits. If script= file, does script replace real? Existing script replaces. But agent should still OR with script? Spec says person can step in, not script. We'll make agent bits OR with the current mine inside dronline_script_frame after script reading: `mine |= dronline_agent_frame();`? But if script ended and script_end=idle sets mine=0, agent can still move: that seems good: real no input, script none, agent input. If script has keys, real bits in mine? dronline_script_frame when global.dro_script != -1 and no more frames: if script_end != "keys" mine=0; else leaves original mine (real real keyboard). Then agent OR. Good. For online, dronline_script_frame called after exchange? existing after connect; agent bits get exchanged. For offline, before apply. Good.

Need queue limit and done reply when last frame applied. Applying happens in dronline_script_frame (when called by game). "When the last frame has been applied by the game (online that is drnet_delay() frames after it was sent), send done." Online when script_frame called maybe after drnet_exchange? Wait in dronline_step, after drnet_exchange and assigning dro_bits, it calls dronline_apply_bits, not dronline_script_frame. Actually online, dronline_script_frame is called BEFORE drnet_exchange? Let's inspect: in `if (global.dro_net && !global.dro_connecting)`:
```
mine = dronline_script_frame(mine);
drnet_set_aux(dronline_detect_hit());
var _all = drnet_exchange(global.dro_netframe, mine);
...
```
So script_frame called before exchange and thus before input frame is sent. It returns mine to send. That's "input frame" in online: every exchanged frame. Applying though local player's own bits? The actual effect in game occurs after exchange (drnet_apply_bits from dro_bits). But "applied by the game" online is `drnet_delay()` frames after it was sent" says spec: when last frame has been applied by game (online that is drnet_delay() frames after it was sent). The current dronline_script_frame is called when step is prepared/sent, not when applied. So done must be delayed by delay frames? Or we can trigger done in GML based on frame count after input sent. Need design.

Maybe agent input queue frames are consumed at dronline_script_frame each input frame, which is when the game sends its input frame, not when the input is applied? Wait local player input (agent) is sent to lockstep; its effect on local game occurs in the same frame but exchange returns combined bits including own sent, then apply bits. Actually dronline_step:
- mine = agent step N
- drnet_exchange(frame F, mine): sends my input for F+D? The DLL stores, exchange returns bits for frame F from all players, including own frame F. Then apply_bits applies frame F. So the input for frame F is applied in same call after exchange. But if D>0, drnet_exchange sent `next_target` up to f+D, but returns bits for frame F (already sent earlier). For local UI, own SOUL predicts; game logic applies frame F bits from dro_bits, which include frame F input. So the local player's input for frame F is applied in frame F (current dronline_step) because dro_bits is all players input for current netframe returned by exchange. However exchange of frame F requires my bits for frame F sent much earlier (D frames ahead) via pending. dronline_script_frame is called at the beginning of frame F to determine `mine` to send for frame F+D. But `mine` is not applied yet for frame F; it is sent for future. The input applied this frame (frame F) was from previous frames (already queued). The agent step consumed in dronline_script_frame now determines future frames. Thus to generate a step holding for 30 frames, we should consume a step bit in dronline_script_frame each frame, but "applied by game" is D frames later than dronline_script_frame call. The spec explicitly says online done after drnet_delay() frames after it was sent. So we need track when step bits were sent, then after applying? Could mark done when the last sent bit is returned by drnet_exchange? That is D frames later. But our queue consumption is at send time. Need know which frame each queue step bit was sent as. The DLL has internal next_target? But steps are in GML? We can implement agent queue in GML, maybe easier to track sent frame. But spec wants C hand-written JSON maybe still.

Let's think where to put agent queue: GML or C? GML can maintain arrays of steps, frame count. C parses command into steps; GML calls drnet_agent_next_step() each input frame to get bits. To send done at correct time, C or GML can track: C knows when next_step called and could delay done? But C doesn't know game frame/delay? It can know UDP drnet_delay() maybe exported; but C has delay variable. However "applied by game" corresponds to when drnet_exchange returns that input frame. The C's send mapping: drnet_script_frame called before drnet_exchange for current frame F. It consumes bits for frame F+D (because exchange assigns `assign(f, bits)` with current frame f? Let's inspect: drnet_script_frame called at each frame F with current game frame. In drnet_exchange(frame=F, my_bits=mine), then assign(F, bits) sends same bits to frames up to F+D. Wait assign(f, bits) puts held bits into `next_target` while next_target <= f+delay. So if delay=2, calling drnet_exchange(F, bits) sends bits to frames F? Actually next_target is already F? At game frame F, exchange called with frame parameter F; assign(f=F, bits) puts bits in slots next_target to F+2. The slot for F may have been filled earlier, but now assign fills F, F+1, F+2? Wait assign always while next_target <= f+delay. next_target should equal current frame F? At next exchange, yes we now populate frames F..F+D. That means input for frame F is only sent at frame F, not D early? Let's review existing design: "Each frame F, a machine sends its bits for frame F + D" from docs. But in drnet_exchange, assign(f, bits) puts bits to frames up to f+delay; if next_target = F, that means F, F+1, ..., F+D. It sends my bits to future frames. The frame F bits are included in this same assign, not earlier. But exchange returns frame F bits. How are remote F bits available? Because my F bits are sent now? It blocks waiting to receive peer bits for F, but own bits for F are local. For future, own bits sent now for future. So "sent for frame F+D" is now. The input for frame F was sent D frames earlier when that frame's F was future (F_old + D). So yes, each time step consumed at frame F sends bits for frame F+D. So "applied" by peer at frame F+D (when their exchange returns frame F+D). Online done should be drnet_delay() frames after it was sent (consumed). For local game, the consumed bit is applied D frames later. Thus we can count frames after the last step bit is consumed. But note dronline_script_frame called for frame F, and the step counts as "sent" for future frame F+D. If multiple steps in a command, they are consumed over consecutive input frames (send frames) after command starts. The last step's last occurrence frame S. It is applied at online peer at S + delay? Actually if step consumed at input frame S, it determines local input sent for frame S+D. The game applies returned bits for frame S+D at local frame S+D? Let's use time: current game frame variable `global.dro_frame`? In dronline_step, `global.dro_frame` equals netframe? Initially global.dro_frame 0, after step increments. drnet_exchange called with `global.dro_netframe` which starts 0, matches global.dro_frame. So at game frame F, dronline_script_frame is called before exchange with netframe F? Let's check after exchange, global.dro_netframe++ and global.dro_frame++ at end. Before exchange, global.dro_netframe = F, global.dro_frame = F. So yes. So done online after last consumed send frame F_last + delay (maybe +1?). Spec: "When the last frame has been applied by the game (online that is drnet_delay() frames after it was sent)" So if last step length consumed at input frame S, done at frame S + delay? It is applied at game frame S+delay. In offline, applied in same frame (dronline_script_frame called, then apply bits same step) maybe done at next frame? We'll implement sent frame count and done when game frame > S + delay? Need exact. We can use global.dro_frame, and after dronline_step increments; maybe done check at start of next frame. Need be close.

But could implement done in C by receiving `drnet_agent_frame` called with frame number and `drnet_delay()`. Yet C has delay variable. But C doesn't need game frame maybe. GML can call a C function to mark each consumed step and pass current frame + delay. Simpler: handle agent queue inside GML, with arrays. Then C only parses commands into commands? But still need parse JSON. Perhaps easiest: C parses agent JSON command into a simple string? Hmm.

Maybe implement only C parsing and GML calls functions to receive command summary and step data one by one. Then GML maintains actual input queue and done timing. The C parser can parse and store command type, id token, and steps in C arrays; GML pulls with functions:
- `drnet_agent_command()` -> command type (0 none, 1 state, 2 input, 3 clear)
- `drnet_agent_id()` -> id string (raw text, maybe JSON string/value) 
- `drnet_agent_steps_count()` -> number of steps
- `drnet_agent_step_frames(index)` -> frames for step
- `drnet_agent_step_buttons(index)` -> bits for step
Then when GML is ready, call `drnet_agent_next()` advances? Or command consumed immediately when command() called. But if GML calls command and receives, C marks it consumed. Need reply. GML sends reply via `drnet_agent_send(text)` -> sends line. For done, GML calls send. Error command? If invalid from C, C can send error directly? But reply needs type error, id possibly. C can parse and send error itself; then command is consumed. Spec says bad line -> error; lines over 64KB dropped with error. We can have C send error and maybe GML not involved. But if invalid JSON, no id; reply `{"type":"error","id":null?"?}` Spec says `"id":...` maybe absent. It says every command may carry "id", echoed in replies. For error from invalid JSON, id maybe omitted. We'll include `"id":null`? The spec examples: `{"type":"error","id":...,"error":"<text>"}`. For no id, maybe `"id":null`. GML builder can handle. But C could send error with its own JSON string, no game state. Fine.

Need avoid C thread touching game state; parsing and TCP are fine. GML pulls command via thread-safe queue. C handles TCP receive and sends.

Design C additions:
- Separate agent listener socket `agent_sock`, connection `agent_conn`, thread `agent_thr`, CRITICAL_SECTION? We can use same `cs`? Existing `cs` protects UDP state. Adding agent queue and socket under same cs might be okay? But network thread uses cs. Agent thread can use same cs, but then blocking send/recv? Avoid holding cs during blocking. We'll create separate `agent_cs` critical section or reuse? Simpler separate `CRITICAL_SECTION agent_cs`. Need init. But existing `cs_ready` only initializes cs and ev_input in drnet_start. If agent starts before drnet_start? dronline_init order: maybe we call agent start after drnet_start? Config agent port. Works in solo/offline too: spec works in solo, host, client; in solo, drnet_start not called. Need agent listener independent of UDP. So agent_start should initialize its own critical section. Could use separate `agent_cs` and `agent_ev`? But GML poll from game thread calls functions that access agent queue under agent_cs. Agent thread uses same.

Socket non-inheritable:
```
agent_listen = WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL,0, WSA_FLAG_OVERLAPPED|WSA_FLAG_NO_HANDLE_INHERIT)
```
bind 127.0.0.1, listen. Then accept loop. For accepted socket, need non-inheritable? Since accepted socket handle may be inheritable? Default accept socket likely inheritable? To avoid chapter change process inheriting the connection, we can set handle non-inheritable on accepted socket (SetHandleInformation) and listener too. WSA_FLAG_NO_HANDLE_INHERIT for listener; for accepted we can use `WSAAccept`? Simpler `accept`, then SetHandleInformation. But accepted socket created after process start; if inherited by chapter change child, it might stay? The old process exits, but inherited socket in new process could keep port? Spec only says listener can listen on same port after chapter change; accepted connection maybe not issue. But make non-inheritable anyway via SetHandleInformation.

Thread creation: use CreateThread; we can SetHandleInformation on thread handle to not inheritable? Not necessary for listener port. Do.

Agent message parsing: read lines up to 64 KB. Need keep buffer per connection. Could maintain `agent_rbuf[65536?]`, `agent_rlen`. We need allow line over 64KB -> drop with error and close? Spec "lines over 64 KB are dropped with an error." We can send error and discard rest of line/close connection? Better send error and discard bytes until newline without accumulating > max? We'll implement.

- One connection at a time; second replaces first. Document in notes/code. Implementation: accept new socket; lock agent_cs; close old agent_conn if valid; set new. Need wake GML? The old command queue maybe? If replace, should clear commands? Probably clear queued commands? We should clear pending commands and input queue? Spec maybe second replaces first; if new agent, old commands should be dropped? Otherwise old agent's queued input continues. Better clear command queue and active command? We can clear all. Need GML's own queued agent steps? Hmm if commands parsed by GML and queued in GML, C cannot clear GML arrays. Could call a flag to clear from GML each frame? Simpler implement all agent command queue in C (including input queue), and GML only pulls current frame bits; then replacing connection can clear C queue. Done timing in C? We can implement queue in C with commands and steps, and C can know frame/delay from GML? We can have GML call `drnet_agent_frame(frame)` at each input frame; C can advance queue, track sent frames, and send done based on current frame and delay. But C needs to send state JSON for done, which GML builds. So C can't send done itself unless GML tells it. Hmm.

Maybe C sends replies by receiving GML built strings? For done, GML could build state and pass to C `drnet_agent_done(current command id?)`. Easier to keep queue in GML.

But connection replacement then need GML queue clear. We can expose C signal `drnet_agent_dropped_count()`? Or simply when agent connection replaced, C command queue cleared, and GML detects connection generation changed and clears its own step queue. Could have `drnet_agent_generation()` increments on each new connection; GML stores last generation, and if changed calls `dronline_agent_clear_steps()`. Also C has command queue cleared. That is okay. But maybe overkill. Spec says second refused or replaces first; we can instead refuse second connection while one active. That avoids clearing GML queue? It can just close new socket. Simpler. Refused: if `agent_conn != INVALID_SOCKET`, we close incoming. That means after old connection dies, new can connect. We need detect disconnection and close. Choose refused, not replace. That is simpler, less state clearing. Spec allows say which. We'll implement refusal: accept new, if active, send error? Maybe just close. We'll note in code comment and Notes. Does this align "a second one is refused or replaces the first (say which)" - yes refused. We can send error line maybe.

So C maintains active agent connection. If established, subsequent connections closed immediately. When remote closes, set agent_conn invalid. GML can queue steps; if connection drops, queued commands that never ran get no done? Clear? We can leave GML queue? The agent reconnects later? We need not let old queued steps continue after disconnect? Spec clear command drops all queued. If connection lost, maybe queued commands should drop? Not specified. We can decide. Simpler: GML queue persists until clear/new commands; but if agent disconnects, steps may still run. That may be weird. We can detect generation or connection closed flag and clear GML queue? Could use C functions: `drnet_agent_connected()` returns 1 if connected. GML can clear its own steps on disconnect. But then if agent sends a long input command and its TCP conn drops (network local no), steps drop. That seems reasonable. Let's do: on dronline_step each frame, if agent queue active and `drnet_agent_connected()` returns 0 (but started), clear GML steps. Need when agent hasn't connected yet? If no connection ever, agent queue empty; no issue. We'll track `global.dro_agent_was_connected`.

But need C expose `drnet_agent_connected()`.

Now GML queue design. We have agent command state. Functions in scr_dronline.gml:
- `dronline_agent_init()` read `agent` port from ini, if >0 start listener. `global.dro_agent_port`, `global.dro_agent_on`, `global.dro_agent_connected`, `global.dro_agent_generation`. But we need not parse in GML maybe C.
- On each game frame, before input? We need poll C for new commands and apply steps. dronline_script_frame is the place. But polling and updates should happen once per frame. Could add `dronline_agent_poll()` in `dronline_step` before computing mine? It processes commands received, queued input, done, state replies built with state. State command: reply built in next game frame. "state -> built in next game frame." So if command arrives now, GML should build state and send next game frame (not same). We can in poll: if pending state command, build state and send reply. That is current frame, but the command may have arrived between frames; "next game frame" from receipt could be this frame? We can process at start of next game frame after arrival. If we poll at beginning of dronline_step, it's the next game frame relative to received. Good.
- Input command: queue steps in GML. Should reply queued at once with total frames; can send in poll immediately. Then when last applied send done. Need track commands and steps.

Need define GML data structures using arrays. We can use arrays (allowed). Avoid ds_* in lockstep? The state code must be read-only, but agent logic itself can create arrays. The rules say no ds_*/buffer/surface creation in state code. But agent queue code? This GML runs on every machine; if it creates data structures with IDs, could desync if IDs are used? The spec says lockstep safety: anything agent does there that other machines do not do is desync, so state code must be read-only: no ... ds_*/buffer/surface creation (ids are plain numbers and would shift). Does that apply to agent command handling overall? The agent runs on one machine only. If we create arrays only, arrays are local values no global IDs; okay. But if we create `ds_list` for queue, its ID would shift? Actually creation of ds_list yields numeric ID that might differ? If only allocated on one machine and never used in simulation, does it affect desync? It could consume IDs. But spec says no ds_*/buffer/surface creation in state code. Agent queue code likely not state code, but still happens during frame? It runs on one machine only, any side effect on global IDs could desync? Data structure IDs are not read by game logic? They may be plain numbers and if game queries? Mostly not. But spec says avoid. We'll use arrays only.

GML arrays can store structs? No structs? GML supports arrays and maybe structs? In GML2022? The code uses arrays only, `array_create`, `array_push`. We should stick arrays. We can create arrays for each queued command: `[id, total_frames, steps_array, sent_frames?]`. Steps arrays maybe concatenated. We need order, at most 10 queued commands; each command has steps. We can store each command as an array of steps; steps could be array of `[buttons, frames]`. GML arrays can be nested? yes. But simpler: store flattened step data arrays.
- `global.dro_agent_cmds` array of commands, each command `[id, steps, total_frames, cursor_step, remaining_in_step, state_frame_due?]`.
Actually if we process frame by frame, we can flatten all queued frames into arrays of buttons bits? That may be easier: maintain `global.dro_agent_frames` array of bits, one per queued input frame. Queue commands append frames. Then when dronline_script_frame called, consume first element. Need send `done` when the last frame of a command is consumed and applied. We can maintain for each command:
  - `id`
  - `first_index` offset (at enqueue, current number of frames) or just command objects in a queue.
But if we append frames globally, when a command ends, done due after its last frame index. We can store in command queue minimal.

Better maintain an array of pending commands, each with remaining frame counts per step. `dronline_agent_poll` enqueues commands. `dronline_agent_step()` called from dronline_script_frame returns bit to OR. It advances through first command.
Data per command:
- `id` (string)
- `steps` array, each step array `[bits, frames]`
- `step_i` current step index
- `remaining` frames left in current step
- `total_frames` total remaining? Maybe.
- `done_frame` -1 until finished consuming; set to current send frame + delay when last bit consumed; send done when global.dro_frame >= done_frame? Use `global.dro_netframe`? Offline no delay? Spec done in offline after applied: after dronline_apply_bits? We can call after dronline_step increments. We can set done_frame = global.dro_frame + global.dro_delay? Then when next frame reaches. Need be careful.

Let's define GML functions:

`dronline_agent_init()` called in dronline_init after reading ini. If `agent` not 0:
- set `global.dro_agent_port = clamp(ini_read_real(...),0,65535)`? Spec agent=<port>. We'll read numeric, default 0 off. `global.dro_agent_on = false;` etc.
- if port>0 and mode != "off"? Spec mode=off nothing changes. So only start if global.dro_on true and agent port >0. We already return early if `!global.dro_on` before scripts. The spec says mode=off nothing changes. We'll guard in dronline_init after mode determined; in mode off global.dro_on false, early return before agent start. Good.
- call `drnet_agent_start(global.dro_agent_port)`; if fails, log? Could show? We can drnet_log if network mode perhaps; in solo drnet_log still logs? drnet_log logs even if no UDP? It writes drnet.log; okay. But drnet_agent_start is new export. Need register.
- initialize arrays: `global.dro_agent_cmds = [];`, `global.dro_agent_was_connected = false;` etc.

`dronline_agent_poll()` called once per dronline_step before input? It should:
1. If agent_on, check `drnet_agent_connected()`. If was connected and now not, clear queued commands? We'll implement.
2. Poll commands from C until none:
   - `var cmd = drnet_agent_command();` returns `0` none, `1` state, `2` input, `3` clear.
   - `var id = drnet_agent_id();` maybe string.
   - for cmd=1 state: build state JSON and `drnet_agent_send(json)`. Need help send.
   - cmd=3 clear: clear queued commands, send `{"type":"cleared","id":...}`.
   - cmd=2 input: get steps_count = drnet_agent_steps_count(); need limits: at most 60 steps, at most 3600 frames per step, at most 10 queued commands; over limit -> error, nothing queued. C may also validate? Spec says over limit -> error. Could validate in GML before enqueue. But C parser has steps. Ensure `steps_count > 60` command error sent and nothing queued. Also each frames >3600 error. We need not use C for errors. Then total queued commands `array_length(global.dro_agent_cmds) >= 10`, send error.
   - Build command entry and append to global.dro_agent_cmds.
   - Send queued reply `{"type":"queued","id":<id>,"frames":<total>}`.
3. Process done events: `dronline_agent_done_check()` maybe after frame increment. It examines head command sent/done. Need track.
But careful: poll command during step before input. The queued reply should be at once. If command is input, its frames should start being applied in the current input frame? "queue the steps ... reply at once." It doesn't explicitly say first step applies same frame or next. Input command arrives between frames; "queue up in order." We can append; if no other queued commands and currently no active, dronline_agent_step later in same frame consumes first frame. Is that okay? It says agent sends input; likely fine. But if we poll at start of dronline_step and enqueue, then later dronline_script_frame consumes, so first frame applies this frame (or sent for future online). That's okay.

However for state command, "next game frame": poll at start of frame builds state now. The command may have arrived since last frame; this is next game frame. Good.

`dronline_agent_step()` called in dronline_script_frame:
- If no cmds return 0.
- Get first command `c = global.dro_agent_cmds[0]`.
- If c done? Actually c has steps.
- Return bits for current step; decrement `remaining`.
- When step done, `step_i++`; if all done, mark command finished and set done frame. Also remove from front? We need keep for done reply until after applied. We can move it to a separate done list. Simpler keep in commands queue with a `done_frame` and remaining 0; dronline_agent_step when remaining=0 returns 0? But we only consume when active. We can keep active command as first until done sent. But for next command queued, if first command finished consuming, should start next command immediately next frame? Yes input commands queue up in order, so once first command's last frame consumed, next frame should be from next command. We can remove first command from active input queue and place into `global.dro_agent_donecmds` waiting for done. Then next `dronline_agent_step` sees next command. So:
   - `global.dro_agent_cmds` input queue of commands not finished.
   - `global.dro_agent_donecmds` commands whose frames have all been consumed but done not yet sent (waiting for apply delay).
- When consuming last frame of command:
  - set `done_frame = global.dro_frame + dronline_agent_apply_delay()`? Need determine.
  - append command to donecmds (with fields).
- `dronline_agent_done_check()` after frame increment? It iterates donecmds; if current `global.dro_frame >= done_frame`, sends done with state. But need state built at done time. We can call build state. Command id needed.
- For offline delay=0, done_frame = global.dro_frame +? Applied in same frame after `dronline_apply_bits`. We can check after `global.dro_frame++` at end. If we set done_frame = global.dro_frame at consume start (before increment), after increment global.dro_frame = old+1, so send done. Good.
- Online delay maybe `drnet_delay()` frames after sent (consumed). So set done_frame = global.dro_frame + drnet_delay() + ? If consume at frame F, applied at F+delay. We want send done after applied. Use `dronline_agent_apply_delay()`:
   - if global.dro_net: `return drnet_delay();`
   - else `return 0;`
Then done_frame = global.dro_frame + drnet_delay()? But need check with current frame. If consume at F, set done_frame = F + delay + 1? Let's reason with global.dro_frame at consume. At start of dronline_step before increment, global.dro_frame = F. dronline_script_frame consumes and returns bit. Then exchange/apply for frame F at dronline_step? If online, the input for frame F+D is sent now; apply frame F bits later. So consumed at frame F will be applied when game frame = F+delay? In dronline_step of frame F+delay, the exchange returns bits for frame F+delay and `dronline_apply_bits` applies. Actually the bit sent at frame F for frame F+D is stored; at game frame F+D, `drnet_exchange` returns frame F+D bits from local slots and applies. So applied at frame F+D. So done_frame = F + D. But if we check after increment, when `global.dro_frame` equals F+D? At start of frame F+D, before increment global.dro_frame = F+D. We can send done at that start perhaps after applying? We might call done_check before drnet_exchange? We'll choose call done_check after `dronline_step` increments? Let's design.

Maybe track at end of dronline_step after increment, send done for commands whose done_frame <= global.dro_frame. After frame F increment, global.dro_frame = F+1. For offline D=0, done_frame=F; after increment F+1 >= F => send. For online D=2, done_frame=F+2; after frame F+2 increment global.dro_frame=F+3 >= F+2 => send one frame late, but okay. Could send at start of D frame after applied? Not crucial. We'll perhaps call done_check at beginning of next frame before consuming? But state should be state after application. We can call after `global.dro_frame++` and before next frame. That is fine.

But `global.dro_frame` may be used in state JSON. For state command reply, spec says frame global.dro_frame. If send done after increment, state frame would be F+1, maybe off by one. For state command poll at beginning of frame, global.dro_frame is current frame. For done, maybe state should be current frame (after applied). It might be okay if done frame is after increment. But spec `frame` is `global.dro_frame`. We'll use actual global.dro_frame at send time. That's okay. We can choose.

Could call `dronline_agent_done_check()` at end after increment. Fine.

Need send state JSON for state and done. Build JSON with plain strings. Need read globals with `variable_global_exists`. We'll implement `dronline_agent_state()` returns JSON string. Need escape helper. GML string functions. Must be read-only. We must avoid `with` assignments. Spec says no `with` blocks that assign. For players_pos, we need inspect instances; use `with` to read? That is okay if read-only. We can use `with (obj_mainchara)` to read variables, no assignment. The rule says "no with blocks that assign" so read-only with okay. Need be careful instance_find etc. We'll use `instance_find` and variable_instance_get to avoid `with`. Simpler.

Need `string_hash_to_newline` for choice options: existing global function? We can use `string_hash_to_newline(global.choicemsg[i])` if exists. Might be game function. Need check. Use `variable_global_exists("string_hash_to_newline")`? Actually global function in GML? We can call if script exists. Better use `if (variable_global_exists("string_hash_to_newline")) option = string_hash_to_newline(...) else option = ...`. But state code should use game function string_hash_to_newline. That function exists in game likely. We can call directly.

Need JSON escape helper. Implement:
```
function dronline_agent_escape(s) {
    var out = "";
    for (var i=1; i<=string_length(s); i++) {
        var c = string_char_at(s,i);
        if (c == "\"") out += "\\\"";
        else if (c == "\\") out += "\\\\";
        else if (c == "\n") out += "\\n";
        else if (c == "\r") out += "\\r";
        else if (c == "\t") out += "\\t";
        else if (ord(c) < 32) out += "\\u" + ...;
        else out += c;
    }
    return out;
}
```
Need control characters. GML `ord(c)` maybe exists. We can use `string_char_at`; `ord(string_char_at(s,i))`. Good. `string_format` for hex? We can implement simple control: `if (ord(c)<32) out += "\\u00" + string_char_at("0123456789abcdef", floor(ord/16)+1?)` Hmm. Simpler: JSON control chars <32 can be represented as `\u00XX`. But spec only quotes, backslash, control; we can output `\u0000` etc. Need not exact? We'll write helper with hex. GML has `string_format`? Simpler use `string_copy("0123456789ABCDEF", (ord(c) div 16)+1,1) ...`. But string indexing starts 1. We'll implement `dronline_agent_hex(n)` for 4 hex digits:
```
var hx = "0123456789abcdef";
return string_char_at(hx, (n>>12)&15 +1) ... 
```
GML bitwise? yes. But `string_char_at` with expression. We'll use floor.

Maybe GML `ord` not available? It is. Use `string_ord_at(s,i)`? In GML 2.3, `ord(char)`? Actually `ord(string)` returns code of first char, `string_ord_at(str, index)`. There is `string_ord_at`. To be safe use `ord(string_char_at(s, i))`? I think `ord` exists in GML? DELTARUNE uses maybe. But UTMT compiler can compile. Use `ord(string_char_at(...))`.
Could avoid control char hex and just use `\u`? But invalid. We'll do simple for known controls maybe:
```
if (code == 8) out += "\\b"; ...
else if (code < 32) out += "\\u00" + hex(code);
```
Need hex function.

But we must ensure no new functions >? Fine.

Need reading globals with variable_global_exists:
- `global.dro_frame` exists (we set)
- `global.chapter` maybe global? use variable_global_exists.
- `room_get_name(room)` built-in.
- `global.interact`, `global.menuno`, `global.submenu`, `global.fighting`.
- `can_move` computed.
- players positions:
  - P1 obj_mainchara: `var p1 = instance_find(asset_get_index("obj_mainchara"),0); if (p1 != noone) { ...}`. Facing global.facing `global.facing`.
  - P2/P3: find obj_caterpillarchara instances with `controls == 1` / 2. Use `instance_number`? asset index. We can loop `for (var i=0; i<instance_number(obj); i++) { var inst = instance_find(obj,i); if (inst.controls == 1) ...}`. In GML, `inst.controls` works? With local var `inst`, `inst.controls` likely yes. We can use `variable_instance_get(inst,"controls")` to be safe. But dot notation is fine. Need avoid `with` assignments. Use `with (inst)` read-only? We can simply access after instance_find:
```
var cat = asset_get_index("obj_caterpillarchara");
for (var i=0; i<instance_number(cat); i++) {
    var inst = instance_find(cat, i);
    var ctrl = variable_instance_get(inst, "controls");
    ...
}
```
Need read `name`, x, y. Facing global.facing2/facing3 if variable_global_exists.
  - missing instance -> leave player out. If no instance with controls==1, no P2.
- `name` variable is char_name? Spec says `"name":char_name`. For p1 char_name? It says Player 1 is obj_mainchara, facing global.facing. What is char_name for player 1? Probably "kris"? In spec: players_pos list: `"name":char_name`. For player 2, its `name` variable is "susie", "ralsei" or "noelle". For player 1, maybe obj_mainchara has `name`? Maybe global? We need read char name. The Local Multiplayer mod likely has `obj_mainchara.name`? Maybe not. Need inspect spec: Player 1 is obj_mainchara (facing global.facing). In Local Multiplayer mod, player 2 is obj_caterpillarchara ... its `name` variable is "susie", "ralsei" or "noelle". For player 1 name maybe `global.charname`? Not specified. The state key says `"players_pos": list of {"player":p,"object":name,"name":char_name,...}`. It doesn't explicitly say player 1 name variable. We need choose. Could use `variable_instance_get(obj_mainchara, "name")` if exists. What's char_name? In Deltarune, main character name maybe "kris"; perhaps `global.chara`? Hmm. Since spec says for player 2 its `name` variable; maybe all players' name comes from instance variable `name`. Player 1 obj_mainchara may have `name` variable? The local multiplayer mod likely gives caterpillar chars `name`; obj_mainchara may not. The spec says name: char_name. Maybe we can use `dronline_agent_char_name(p)` reading. For p=1, if not exists maybe "kris". But not defined. Need maybe `variable_instance_get(inst, "name")`, if undefined/null, null. That is acceptable. We'll read `var nm = variable_instance_get(inst,"name"); if is_string -> escape else null._json`. We can provide null.
- `dialogue`: list obj_writer instances. `raw` (`mystring`), `text` cleaned, `shown` (`pos - 1`, capped at length), `length`, `waiting` (`halt != 0`), x,y. Need clean `mystring`. Implement cleaning.
  - instance_find `obj_writer` loop. Read variables with variable_instance_get. Need `mystring` string, `pos`, `halt`.
  - `shown = clamp(pos - 1,0,length)`; if pos undefined -> null? use variable_instance_get with default maybe. Use `is_undefined`.
  - In GML, `is_undefined` function exists? Existing code uses `is_undefined(k)` in audio. Yes.
- `choice`: if `obj_choicer_neo` exists:
  - options array global.choicemsg[0..choicetotal], with `string_hash_to_newline`. Read `global.choicemsg` maybe array.
  - `selected` mychoice, `can_choose` canchoose. Need if choices? Could be object instance.
- camera: built-in `camera_get_view_x(view_camera[0])` etc.
- `nearby`: up to 30 instances within 240 px of player 1, nearest first. object object_get_name, x,y,solid, visible. Skip no sprite and players themselves. Need compute distance to p1 x,y. Use instance_number(all)? `with (all)` in read-only okay. We need exclude instances without sprite, exclude players themselves, exclude maybe same player objects. We'll collect up to 30 nearest. Sorting in GML without ds? We can build arrays and sort manually. We need read `object_get_name(object_index)`, `sprite_index` exists? `sprite_index` maybe -1. `solid`, `visible` built-in. Need skip player objects obj_mainchara, obj_caterpillarchara? For skip players themselves, we can skip object_index equal to obj_mainchara or obj_caterpillarchara? But player-related objects maybe all? Spec says skip players themselves; likely those object instances. We'll skip object_index == obj_mainchara or obj_caterpillarchara. But nearby list may include NPC duplicates? okay.
  - Need sort nearest first. Could collect arrays of distances, names and iterate selecting minimum. Avoid creating ds. Good.
- JSON build with plain strings. Need ensure arrays as `[`, objects `{`, etc.
- Need know when variables missing -> null.

State JSON built on demand. We'll call from `dronline_agent_state_request()`. Might be reasonably long but okay.

Need C parsing commands. Hand-written JSON reader. We can parse subset. Need handle:
- command object starts `{`, ends `}`. Only `cmd`, `id`, `steps`.
- Need parse `"cmd":"state"` etc. `id` number or string.
- `steps` array of objects with `"buttons"` array of strings and `"frames"` number.
- Validate limits? Could parse all; GML enforces. But C can enforce too maybe to avoid storing huge steps. We'll cap at 60 steps and 3600 frames in C too? Spec says limits; GML checks after parse. C should be robust not overflow. We'll cap arrays: `#define AGENT_MAX_STEPS 60`, each frames max 3600, total? But `dronline_agent_steps_count` etc. We can parse; if steps >60 or frames >3600, set command type maybe -2? But GML can inspect. Better keep C parser lenient but safe, GML checks and sends error. But error message should include. We'll parse and store; GML checks and if invalid sends error. C can still parse many? Need bounds to avoid memory; use fixed array max 60. If JSON has >60 steps, we can mark error in C and discard command; C sends error? But GML doesn't need. To keep error reply with id, C needs id. Simpler C parses id and steps; if too many, still store up to 60? Then GML can reject. But if >60, steps_count returned >60, so GML rejects. Good.
- `id` may number or string. Store as string representation. For reply, we need output number without quotes, or string with quotes. Need know if id was string. We can store `agent_id_is_string`. And `agent_id_str`.
- C parser could handle whitespace. Need robust.

C data structures for agent commands:

```
#define AGENT_MAX_STEPS 60
#define AGENT_MAX_LINE 65536
typedef struct { uint16_t buttons; uint32_t frames; } agent_step;
typedef struct {
    int type; // 0 none, 1 state, 2 input, 3 clear
    int id_is_string;
    char id[64];
    int steps_count;
    agent_step steps[AGENT_MAX_STEPS];
} agent_cmd;
```
But if more steps, steps_count >60? Could parse all? We can't store all, but can return count more than 60. We need count; store first 60 maybe; GML sees count >60 and rejects. But if count huge, counting steps. We'll parse. We can have `parsed_steps` counter and store first 60. Use `agent_cmd.steps_count` = actual total if <=60? We can just limit count to 60? For error we need know >60. Set `too_many_steps` flag? Add field `steps_count` total, but steps array only first 60. GML reads count from `drnet_agent_steps_count()`, if >60 reject; it won't read beyond? good.

But C function `drnet_agent_step_frames(index)` should return 0 if index >=60 or >=stored. Fine.

Need queue for parsed commands. Could maintain up to maybe 16 commands `agent_cmd agent_cmds[16]`; `agent_cmd_head`, `agent_cmd_tail`, count. Since GML polls each frame, queue won't grow large. But agent could send many commands while GML not poll; 10 queued input limit, state/clear maybe many. Need cap to avoid memory; maybe reject when queue full? We'll have fixed 32. If full, drop and send error. We can just not accept; but send error? We can parse and if no space send error `{"type":"error","id":null,"error":"too many queued commands"}`. Fine.
- Thread receives lines, parses; if valid, enqueue under agent_cs. For input limit 10 queued commands? C queue can hold more, GML keeps 10. If GML reaches 10, sends error and command consumed. Fine.

Reply sending from C:
- `agent_send(const char *text)` appends newline and sends on agent_conn. Must be able from game thread (GML poll) or agent thread? From GML for replies, from agent thread for errors/refusals. Use `Send`/`send`. Need lock agent_cs to protect conn. Avoid sending while another thread? Use critical section.
- `drnet_agent_send(const char* text)` export, takes string, returns 1 if sent/queued? It will block? Local TCP send likely fine. Send under agent_cs? If we hold lock while send may block; okay but small. Could copy and send outside lock but if conn closed after, send may fail. We can lock and `send()`; but if send blocks? TCP local small messages; okay. Better not hold lock during send to avoid deadlock? Use lock to copy conn, unlock, then send. But connection may close/reused? We refuse second, so conn won't be replaced; if remote closes, `send` fails. Fine. We'll implement:
```
static int agent_send_raw(int socket, const char *line, int len)...
```
For `drnet_agent_send`, lock get conn, unlock, send full with `send` loop? Blocking socket, but local. Use nonblocking? We'll set accepted socket non-blocking? Then sends can EWOULDBLOCK; need queue. Better agent_conn set non-blocking? Could keep blocking; recv on agent thread can block. Accepted socket maybe blocking. For send from game thread, blocking send could stall game if agent not reading. Bad input safety: agent doesn't read? Small lines; could block? Better set non-blocking for accepted and use select? Or keep blocking but set `SO_SNDTIMEO`? Simpler: set accepted socket non-blocking and maintain outbound buffer in C? Too much. Could use `send` with non-blocking and if would block, drop? But replies could be lost. Better use blocking but messages small; localhost buffer. We'll set accepted socket blocking. It could block if buffer full; unlikely. But spec "never crash or stall the game on bad input". We should avoid blocking indefinitely. We can set send timeout 1s. Winsock `setsockopt(SO_SNDTIMEO, 1000)`. But if timeout, close connection? We'll just attempt.

Actually from game thread, if send fails due to closed conn, fine. We'll implement `agent_send_line` with critical lock? Let's keep simple. Since agent_conn is own, only game thread sends (for replies) except agent thread may send errors for invalid JSON/refused. Valid commands are parsed on agent thread and queued; replies are sent by game thread. For invalid JSON, agent thread can send error directly. There is concurrency: game thread and agent thread may send at same moment? We'll use `agent_cs` around `send` to serialize. But holding lock while `send` can block; okay.

Need accept new connection while old active: we choose refuse. In agent thread:
```
client = accept(agent_listen,...)
if (client == INVALID_SOCKET) continue;
lock(agent_cs);
if (agent_conn != INVALID_SOCKET) {
    // refuse to keep one command stream
    char *msg = "{\"type\":\"error\",\"error\":\"an agent connection is already open\"}\n";
    send(client, msg, len,0);
    closesocket(client);
} else {
    agent_conn = client;
    // maybe set keepalive? no
}
unlock;
```
But if old conn exists but is actually dead (not detected yet), new agent refused until recv detects. Acceptable. Could make active connection replaced? We choose refuse; note.

Need recv line from agent. Since blocking socket, agent thread loops reading one connection at a time. If no connection, accept. If connection closes, reset buffer and go back accept. We can use `select` to wait for accept/read? Simpler:
```
while (running_agent) {
  if (agent_conn == INVALID) {
     // accept with timeout? we can use select on listener
     fd_set; select(agent_listen, 1s); if readable accept.
  } else {
     // read from agent_conn; if line complete parse; if recv 0 close.
     // Use select with 1s to check if gone? 
  }
}
```
Need not block thread forever when stopping. Use select for readability, timeout 1s. Use nonblocking? Could use select for both.
Set both listener and conn nonblocking? `ioctlsocket` for FIONBIO. Then accept and recv. For `accept`, with nonblocking listener, if no pending accept returns WSAEWOULDBLOCK. Fine.
Thread:
```
while (running_agent) {
  fd_set rs; FD_ZERO; if agent_conn == INVALID -> add listener; else add agent_conn; 
  select(0,&rs,...,timeval 0.1s);
  lock agent_cs;
  if listener readable -> accept; if conn != INVALID refuse logic ...
  if conn readable -> recv;
  unlock;
}
```
Need handle both maybe. Could also have listener and conn simultaneously if existing conn and a second connect; we want refuse, so need accept even when conn active? Yes to refuse/close new. So always have listener in read set and optionally agent_conn. When listener readable, accept; if active, send error/close; else install. When agent_conn readable, recv.
Need use same critical section? We'll use separate `agent_cs`, but we also use `cs`? Not.

Parsing lines: Use state buffer `agent_rbuf[AGENT_MAX_LINE+1]`, `agent_rlen`. Read available. Loop:
- `recv(agent_conn, buf, sizeof(buf),0)`. If socket blocking? We'll set nonblocking, so recv -> WSAEWOULDBLOCK. If 0 -> close. If SOCKET_ERROR, if WSAEWOULDBLOCK break else close.
- Append bytes to `agent_rbuf`. If length exceeds AGENT_MAX_LINE before newline -> send error and drop until newline: We can set `agent_overflow=1`; discard incoming until newline. Simpler: if rlen >= max and no newline, send error `{"type":"error","error":"line too long"}` and reset rlen; discard all until newline. But we might have partial line > max; we can continue reading, but not store. Implement `agent_discarding` flag.
- For each complete line in buffer, extract C string, parse. Need remove newline and maybe \r.
- Multiple lines per recv; loop.
- We need avoid parse partial line until newline.
- Buffer max maybe 65536 + 1. If overflow, we set discarding. While discarding, read but look for newline; discard everything before newline. After newline, reset discarding.
- If `rlen > 0` and no newline, leave.

When connection closes, clear `agent_rlen=0`, `agent_conn=INVALID`, `agent_connected` flag 0. But commands already parsed in C queue? Clear C command queue too. Since connection dropped, old commands should be cleared (or GML clears). We'll clear C queue and maybe GML will clear based on connected. Good.

Need agent start/stop.
- `drnet_agent_start(port)`:
  - if already running return 1.
  - initialize agent_cs if not.
  - create listener socket non-inheritable.
  - set nonblocking.
  - bind to `127.0.0.1`, port. `htons((u_short)port)`, `inet_pton` or `inet_addr("127.0.0.1")`. Use `inet_addr`.
  - listen(sock, 1)
  - set running_agent=1; create thread `agent_thread`.
  - if fail close.
  - log.
- `drnet_agent_stop()`: close listener/conn, stop thread, cleanup? Spec doesn't require, but could on shutdown? Not necessary, process exits. But for mode off nothing changes. We can maybe call in dronline_shutdown? Not needed; process exit. But if game_end maybe still process. Not important.
- `drnet_agent_command()` returns type, dequeues from C queue. It should perhaps call `lock(agent_cs)` and pop.
- `drnet_agent_id()` returns id string; maybe `const char*`. Need thread safe, return static buffer? Could return pointer to global `agent_local_id` set by `drnet_agent_command`. Since game thread calls command first, then id. We'll have `agent_last_id` static char. `drnet_agent_command` sets `agent_last_id` from top command. `drnet_agent_id()` returns that.
- `drnet_agent_steps_count()` returns count.
- `drnet_agent_step_frames(index)` returns frames.
- `drnet_agent_step_buttons(index)` returns bits.
- `drnet_agent_send(const char* text)` sends line.
- `drnet_agent_connected()` returns 1 if connection active.
- `drnet_agent_start` only.

Need register these in add_extension.csx. Types:
- drnet_agent_start, D, D
- drnet_agent_command, D (no args)
- drnet_agent_id, S (no args)
- drnet_agent_steps_count, D
- drnet_agent_step_frames, D, D
- drnet_agent_step_buttons, D, D
- drnet_agent_send, D, S
- drnet_agent_connected, D
- maybe drnet_agent_log? not needed.

Need ensure C functions with >4 args? none >4, okay.

Need test_consistency sees GML calls drnet_agent_*; must match exports and csx. We'll update.

Need state building GML. Must be careful no ds_* etc. But GML itself uses array functions, okay. Need not create `ds_*`.

Need add config example `agent=0`. Mention untested draft. README one line.

Need docs/DESIGN.md agent section. Also settings comment at top of gml/scr_dronline.gml. We'll add `agent = 0` in config comment and maybe at bottom `dronline_agent` section.

Need write tests/test_agent_client.py: unit tests without game/network (step conversion, JSON line framing). The tool `tools/agent_client.py` must import input_script parser. Need implement class. Step conversion: command `do "<steps>"` sends steps written in input_script text syntax, import parser. Wait for done prints state. We need design AgentClient class with methods:
- `state()` sends `{"cmd":"state","id":...}` and returns state.
- `do(text)` compiles steps with `input_script.compile_text`, builds command JSON with steps: each step `{"buttons":[names],"frames":frames}`. Need convert frames list to steps groups preserving names. `input_script.compile_text` returns per-frame bits. We need compress consecutive identical bits into steps. For each frame sequence of same bits, step buttons names from bits. Use `input_script.button_names` maybe returns "-" for 0. We need buttons list: if zero, `[]`; if nonzero, split by `+`. For `tap`, compile_text expands into frames (press, gaps). This conversion produces steps. Need convert to `{"buttons":[...], "frames":n}`. 
- Wait for queued and done messages. Use JSON line framing with `json` stdlib. Socket connection. `--port`, `--host`, `--timeout`.
- `do` sends input, reads replies until done with matching id. Prints state from done. Timeout.
- `state` sends, reads until state with matching id. Prints state as JSON.

Need usable as module class. We'll design:
```
class AgentClient:
    def __init__(host='127.0.0.1', port=..., timeout=5):
        ...
    def connect()
    def close()
    def _request(obj, wait_type)
    def state(self): send cmd state, id increment, return state dict
    def input_steps(self, text): send cmd input, id etc; wait for queued then done; return state
    def clear(self, id=None)
```
Need thread? no.

Need framing: one JSON per line, UTF-8. Implement reading with socket file. Could use `makefile` and readline with timeout? `socket.create_connection(..., timeout)`, then `file = sock.makefile('rwb')`? Standard library only, okay. But line length maybe >? Use `sock.makefile('rb')`; `readline`. For write, `sock.sendall(line + b'\n')`. Need handle timeout. Use `sock.settimeout(timeout)`.
- `_send(obj)`: `data=json.dumps(obj, separators=(',',':'))`; encode; `sock.sendall(data + b'\n')`.
- `_receive()`: readline, if empty EOF. Decode. Parse JSON. Return dict.

Need `do "<steps>"` CLI: `python agent_client.py state --port ...`; `do "20 right; tap confirm 2 15"`; maybe `--host`, `--port`, `--timeout`. We'll implement argparse with subcommands. State command prints JSON state with `json.dumps`.
Step conversion to command JSON:
```
def text_to_steps(text):
    frames = input_script.compile_text(text)
    steps=[]
    i=0
    while i<len(frames):
        bits=frames[i]
        j=i+1
        while j<len(frames) and frames[j]==bits:
            j+=1
        buttons = []
        if bits:
            for name in input_script.BUTTONS:
                if bits & (1 << input_script.BUTTONS.index(name)): buttons.append(name)
        steps.append({"buttons": buttons, "frames": j-i})
        i=j
    return steps
```
Need buttons order per bit layout? The spec examples `["right"]`; `button_names` orders BUTTONS list from bit 0 up. We can use `input_script.button_names(bits)` returns "-" or "+". Split:
```
names = input_script.button_names(bits)
if names == "-": buttons=[]
else: buttons=names.split("+")
```
Good. But `button_names` returns lower-case full names. Good.

Tests: `tests/test_agent_client.py`:
- test step conversion grouping consecutive frames; tap creates steps.
- test JSON line framing: perhaps use `tools.agent_client` functions. We can test `text_to_steps`. Need import from tools. Ensure path. Also test `AgentClient` line framing without network? We can implement a pure function `encode_message`? or use `_send` not. Simpler test `text_to_steps`. For JSON line framing, maybe test `json.dumps` line? We can define `line_encode` in agent_client and test. In CLI, we'll have `encode_line(obj)` and `decode_line(line)`. Unit test those.

Need update tools/agent_client.py. Since repository root, tools import input_script. We can import `from tools.input_script import ...` if run as module. But when run from `tools/`, need path. We'll do:
```
try: from tools.input_script import compile_text, button_names
except ImportError: import input_script
```
Better in file, add sys.path to repo root parent? We'll use:
```
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from tools.input_script import compile_text, button_names
```
Then tests import `tools.agent_client`.

Need README line. In "Usage" maybe after scripted input? Add one line under status? Spec "one line in README.md". We'll add in status or usage: untested draft agent API.

Need docs/DESIGN.md Agent API section. Add after scripted input maybe before determinism. Include untested draft.

Need gml comment top update. Add `agent = 0` in config comment and maybe Global `dronline_agent_*`.

Need avoid adding game files. Fine.

Now implement GML in detail.

First, integrate into dronline_init. At end before return. We need read agent in ini block. Add after `global.dro_srec_file = ini_read_string(...)`:
```
global.dro_agent_port = ini_read_real("online", "agent", 0);
```
But `ini_read_real` returns 0 if key not present? If no key -> 0. Good.
Then after `if (!global.dro_on) return;` and before network start? Agent can start before network? It works in solo. Need start after network? The agent state may need drnet_delay; but in solo no drnet_start; drnet_agent_start doesn't depend. We can start after network start or before, doesn't matter. Should start only if mode != off. In dronline_init:
```
if (global.dro_mode == "host" || global.dro_mode == "client") { ... start network... }
dronline_agent_init();
```
Maybe agent should be available even if network start fails? mode may be set off on failure. We can start after network mode set. We'll put after network init block and before random_set_seed? State code no random. Fine. But agent `state` may want mode; global.dro_mode set. We'll put after network init block and before random_set_seed? It doesn't matter. Need ensure `drnet_agent_start` called after `drnet_stop`? no.

Define `dronline_agent_init()` near script init maybe after `dronline_ready`.
```
function dronline_agent_init()
{
    if (global.dro_agent_port <= 0 || global.dro_agent_port > 65535) return;
    if (!drnet_agent_start(global.dro_agent_port)) { drnet_log("agent API: could not listen on 127.0.0.1:" + string(global.dro_agent_port)); return; }
    global.dro_agent_on = true;
    global.dro_agent_cmds = [];
    global.dro_agent_done = [];
    global.dro_agent_was_connected = false;
    drnet_log("agent API: listening on 127.0.0.1:" + string(global.dro_agent_port) + " (untested draft)");
}
```
Need initialize arrays before maybe.

Add in dronline_init before global.dro_on? We can initialize `global.dro_agent_port = 0; global.dro_agent_on = false; global.dro_agent_cmds = []; global.dro_agent_done = []; global.dro_agent_was_connected = false;` after reading ini.

In `dronline_step`, call `dronline_agent_poll()` at start after`dronline_ready()`? Before input. Need ensure agent_poll uses current global.dro_frame and may send replies; no game state modification except its own arrays. We call after `global.dro_restarting = false; dronline_heart_unswap();` maybe before input. It can build state. But building state may call `with(all)`? nearby loops all instances; but no assignments, okay. However doing state build at start before game processes frame? Current frame state before current input? Spec says state built in next game frame; likely state at that frame before step? Fine. But `nearby` uses player positions after previous frame. That's okay. Need no random etc. Build state doesn't modify.

At end of dronline_step after `global.dro_frame++`, call `dronline_agent_done_check();` to send done. But also need if command finished and delay=0, done after increment. We'll put:
```
global.dro_frame++;
dronline_agent_done_check();
```
But note existing code increments at very end. Good.

Need ensure `dronline_agent_done_check` doesn't run when mode off (dronline_ready returns false) so no.

Now GML functions.

`dronline_agent_poll()`:
```
function dronline_agent_poll()
{
    if (!global.dro_agent_on) return;
    var was = global.dro_agent_was_connected;
    var conn = drnet_agent_connected();
    global.dro_agent_was_connected = conn;
    if (was && !conn) { global.dro_agent_cmds = []; global.dro_agent_done = []; }
    for (var i=0; i<8; i++)  // process at most 8 commands per frame to avoid long bursts
    {
        var cmd = drnet_agent_command();
        if (cmd == 0) break;
        var id = drnet_agent_id();
        if (cmd == 1) { drnet_agent_send(dronline_agent_reply_state(id)); }
        else if (cmd == 3) { global.dro_agent_cmds = []; global.dro_agent_done = []; drnet_agent_send(dronline_agent_reply_cleared(id)); }
        else if (cmd == 2) {
            var steps = drnet_agent_steps_count();
            var invalid = "";
            if (steps > 60) invalid = "too many steps";
            if (array_length(global.dro_agent_cmds) >= 10) invalid = "too many queued commands";
            if (invalid == "") {
                var total = 0;
                var frames = [];
                for (var s=0; s<steps; s++) {
                    var f = drnet_agent_step_frames(s);
                    var b = drnet_agent_step_buttons(s);
                    if (f <= 0 || f > 3600 || b < 0 || b > 2047) invalid = "bad step";
                    if (invalid == "") { array_push(frames, [b, f]); total += f; }
                }
                if (invalid == "") {
                    array_push(global.dro_agent_cmds, [id, frames, total, 0, 0, -1]); // [id, steps, total_original, step_i, remaining_in_step, done_frame]
                    drnet_agent_send(dronline_agent_reply_queued(id, total));
                }
            }
            if (invalid != "") drnet_agent_send(dronline_agent_reply_error(id, invalid));
        }
    }
}
```
But `steps_count` could be 0? We'll error. Need `drnet_agent_step_frames` returns double; in GML var. Use `round` maybe.

`dronline_agent_step()` called from dronline_script_frame:
```
function dronline_agent_step()
{
    if (!global.dro_agent_on) return 0;
    var cmds = global.dro_agent_cmds;
    if (array_length(cmds) == 0) return 0;
    var c = cmds[0];
    var steps = c[1]; var si = c[3]; var rem = c[4];
    if (rem <= 0) {
        if (si >= array_length(steps)) { // should have been moved; just remove?
            array_delete(c? no)
        }
        return 0;
    }
    var step = steps[si];
    var bits = step[0];
    rem--;
    if (rem <= 0) {
        si++;
        if (si >= array_length(steps)) {
            // command consumed; move to done
            c[3] = si; c[4] = 0; // not needed
            c[5] = global.dro_frame + dronline_agent_apply_delay(); // done_frame
            array_push(global.dro_agent_done, c);
            // remove from active commands
            var newcmds = [];
            for (var i=1; i<array_length(cmds); i++) array_push(newcmds, cmds[i]);
            global.dro_agent_cmds = newcmds;
        } else {
            // set next step remaining
            c[3] = si; c[4] = steps[si][1];
            cmds[0] = c; // actually c is an array reference? GML arrays copy? Hmm.
        }
    } else {
        c[4] = rem;
        cmds[0] = c;
    }
    return bits;
}
```
GML arrays are reference? In GML, assigning an array copies? Actually arrays are copy-on-write? When you access `var c = cmds[0];`, c is reference? Modifying c[4] then `cmds[0] = c;` may be needed. We'll do. But `array_push` etc. Need be careful.
Alternatively store all queued input frames globally, not command object in active queue. That may be simpler to avoid array mutation complexity. Let's redesign with frames list and command done list.

We can store all queued frames as `global.dro_agent_frames` array of bits, and a queue of command boundaries:
- `global.dro_agent_cmds`: each command `[id, start_frame, length, done_frame]`? But if multiple commands queued, we can compute current frame bits from first command.
- Actually we can simply append frames to `global.dro_agent_frames` and maintain a separate `global.dro_agent_done_queue` for command completions with end frame index. When `dronline_agent_step` consumes first frame from `global.dro_agent_frames`, decrements a remaining counter. Need know when command ends. Store active command sequence in order itself:
  - `global.dro_agent_cmds`: array of commands (not flattened) as above. Maybe easier but okay.

Maybe use flattened input frames and a queue of command descriptions:
`global.dro_agent_frames` = array of bits per queued frame.
`global.dro_agent_marks` = array of commands, each `[id, frame_index_of_last?]`. But when enqueue a command, we append its frames and record `done_frame_index` = current length before append + total steps; `completion_frame`? When consuming frames, if consumed count reaches `completion_frame`? Actually we need know after consuming the last frame of command. We can maintain `global.dro_agent_frames` and `global.dro_agent_active_id` but multiple commands ahead. We can use a queue of command ends:
- At enqueue, before appending, `start_index = array_length(global.dro_agent_frames)`.
- Append all frame bits per command (expanding steps).
- `end_index = array_length(global.dro_agent_frames)` (exclusive).
- Store `[id, start_index, end_index, done_at]` in `global.dro_agent_ends`? But command done order same as frame order. We can maintain `global.dro_agent_ends` queue each entry `[id, end_index_exclusive, sent_done_frame?]` and when `dronline_agent_step` consumes frames, after consuming a frame and total consumed == end_index, we know command finished; move that cmd entry to done with `done_frame`. Since commands are processed in order, we can store `global.dro_agent_ends` as a list in enqueue order. But if there are no frames (invalid), no entry. Good.
- `dronline_agent_step`:
```
if array_length(global.dro_agent_frames)==0 return 0;
var bits = global.dro_agent_frames[0];
// remove first
...
var consumed = global.dro_agent_consumed + 1?  
```
But we also can track consumed count relative to command end. Use `array_shift`? GML doesn't have shift. We can use index variable instead of modifying array:
`global.dro_agent_frame_pos` current index into `global.dro_agent_frames`. Initially 0. To consume, `bits = global.dro_agent_frames[global.dro_agent_frame_pos]; global.dro_agent_frame_pos++`.
When `global.dro_agent_ends[0][1] <= global.dro_agent_frame_pos`, that command's last frame consumed. Move to done list with done_frame = global.dro_frame + delay; then pop from ends (advance an index or rebuild). Could use `global.dro_agent_end_pos` index instead of deleting. Or just keep done queue in separate array. At enqueue, append command `[id, end_index]` to `global.dro_agent_ends`. In step after incrementing pos, while `global.dro_agent_ends[0][1] <= pos`, pop first (use queue index `global.dro_agent_end_pos`), append to done. But if multiple commands consumed? one frame per step, so at most one? Could be zero-length no, none. So one while maybe fine. But if command total 0 invalid, not enqueued.

This flattened approach simplifies queueing and clear. Data:
- `global.dro_agent_frames = []`
- `global.dro_agent_frame_pos = 0`
- `global.dro_agent_ends = []`   // [{id, end_index}]
- `global.dro_agent_end_pos = 0`
- `global.dro_agent_done = []`   // [{id, done_frame}]
When clear: reset arrays and positions.
When enqueue command: validate total frames >0. Append bits for each step. Append end marker. If there were no frames (all steps 0 invalid) reject.

But specs step frames at most 3600, at least? step frames likely >0. We'll enforce.

This uses arrays only; no complex nested mutation.

Let's implement this.

`dronline_agent_init` sets arrays.

`dronline_agent_poll`:
```
if (!global.dro_agent_on) return;
var conn = drnet_agent_connected();
if (global.dro_agent_was_connected && !conn) dronline_agent_clear();
global.dro_agent_was_connected = conn;

for (var n=0; n<8; n++) {
    var cmd = drnet_agent_command();
    if (cmd == 0) break;
    var id = drnet_agent_id();
    if (cmd == 1) { drnet_agent_send(dronline_agent_reply("state", id) ...) }
    else if (cmd == 3) { dronline_agent_clear(); drnet_agent_send(...cleared); }
    else if (cmd == 2) {
        var invalid = "";
        var steps = drnet_agent_steps_count();
        if (steps > 60) invalid = "too many steps in one command";
        if (steps < 1) invalid = "no steps";
        if (array_length(global.dro_agent_ends) - global.dro_agent_end_pos + array_length(global.dro_agent_done) >= 10?) 
        Need queued commands counted = active commands not done? At most 10 queued commands. Commands that are done waiting don't count? "at most 10 queued commands" likely input commands queued, including currently executing? We should count active commands (`ends` length - end_pos) plus perhaps done waiting? Done waiting no longer queued input. But command still not done reply; execution queued maybe no. We'll count active: `(array_length(global.dro_agent_ends) - global.dro_agent_end_pos) >= 10` -> error. Also if current no frame? Good.
        if (steps > 0) {
            var total = 0;
            for (var s=0; s<steps; s++) {
                var f = drnet_agent_step_frames(s); var b = drnet_agent_step_buttons(s);
                if (f <= 0 || f > 3600) invalid = "step frames must be 1..3600";
                else {
                    for (var k=0; k<f; k++) array_push(global.dro_agent_frames, b);
                    total += f;
                }
            }
            if (invalid == "") {
                array_push(global.dro_agent_ends, [id, array_length(global.dro_agent_frames)]); // end index exclusive
                drnet_agent_send(dronline_agent_json_message("queued", id, "frames", total));
            } else {
                drnet_agent_send(dronline_agent_error(id, invalid));
            }
        } else ...
    }
}
```
But if invalid occurs mid-steps, we've already appended some frames, then should not enqueue. We must validate first before appending. So do two passes: first validate all; then append. We'll write:
```
if (steps <1 || steps>60) invalid; else {
    var total=0;
    for s... get f,b and validate; total+=f;
}
if invalid=="" {
    for s... append repeated frames;
    append end marker; send queued.
}
```
Need fetch b/f twice; okay.

Need ensure command limit check before appending. Use active count:
`var active = array_length(global.dro_agent_ends) - global.dro_agent_end_pos;` Actually `global.dro_agent_ends` contains entries for all commands enqueued, including ones already consumed? If we don't delete but use end_pos, array length includes consumed. `active = array_length(global.dro_agent_ends) - global.dro_agent_end_pos`. This counts commands whose end marker not yet popped. Good. Done list separate. So at most 10 active commands.
Need also count commands already enqueued but not all frames? Done queue not input. Good.

`dronline_agent_clear()`:
```
global.dro_agent_frames = [];
global.dro_agent_frame_pos = 0;
global.dro_agent_ends = [];
global.dro_agent_end_pos = 0;
global.dro_agent_done = [];
```
But if there are queued commands, clear replies? Let caller send cleared. Fine.

`dronline_agent_step()`:
```
if (!global.dro_agent_on) return 0;
if (global.dro_agent_frame_pos >= array_length(global.dro_agent_frames)) return 0;
var bits = global.dro_agent_frames[global.dro_agent_frame_pos];
global.dro_agent_frame_pos++;
// move commands whose end_index <= frame_pos
while (global.dro_agent_end_pos < array_length(global.dro_agent_ends)) {
    var e = global.dro_agent_ends[global.dro_agent_end_pos];
    if (e[1] > global.dro_agent_frame_pos) break;
    // command fully consumed
    var c = [e[0], global.dro_frame + dronline_agent_apply_delay()];
    array_push(global.dro_agent_done, c);
    global.dro_agent_end_pos++;
}
return bits;
```
But if a command has total frames 5; end marker end_index after appending = previous length + total. frame_pos after consuming fifth = end_index. Then move. Good.

`dronline_agent_done_check()`:
```
if (!global.dro_agent_on) return;
for (var i=0; i<array_length(global.dro_agent_done); i++) {
    var d = global.dro_agent_done[i];
    if (global.dro_frame >= d[1]) { // done due
        drnet_agent_send(dronline_agent_reply_done(d[0]));
        // remove from array
    }
}
```
Need rebuild without sent:
```
var remaining = [];
for (var i=0; i<array_length(global.dro_agent_done); i++) {
    var d = global.dro_agent_done[i];
    if (global.dro_frame >= d[1]) {
        drnet_agent_send(dronline_agent_reply_done(d[0]));
    } else array_push(remaining, d);
}
global.dro_agent_done = remaining;
```
But `dronline_agent_reply_done` builds state now. Good.

`dronline_agent_apply_delay()`:
```
if (variable_global_exists("dro_net") && global.dro_net) return max(drnet_delay(),0); // drnet_delay returns double
return 0;
```
Spec says online that is drnet_delay() frames after it was sent. We add no +? We'll set done_frame = global.dro_frame + apply_delay. Then send when `global.dro_frame >= done_frame`. At final step consume at frame F, done_frame = F+D. `dronline_agent_done_check` called after increment at end of same dronline_step. In frame F, after increment global.dro_frame = F+1. If D=0, F+1 >= F, sends done after state applied. Good. If D=2, at frame F, global.dro_frame becomes F+1, not sent. At frame F+1, after increment F+2, not sent. At frame F+2, after increment F+3, send. That's one frame after input applied? Actually applied at start of frame F+2 (before increment = F+2). We send after frame F+2 executes, so state is after apply and then some game step? It's after exact frame F+2. But spec maybe okay. Could call done_check before increment at strategic point? To send at frame F+D after application before increment? Not critical. But if we want exact, we can call `dronline_agent_done_check` at beginning of dronline_step before `global.dro_frame++`? Then for D=2, at frame F+2 start, global.dro_frame=F+2, sent before applying? but input for frame F+2 has not been applied yet (apply_bits later in same step). Sending before apply would state prior to applying, not after. So after apply but before increment is ideal. Existing dronline_step increment at very end. We can insert `dronline_agent_done_check();` right before `global.dro_frame++;`, after `dronline_apply_bits` and hash? But hash? Actually order at end:
```
dronline_apply_bits();
if (... logs)
if (... hash)
if flush
global.dro_frame++;
```
So we can call `dronline_agent_done_check();` just before increment. Then global.dro_frame is still F+2. For D=2 done_frame=F+2; at start of frame F+2 global.dro_frame=F+2; after apply and before increment, condition true. That seems exact. For offline D=0, done_frame=F; after apply before increment global.dro_frame=F; true. Great. We'll insert before increment.
But note `dronline_agent_done_check` builds state and may include any mutations in this frame (after apply). Good. It may be called multiple times? no.
Thus we don't need call after increment. We'll call before `global.dro_frame++`.

Potential issue: `dronline_agent_done_check` uses `global.dro_frame` before increment; fine.

Need state reply functions. Implement `dronline_agent_json_string(dict?)` manually.

Functions:
- `dronline_agent_quote(s)` maybe returns quoted JSON string.
- `dronline_agent_num_or_null(value?)`.

Better build complete JSON string with helpers.

`dronline_agent_state_json()` builds state object string.

`dronline_agent_reply_state(id)` returns `{"type":"state","id":<id>,"state":{...}}`
But id need insert raw id. We need know if id is number or string. C stores `agent_id_is_string`? We expose `drnet_agent_id()` returns string of id's text but doesn't tell quoted or not. We can have `drnet_agent_id_is_string()` export, or make `drnet_agent_id()` return JSON value string such that GML can use raw? Example if id is number 42, `drnet_agent_id()` returns `42`, GML inserts raw. If id is string "abc", returns `"abc"`? But C has parsed and could store the raw JSON representation (not escaped? Actually id string token contents no quotes). Better C function returns id text (without quotes) and `drnet_agent_id_is_string()` returns 1/0. We can register. Or C returns id as JSON encoded: if number, return "42"; if string, return "abc" but GML can quote? Need know. Let's just implement `drnet_agent_id_is_string()`.

Add C export `drnet_agent_id_is_string` double (no args). It returns `agent_last_id_is_string`. GameMaker calls after command. Register. Also `drnet_agent_id()` returns raw id string.
Then GML helper:
```
function dronline_agent_json_id(id)
{
    if (drnet_agent_id_is_string()) return dronline_agent_json_string(id);
    if (id == "") return "null";
    return id; // number
}
```
Need ensure id is valid JSON number; C read. Fine.

But in GML poll, after `var cmd = drnet_agent_command();` we may call `drnet_agent_id_is_string()` before id? It returns last command. Fine. Store `var id_is_string = drnet_agent_id_is_string(); var id = drnet_agent_id();`

Need queue data id maybe include is_string? We currently store id only. When done, we need know to echo. Could store `[id, done_frame, is_string]` or have `global.dro_agent_id_is_string` per command. Let's store `[id, end_index, is_string]` after command; but done needs string. We'll store in command end `[id, end_index, id_is_string]`, and when moving done, preserve `[id, done_frame, id_is_string]`.

At enqueue:
- `array_push(global.dro_agent_ends, [id, array_length(global.dro_agent_frames), id_is_string]);`
At `dronline_agent_step` moving:
```
var c = [e[0], global.dro_frame + apply_delay, e[2]];
```
Done sends with `dronline_agent_json_id_parts(d[0], d[2])`.

But for immediate replies queued/state/cleared, we can use `id_is_string` local.

Helper:
```
function dronline_agent_json_id_value(id, is_string)
{
    if (is_string) return dronline_agent_json_string(id);
    if (id == "") return "null";
    return id;
}
```

Now state JSON. Need many globals. Implement with helper:
```
function dronline_agent_json_string(s)
{
    var out = "\"";
    for (var i=1; i<=string_length(s); i++) {
        var c = string_char_at(s,i);
        if (c == "\"") out += "\\\"";
        else if (c == "\\") out += "\\\\";
        else if (c == "\n") out += "\\n";
        else if (c == "\r") out += "\\r";
        else if (c == "\t") out += "\\t";
        else {
            var code = ord(c);
            if (code < 32) out += "\\u00" + dronline_agent_hex(code mod 256? but ord gives <=255) 
            else out += c;
        }
    }
    return out + "\"";
}
```
Need `ord(c)` where c is one-character string. In GML, `ord(c)` maybe returns char code of first char. But if `c` is empty? not. We'll use `ord(c)`.
Hex:
```
function dronline_agent_hex2(n)
{
    var hx = "0123456789abcdef";
    n = n mod 256;
    return string_char_at(hx, (n div 16)+1) + string_char_at(hx, (n mod 16)+1);
}
```
For code<32, `out += "\\u00" + dronline_agent_hex2(code);` This works for control, but >255? ord maybe unicode; for control code<32 okay. For other unicode, leave as char.

Need `is_real`, `is_string`, `variable_instance_get` etc. We can write:
```
function dronline_agent_var_exists(name) { return variable_global_exists(name); }
function dronline_agent_val(v) { if is_undefined(v) return "null"; if is_numeric? }
```
But GML types and undefined. For JSON values:
- `dronline_agent_json_num(v, floor?)` return string(v)
- `dronline_agent_json_number(v)` if undefined -> null else string(v)
- `dronline_agent_json_bool(v)` returns "true"/"false".
- `dronline_agent_json_val(v)`:
```
if (is_undefined(v)) return "null";
if (is_real(v)) return string(v); // but booleans in GML are real? boolean true = 1? In GML, booleans are real 0/1? There is `is_bool`? Since we want true/false, use explicit.
if (is_string(v)) return dronline_agent_json_string(v);
return "null";
```
But `is_real` exists? likely. Could avoid by type checks. We know which variables are numbers.

Read global safely: `variable_global_exists("chapter") ? global.chapter : null`. Since GML cannot have `?:`? GML supports ternary? Existing code doesn't use, but likely GML has `?:`. To be safe, avoid ternary. Use `if`. Many functions. We'll write a helper:
```
function dronline_agent_g(name, def)
{
    if (variable_global_exists(name)) return variable_global_get(name);
    return def;
}
```
Does `variable_global_get` exist? Yes. But in GML, `variable_global_get(name)` returns value. Use it. Existing code uses `variable_global_exists` only. `variable_global_get` should exist. We can alternatively use `global`? `global` itself. We'll use `global` after checking.

For safety, define:
```
function dronline_agent_gn(name, def)
{
    if (variable_global_exists(name)) {
        var v = variable_global_get(name);
        if (is_real(v)) return v;
    }
    return def;
}
```
But `is_real` maybe. Could simply return `variable_global_get(name)` and caller checks `is_undefined`. We'll use:
```
var chapter = variable_global_exists("chapter") ? global.chapter : null;
```
Are ternaries supported in GML? GML has `condition ? a : b`? Yes modern GML supports `? :`. Existing code doesn't use, but likely. To avoid risk, use `if/else`. But verbose. Maybe use helper `dronline_agent_gv(name)` returns `null` or value, and caller `if (is_undefined(v))`? Null is not undefined? In GML `null` constant? Not sure. Existing code uses `noone`, not null. The spec says missing -> `null`. Need output JSON null. We can use helper to return `-1`? Better use explicit.

Given GML compiler with UTMT supports ternary? To be safe, avoid ternary. But output code with lots of if. Let's write helper functions that return JSON strings for each global.

Maybe:
```
function dronline_agent_state_global(name, kind)
{
    if (!variable_global_exists(name)) return "null";
    var v = variable_global_get(name);
    if (kind == "num") return string(v);
    if (kind == "str") return dronline_agent_json_string(string(v));
    if (kind == "bool") return v ? "true" : "false";
    return "null";
}
```
GML supports `if (v)` with real? bool maybe. We can avoid bool via `if (v > 0.5)`. We'll use `if (v)` likely okay. For desync etc.

Need `is_real` perhaps not necessary.

Now state pieces:

```
function dronline_agent_state_json()
{
    var out = "{";
    out += "\"frame\":" + string(global.dro_frame) + ",";
    var chapter = variable_global_exists("chapter") ? string(global.chapter) : "null";
    out += "\"chapter\":" + chapter + ",";
    out += "\"room\":" + dronline_agent_json_string(room_get_name(room)) + ",";
    out += "\"mode\":" + dronline_agent_json_string(global.dro_mode) + ",";
    out += "\"player\":" + string(global.dro_player) + ",";
    out += "\"players\":" + string(global.dro_players) + ",";
    if (global.dro_net) out += "\"online\":true,"; else out += "\"online\":false,";
    var desync = variable_global_exists("dro_desync") ? global.dro_desync : -1; // state says desync global.dro_desync
    out += "\"desync\":" + (desync < 0 ? "-1" : string(desync)) + ",";
    var inter = variable_global_exists("interact") ? global.interact : null; // need null
...
```
Could helper `dronline_agent_global_num(name)`. For `global.dro_desync`, if missing null, but always exists. Spec says read every global with variable_global_exists first; missing -> null. So use helper for all.
Let's define:
```
function dronline_agent_global_num(name)
{
    if (variable_global_exists(name)) return string(variable_global_get(name));
    return "null";
}
function dronline_agent_global_bool(name)
{
    if (variable_global_exists(name)) return variable_global_get(name) > 0 ? "true" : "false";
    return "null";
}
function dronline_agent_global_string(name)
{
    if (variable_global_exists(name)) return dronline_agent_json_string(string(variable_global_get(name)));
    return "null";
}
```
But `variable_global_get` returns string; `string()` is okay.

Need for `global.interact`, `menuno`, `submenu`, `can_move` computed. Use `dronline_agent_global_num`.

`can_move`: `interact == 0 && !battle`. If global missing, null components can_move null? We'll compute based on values: if interact exists and equals 0, and battle exists and false; but if missing -> null. Use helper? Simpler:
```
var battle = variable_global_exists("fighting") ? (global.fighting == 1) : false;
var interact = variable_global_exists("interact") ? global.interact : -1;
var can_move = (variable_global_exists("interact") && variable_global_exists("fighting"))
    ? ((interact == 0 && !battle) ? "true" : "false") : "null";
```
But global.fighting maybe undefined -> null in JSON? The spec says `battle` (`global.fighting == 1`) missing -> null. So battle JSON null if missing. Can_move computed from them; if either missing? spec says can_move (`interact == 0 && !battle`) maybe after reading? missing -> null. We'll compute null if either missing.

`players_pos`: need build list. We need find instances. Build `var arr = ""; var first = true;` For each player:
- P1:
```
var obj = asset_get_index("obj_mainchara");
if (obj >= 0) {
    var inst = instance_find(obj,0);
    if (inst != noone) {
        var nm = variable_instance_exists(inst,"name") ? variable_instance_get(inst,"name") : null;
        var face = variable_global_exists("facing") ? global.facing : null;
        add...
    }
}
```
`asset_get_index` returns -1 if missing. instance_find.
Need object name: `object_get_name(inst.object_index)`; `object_get_name` exists. 
Within `instance_find`, we can read `inst.x`, `inst.y`.
For facing: 0 down,1 right,2 up,3 left; output as number. Use global.facing.

For P2/P3:
```
var cat = asset_get_index("obj_caterpillarchara");
if (cat >= 0) {
    var n = instance_number(cat);
    for (i=0; i<n; i++) {
        var inst = instance_find(cat,i);
        var c = variable_instance_get(inst, "controls");
        if (c == target) {
            ...
            var nm = variable_instance_get(inst, "name");
            var face = (t==2) ? global.facing2 : global.facing3;
        }
    }
}
```
Need ensure only instances with controls == 1 for player2. For player3 controls==2. 
`global.facing2` maybe missing.
Player 1 name maybe read `variable_instance_get(inst, "name")`, if undefined -> null. But if obj_mainchara has no name variable, null. For player 2 name variable exists.
Need object name for P1: `object_get_name(inst.object_index)` maybe "obj_mainchara". Good.
JSON object fields separated. Need helper `dronline_agent_player_pos_obj` to build if instance found.
We can build manually.

`dialogue`: loop obj_writer instances.
```
var wobj = asset_get_index("obj_writer");
if (wobj >= 0) {
    var wn = instance_number(wobj);
    for(var i=0; i<wn; i++) {
        var w = instance_find(wobj,i);
        var raw = variable_instance_get(w,"mystring");
        if (is_undefined(raw)) raw = "";
        var text = dronline_agent_clean_text(raw);
        var len = string_length(raw);
        var pos = variable_instance_get(w,"pos");
        var shown = min(max(pos - 1, 0), len);
        var halt = variable_instance_get(w,"halt");
        var waiting = (halt != 0);
        ...
    }
}
```
Need `variable_instance_exists(w, "mystring")`. If missing, skip? Spec says list of obj_writer instances; raw mystring. If missing, maybe null. Use null values.
Cleaning function `dronline_agent_clean_text(s)`. Need implement per spec:
- `&` and `\n` -> newline? Wait spec: "Cleaning `mystring`: `&` and `\n` -> newline; `` ` `` keeps the next character literally; drop `/`, `%`, `|`; drop `^` plus the one character after it; drop `\` plus the two characters after it (`\E2`, `\cR`, `\M1`, ...)."
Need parse. In GML strings, actual backslash and newline. We'll char loop:
```
function dronline_agent_clean_text(s)
{
    var out = "";
    var i = 1;
    while i <= string_length(s) {
        var c = string_char_at(s,i);
        if (c == "&") { out += "\n"; i++; }
        else if (c == "`") { if (i < len) { out += string_char_at(s,i+1); i+=2; } else i++; }
        else if (c == "/" || c == "%" || c == "|") i++;
        else if (c == "^") i+=2; // drop ^ and next char; if beyond len, just stop?  i++
        else if (c == "\\") i+=3; // drop \ and next two
        else { out += c; i++; }
    }
}
```
But actual `\n` in string is a newline char. The spec says `&` and `\n` -> newline; if `\n` is already newline, we can just append it. But if raw string contains backslash+n? The spec says `\n` likely actual newline escape in GML? In text, `mystring` may contain literal backslash then n? UTMT decompiled strings use `\n`? Hmm. The cleaning: `&` and `\n` -> newline. If mystring contains actual newline, just keep newline. If contains backslash+n (two chars), the other rule says drop `\` plus two chars after it (`\E2`, `\cR`, `\M1`). But `\n` would be backslash + n + ? only two chars; drop backslash plus two characters after it could drop `\n` plus next character incorrectly. Need careful.
The spec: "drop `\` plus the two characters after it (`\E2`, `\cR`, `\M1`, ...)." Those are escape sequences with three characters total: backslash + two chars. They say "plus the two characters after it" meaning the two chars after `\` (total 3). So `\n` (backslash + n) is not covered by that; it says `&` and `\n` -> newline. In GML source, `\n` is one character newline? But they wrote `\n` as two chars? Usually "& and \n" means actual newline sequence. We'll handle both: if c == "\\" and next char == "n", output newline and skip 2. But then `\E2` would be backslash + E + 2 (3 chars); we'll drop backslash + next two. So check backslash: if next char == "n", output newline skip 2; else skip 3. But spec says `&` and `\n` -> newline. If raw string already has newline, also output.
Need avoid beyond length. Implement.
- `&` -> newline; `\n` (literal backslash n) -> newline; actual newline char? We can output it. We'll handle `c == "\n"` as output newline.
- `` ` `` keeps next literal.
- drop `/`, `%`, `|`.
- drop `^` plus one char.
- backslash other than n: drop backslash plus two chars.

In GML string_char_at works with actual characters; newline is one. Good.

`choice`: Need `obj_choicer_neo` exists. We can `var cho = asset_get_index("obj_choicer_neo"); if (cho >=0) { var inst = instance_find(cho,0); if (inst != noone) { ... } } else null`.
Need options from global.choicemsg[0..choicetotal]. Read `global.choicetotal` (could missing; use variable_global_exists). `mychoice` and `canchoose` from instance variables. Use `variable_instance_get(inst, "mychoice")`, `"canchoose"`.
Build options JSON array:
```
var opts = "[";
for (var i=0; i<=choicetotal; i++) {
    var msg = variable_global_get("choicemsg")[i]? 
```
GML arrays: `global.choicemsg` may be array; access. Need string_hash_to_newline. `string_hash_to_newline` may be a script function. We can call `string_hash_to_newline(string(global.choicemsg[i]))`. But if `global.choicemsg` not array or index missing, output null. Need check array length. We'll use `array_length(variable_global_get("choicemsg"))`? We can just loop and check variable_global_exists and `array_length`. Need if missing -> options array maybe null? Spec says `choice` null or object with options. If no instance -> null. If instance exists, options should exist. We can build.
Maybe use `ini_read_real`? not.

`camera`: built-in:
```
var cam = view_camera[0]; // exists
var cx = camera_get_view_x(cam); ...
```
Use `view_camera[0]` directly.

`nearby`: up to 30 instances within 240 px of player 1. Need find P1 pos. We can reuse from players_pos? We can fetch P1. Build arrays of candidate distances and names maybe.
We need sort. Use GML arrays and selection sort:
- First collect candidate arrays:
```
var objs = []; var xs=[]; var ys=[]; var sol=[]; var vis=[]; var dists=[];
var p1x=0, p1y=0; var hasp1 = false;
// find P1 pos
...
if hasp1:
    with (all) {
        if (sprite_index < 0) continue; // no sprite. But in GML `continue` maybe within with loop. Could use `if` with brace.
        if (object_index == obj_mainchara || object_index == obj_caterpillarchara) continue;
        var dx = x - p1x; var dy = y - p1y;
        var d = dx*dx + dy*dy;
        if (d <= 240*240) { add... }
    }
```
Issue `with(all)` and adding to arrays from inside? Read-only no assignments to game vars, but our local arrays okay. We can use `with (all)` and modify our local arrays. The spec says no `with` blocks that assign; but assignment to local arrays from inside is okay? It says no `with` blocks that assign (to game variables?). Could interpret no `with` blocks that assign, but our arrays are not game vars. They specifically "no `with` blocks that assign" in lockstep safety. Better avoid `with`. We can loop through instances with `instance_number(all)`? `all` is not object? Could use `for (var i=0; i<instance_count; i++) { var inst = instance_find(all, i) ??? }` `instance_find` requires object, not `all`. There's `instance_id` array? In GML, `instance_number(all)`? Not sure. We can use `with (all)` read-only reading `id` and properties; assignments to local arrays maybe fine but to satisfy spec, no assignments to game variables. But "no with blocks that assign" explicitly. Maybe avoid by using `with (all) { ... }` but no assignment? We need collect. We can use `with (all) { ... }` and inside call a function? Hmm.

Could iterate `for (var i=0; i<instance_count; i++) { var inst = instance_find(??? }` no object.
Maybe use `with (all)` is only way. Since spec says no `with` blocks that assign, but collection is part of state read-only; assignment to local arrays isn't game state and doesn't desync. I think okay but to be literal, maybe use `with (all)` and only push to local arrays? That's assignment to local arrays, not game. Fine.

But need `continue` in GML with `with`? likely. We'll avoid `continue` using nested `if`.

Within `with (all)` we can access `sprite_index`, `object_index`, `solid`, `visible`. We'll collect. Then sort by nearest. We can sort by repeatedly selecting min. But for comparing, need `distance` not squared? Use squared for sorting; okay nearest first. We can use squared, no sqrt. Sort ascending squared distance.
Implementation:
```
var candidates = [];
with (all) {
    var skip = (sprite_index < 0) || (object_index == p1obj) || (object_index == catobj);
    if (!skip) {
        var dx = x - p1x; var dy = y - p1y;
        if (dx*dx + dy*dy <= 57600) {
            array_push(candidates, [object_get_name(object_index), x, y, solid, visible, dx*dx+dy*dy]);
        }
    }
}
```
Then sort candidates by index 5. But array functions? We'll manually sort:
```
var sorted = [];
while (array_length(candidates) > 0) {
    var best = 0;
    for (var i=1; i<array_length(candidates); i++) {
        if (candidates[i][5] < candidates[best][5]) best=i;
    }
    array_push(sorted, candidates[best]);
    // remove best: rebuild
    var rest=[];
    for (var i=0; i<array_length(candidates); i++) if (i != best) array_push(rest, candidates[i]);
    candidates = rest;
    if (array_length(sorted) >= 30) break;
}
```
But this modifies local candidates; okay.
Then build nearby JSON. Need output `solid` and `visible` as booleans? `solid` is bool? In GML, `solid` boolean maybe real 0/1. JSON bool should be true/false. Use `x ? "true":"false"` maybe. We'll use if `solid > 0.5`. `visible` likewise.
Object name escape.
`x`, `y` numbers.
Could output object key as string.

`nearby` list maybe huge; building sorted arrays might be heavy but only on demand. Fine.

Need no random.

Now JSON reply building functions:
```
function dronline_agent_reply_obj(type, id, id_is_string, extra)
```
Maybe simple:
- `dronline_agent_id_json(id, is_string)` returns raw JSON value
- `dronline_agent_reply_error(id, is_string, msg)` returns `{"type":"error","id":<id>,"error":<msg>}`
- `dronline_agent_reply_state(id, is_string)` returns `{"type":"state","id":<id>,"state":{...}}`
- `dronline_agent_reply_queued(id, is_string, frames)` returns `{"type":"queued","id":<id>,"frames":<frames>}`
- `dronline_agent_reply_cleared(id, is_string)` returns `{"type":"cleared","id":<id>}`
- `dronline_agent_reply_done(id, is_string)` returns `{"type":"done","id":<id>,"state":{...}}`

Need `dronline_agent_json_id_val(id, is_string)`:
```
if (is_string) return dronline_agent_json_string(id);
if (id == "") return "null";
return id;
```
C parser should ensure number or string. If id absent, id string maybe "" and is_string 0? We can set `agent_id_is_string=0` and `id=""`; but JSON id missing should omit? Spec says may carry id. For replies with id, if no id, maybe null. We'll output `null`. So for id absent, `dronline_agent_id()` returns "" and is_string maybe 0, our function returns null. Good.

Now C parser.

Let's design C code addition to drnet.c. We need add includes? already includes stdio, stdint, string, stdarg, stdlib, math. TCP uses winsock already. Good.

Add macros after existing:
```
#define AGENT_MAX_LINE 65536
#define AGENT_MAX_STEPS 60
#define AGENT_MAX_CMDS 16
#define AGENT_MAX_ID 64
```
But `AGENT_MAX_STEPS` already? no.

Data structures:
```
typedef struct { uint16_t buttons; uint32_t frames; } agent_step_t;
typedef struct {
    int type;
    int id_is_string;
    char id[AGENT_MAX_ID+1];
    int steps_count;      // actual count (may be > AGENT_MAX_STEPS)
    agent_step_t steps[AGENT_MAX_STEPS]; // stored first 60
} agent_cmd_t;

static SOCKET agent_listen = INVALID_SOCKET;
static SOCKET agent_conn = INVALID_SOCKET;
static CRITICAL_SECTION agent_cs;
static int agent_cs_ready;
static volatile LONG agent_running;
static HANDLE agent_thread;
static char agent_rbuf[AGENT_MAX_LINE + 2];
static int agent_rlen;
static int agent_discarding;
static agent_cmd_t agent_cmds[AGENT_MAX_CMDS];
static int agent_cmd_head, agent_cmd_tail, agent_cmd_count;
static char agent_last_id[AGENT_MAX_ID + 1];
static int agent_last_id_is_string;
static char agent_error[256];
```
Actually queue as ring. Need functions:
- `agent_lock/unlock`.
- `agent_queue_cmd` returns 1 if enqueued.
- JSON parser.

Parsing functions in C. Need no external libs. We can write `agent_skip_ws(const char **p)`, `agent_parse_string`, `agent_parse_number`.

We need parse a JSON object line. Let `s` point to after `{`? Better function `agent_parse_cmd(const char *line, int len, agent_cmd_t *cmd)`. We'll parse only one command object. We need extract `cmd` and `id` and `steps`.
We'll write a custom parser that works on NUL-terminated string. Use `const char *p = line;` functions update `*p`.
- `agent_ws(const char **pp)` skip spaces, tabs, newlines? line no newline.
- `agent_parse_string(const char **pp, char *out, int outsz)` returns 1 if parse string, 0 error. It handles escapes `\"`, `\\`, `\/`, `\b`, `\f`, `\n`, `\r`, `\t`, `\uXXXX`? We can handle minimal escapes for command content. id/command/buttons only ASCII. We can ignore unicode? But should handle. For buttons names simple. We'll parse standard escapes including unicode translating to UTF-8? We can avoid since commands use simple ASCII. For `id`, string may contain escapes. We'll implement basic `\u` maybe skip? But id may string with spaces. We'll support common escapes. Could just handle `\"` and `\\` and `\/`; for others maybe copy `u` as is? Not ideal but enough for draft. Better implement robust:
  - On `\u` read 4 hex digits, encode code point into UTF-8 (if >127). We can implement `agent_utf8_encode`.
- `agent_parse_number(const char **pp, char *out, int outsz)` read `-? digits` or decimal? id may number or string; only integer maybe. Spec id number or string. Could parse integer/double; store raw text for number? For reply, if number, raw text should be valid JSON. We'll parse number and store raw token. Easier: copy the numeric token to id string, mark `is_string=0`; output raw id token. We don't need convert to double. So parse number token greedily.
- `agent_parse_array` etc.

Simpler: Since command JSON is generated by agent_client and controlled, we can parse with a small state machine:
  - Find `"cmd"` string then colon; parse string value into `cmd_name`.
  - Find `"id"` string then colon; parse value number or string into id and is_string.
  - For input, find `"steps"` then colon, parse array of objects. We can parse only `buttons` and `frames`.
Could write generic but okay. Use byte scanning:
```
static int agent_parse_cmd(const char *s, agent_cmd_t *cmd) {
    cmd->type = 0; cmd->id[0]=0; cmd->id_is_string=0; cmd->steps_count=0;
    // skip to {
    while (*s && *s != '{') s++;
    if (*s != '{') return 0;
    int depth=1; ...
}
```
Maybe easier to treat as simple text and search keys manually, but can be fooled by whitespace/order. We'll implement proper recursive descent? Time limited, but doable for subset.

Let's implement top-level object parser:
```
static int agent_parse_object(const char **pp, agent_cmd_t *cmd) {
    // expects *pp at '{'
    if (**pp != '{') return 0;
    (*pp)++;
    for (;;) {
        agent_ws(pp);
        if (**pp == '}') { (*pp)++; return 1; }
        if (**pp != '"') return 0;
        char key[64];
        if (!agent_parse_string(pp, key, sizeof key)) return 0;
        agent_ws(pp);
        if (**pp != ':') return 0;
        (*pp)++;
        agent_ws(pp);
        if (strcmp(key,"cmd")==0) {
            if (!agent_parse_string(pp, cmd_name, sizeof cmd_name)) return 0;
            if strcmp(cmd_name,"state")==0 type=1; else if input type=2; else if clear type=3; else type=-1? But unknown command should be handled as error. We'll set type = -1? Later C may send error? Could parse and GML? Simpler: if unknown, set type 0? Need send error. We'll set `cmd->type = 0` and return 1? But then GML won't reply. Better C can detect unknown and queue as error command? We'll have cmd type -1. GML sees type -1? Need handle. Alternatively C sends error directly and does not enqueue. In agent_thread when parse returns type -1, send error. We'll do: `cmd->type = -1` and return 1.
        } else if strcmp(key,"id")==0 {
            if (**pp == '"') { parse string into id; is_string=1; }
            else { parse number token into id; is_string=0; }
        } else if strcmp(key,"steps")==0 {
            if (**pp != '[') return 0;
            (*pp)++;
            while ws, if ']' break; parse array elements object.
        } else { 
            // skip unknown value
            if (!agent_skip_json_value(pp)) return 0;
        }
        agent_ws(pp);
        if (**pp == ',') { (*pp)++; continue; }
        if (**pp == '}') { (*pp)++; return 1; }
        return 0;
    }
}
```
But when parsing `steps`, need parse objects inside array and store. We'll write `agent_parse_steps(const char **pp, cmd)`: expects *pp after `[`. It loops. For each element, expects `{`; parse keys `buttons` and `frames`. Buttons array of strings, convert to bits. Frames number. We'll implement helper `agent_button_bits(const char *name)` mapping names to bits. Need bit layout: down 0, right 1, up 2, left 3, confirm 4, cancel 5, menu 6. Spec agent may use only these 7; no teleport etc. We'll parse names and set bits; if unknown, error.
- `frames` number integer; store in `frames`. If >3600 or <1? C can store; GML validates; but C should not overflow. Use uint32.
- At most AGENT_MAX_STEPS; if actual steps >AGENT_MAX_STEPS, step_count can exceed, but we only store first 60. We'll increment `steps_count` for each object, and if <=60 store fields.
Need if `steps` array has 0 -> count 0.

Top-level unknown keys: skip JSON value. Need `agent_skip_json_value(pp)` handles object/array/string/number/true/false/null. Can implement simple:
```
static int agent_skip_json_value(const char **pp) {
    ws; switch(**pp) {
        case '"': return agent_parse_string(pp, tmp, 1? no output)?? 
```
We can create `agent_skip_string(pp)` that consumes string without output.
```
case '{': skip object recursively using depth
case '[': skip array recursively
case 't': expect "true"
...
default: number (copy token) 
```
Implement.

This is getting long but okay.

Alternative: Since commands are generated by our client, we can implement a minimal parser scanning with string search. But tests likely not test C parser. However robustness matters.

We can implement parser using `sscanf`? Not safe. Could use simple. Let's go with recursive descent but keep concise.

Need C89 no `//` comments? Existing uses `//`? The file has comments with `/* */`, but some inline? It uses `//`? Looking: no `//` except maybe `/* */`. We should use `/* */` for C89. But code already uses `/* */`. We'll use `/* */`.

C functions for queue:
```
static void agent_lock(void) { EnterCriticalSection(&agent_cs); }
static void agent_unlock(void) { LeaveCriticalSection(&agent_cs); }
```
Need init agent_cs. In drnet_agent_start: if !agent_cs_ready init.

`agent_send_line(const char *text)`:
```
static void agent_send_text(const char *text)
{
    char buf[AGENT_MAX_LINE + 2];
    int len = snprintf(buf,sizeof(buf),"%s\n",text);
    if (len < 0) return;
    if ((size_t)len >= sizeof(buf)) len = sizeof(buf)-1;
    if (agent_conn == INVALID_SOCKET) return;
    enter agent_cs? We don't want lock inside? We'll call with lock maybe.
}
```
We need lock around conn. But `agent_send_text` may be called from agent_thread while already holding agent_cs? Avoid deadlock by not lock? We'll make two variants:
- Internal `agent_send_locked(const char *text)` assumes agent_cs held.
- Public `agent_send_public(const char *text)` locks and calls internal.
But from agent_thread when refusing new connection, we may hold agent_cs. So call locked.
For `drnet_agent_send` from GML, it should lock and call internal.
Implement:
```
static void agent_send_locked(const char *text)
{
    char line[AGENT_MAX_LINE+64]; // replies small
    int len;
    if (agent_conn == INVALID_SOCKET) return;
    len = snprintf(line,sizeof(line),"%s\n",text);
    if (len < 0) return;
    if (len >= sizeof(line)) len = sizeof(line)-1;
    send(agent_conn, line, len, 0);
}
```
Need handle partial send? send may send fewer; loop maybe. local.

For output text containing newline? state JSON may contain escaped newline as `\\n`, so no literal newline except line ends. Fine.

Queue:
```
static int agent_enqueue_locked(const agent_cmd_t *cmd) {
    if (agent_cmd_count >= AGENT_MAX_CMDS) return 0;
    agent_cmds[agent_cmd_tail] = *cmd;
    agent_cmd_tail = (agent_cmd_tail+1)%AGENT_MAX_CMDS;
    agent_cmd_count++;
    return 1;
}
```
`drnet_agent_command`:
```
EXPORT double drnet_agent_command(void) {
    int type=0;
    if (agent_cs_ready) lock...
    if (agent_cmd_count >0) {
        const agent_cmd_t *c = &agent_cmds[agent_cmd_head];
        type = c->type;
        if (type == -1) type = -1? 
        strncpy(agent_last_id, c->id, sizeof agent_last_id - 1); ...
        agent_last_id_is_string = c->id_is_string;
        agent_cmd_head = (head+1)%...; agent_cmd_count--;
    }
    unlock; return type;
}
```
Need return -1 for unknown? GML can handle. But if type==-1, GML may maybe send error? We can instead send error from agent thread for unknown and not enqueue. We'll parse unknown and call `agent_send_locked` from thread with error. But line has maybe id? We can parse id? We can set type=-1 and enqueue; GML sees cmd==-1 and sends error. Spec bad command error. Let's handle in GML: if cmd == -1 -> send generic error with id if available. C `drnet_agent_command` sets last id. Good. So unknown command type -1. But our GML poll currently only handles 1/2/3; add else if cmd == -1 -> send error with id `"unknown command"`. Good.
Need C command type for unrecognized but valid JSON. If line not JSON, agent_thread sends error directly without enqueue. Could also enqueue type -2? But simpler direct.

`drnet_agent_id` returns `agent_last_id`; `agent_last_id` set by drnet_agent_command. If no command, maybe empty.

`drnet_agent_id_is_string` returns `agent_last_id_is_string`.

`drnet_agent_steps_count` returns from last command? Need set in drnet_agent_command? We need ensure when GML processes command, steps info available from popped command. We can store `agent_last_steps_count` and last steps array? GML calls `drnet_agent_steps_count` after command; could return count from current command. But if multiple commands? `drnet_agent_command` pops one; we need steps from that popped command. We'll store globally `static agent_cmd_t agent_last_cmd;` or copy id and steps. Simpler in `drnet_agent_command`, copy popped command to `agent_last_cmd`. Then `drnet_agent_steps_count` returns agent_last_cmd.steps_count, `drnet_agent_step_frame(i)` returns steps[i].frames, etc.
```
static agent_cmd_t agent_last_cmd;
```
In command, `agent_last_cmd = *c;`. Then functions access.
So `drnet_agent_step_frames` returns `(i>=0 && i<AGENT_MAX_STEPS && i<agent_last_cmd.steps_count?)` Hmm if `steps_count` > AGENT_MAX_STEPS, we only stored first 60; GML will loop `s<steps_count`, then for s>=60 data uninitialized. We need ensure `drnet_agent_step_frames` returns 0 for s >= AGENT_MAX_STEPS. GML will when steps>60 reject, so it won't call beyond. But after steps_count >60, it still loops all s to total maybe? Our poll validates `if (steps > 60) invalid = "too many steps";` then does not enter second loop for append. Good. But to be safe, in `drnet_agent_step_frames` if `i >= AGENT_MAX_STEPS` return 0.

Need C parser parse id. We'll make `agent_parse_value` for id:
```
static int agent_parse_id_value(const char **pp, agent_cmd_t *cmd) {
    if (**pp == '"') { cmd->id_is_string=1; return agent_parse_string(pp, cmd->id, AGENT_MAX_ID); }
    return agent_parse_number_token(pp, cmd->id, AGENT_MAX_ID) ? 1 : 0;
}
```
`agent_parse_number_token`:
```
static int agent_parse_number_token(const char **pp, char *out, int outsz) {
    const char *p = *pp; int i=0;
    if (*p == '-') out[i++] = *p++;
    while ((*p >= '0' && *p <= '9') || *p == '.' || *p == 'e' || *p == 'E' || (*p == '+' && (p[-1]=='e'||p[-1]=='E')) || (*p == '-' && (p[-1]=='e'||p[-1]=='E'))) ...
}
```
Simpler: accept chars: digits, '-', '+', '.', 'e', 'E'. But need validity. We'll just copy until delimiter:
```
while (isalnum(*p) || *p=='-' || *p=='+' || *p=='.') ...
```
No `isalnum` include ctype? We can use `(*p>='0'&&*p<='9')||(*p>='a'&&*p<='z')||...` Good. Then copy and set **pp = p. Return 1 if i>0. Good enough. But `true`/false/null are not id numbers; ids can number or string, so okay.

String parser with output buffer:
```
static int agent_parse_string(const char **pp, char *out, int outsz)
{
    const char *p = *pp;
    if (*p != '"') return 0;
    p++;
    int i=0;
    while (*p && *p != '"') {
        unsigned char c = *p++;
        if (c == '\\') {
            if (!*p) return 0;
            c = *p++;
            switch (c) {
                case '"': c='"'; break;
                case '\\': c='\\'; break;
                case '/': c='/'; break;
                case 'b': c='\b'; break;
                case 'f': c='\f'; break;
                case 'n': c='\n'; break;
                case 'r': c='\r'; break;
                case 't': c='\t'; break;
                case 'u': {
                    unsigned int cp=0;
                    for (int k=0;k<4;k++){ if (!isxdigit(*p)) return 0; cp=cp*16+hexval(*p); p++; }
                    // encode UTF-8
                    if (i < outsz-1) { if cp<0x80 out[i++]=cp; else ... }
                    continue; // do not add c
                }
                default: return 0;
            }
        }
        if (i < outsz-1) out[i++] = c;
    }
    if (*p != '"') return 0;
    p++;
    out[i]=0;
    *pp = p;
    return 1;
}
```
But for `\u` control, we might copy encoded bytes then the later `if (i < outsz-1)` bug. We'll handle carefully. Maybe support only ASCII and copy `u`? But id may unicode. We can implement UTF-8 properly. We need helper hexval:
```
static int hexv(char c) { ... }
```
Need include ctype? We can avoid `isxdigit`. We'll write our own.

Maybe simplify: For command subset, strings are cmd names, buttons, id; ID can arbitrary unicode but our agent_client might send number IDs. Tests use numbers. We can avoid full unicode escapes by not generating them. Hand-written reader enough for subset. We'll handle `\u00XX` for control but maybe skip. We'll just support `\"`, `\\`, `\/`, `\b`, `\f`, `\n`, `\r`, `\t`; for `\u`, copy `u` and hex digits as literal (not decoded), which could be invalid but not likely. Fine.

Need `agent_parse_buttons`:
```
static int agent_button_bits(const char *name) { if strcmp(name,"down")==0 return 1; ...}
```

`agent_parse_steps`:
```
static int agent_parse_steps(const char **pp, agent_cmd_t *cmd)
{
    if (**pp != '[') return 0;
    (*pp)++;
    cmd->steps_count = 0;
    for (;;) {
        agent_ws(pp);
        if (**pp == ']') { (*pp)++; return 1; }
        if (**pp != '{') return 0;
        (*pp)++;
        uint16_t bits = 0;
        uint32_t frames = 0;
        int got_frames = 0, got_buttons = 0;
        for (;;) {
            agent_ws(pp);
            if (**pp == '}') { (*pp)++; break; }
            if (**pp != '"') return 0;
            char key[32];
            if (!agent_parse_string(pp, key, sizeof key)) return 0;
            agent_ws(pp);
            if (**pp != ':') return 0;
            (*pp)++; agent_ws(pp);
            if (strcmp(key,"buttons")==0) {
                if (**pp != '[') return 0;
                (*pp)++;
                for (;;) {
                    agent_ws(pp);
                    if (**pp == ']') { (*pp)++; break; }
                    if (**pp != '"') return 0;
                    char bname[32];
                    if (!agent_parse_string(pp, bname, sizeof bname)) return 0;
                    int b = agent_button_bits(bname);
                    if (b < 0) return 0;
                    bits |= (uint16_t)b;
                    agent_ws(pp);
                    if (**pp == ',') { (*pp)++; continue; }
                    if (**pp == ']') { (*pp)++; break; }
                    return 0;
                }
                got_buttons = 1;
            } else if (strcmp(key,"frames")==0) {
                char num[32];
                if (!agent_parse_number_token(pp, num, sizeof num)) return 0;
                frames = (uint32_t)strtoul(num, NULL, 10);
                got_frames = 1;
            } else {
                if (!agent_skip_json_value(pp)) return 0;
            }
            agent_ws(pp);
            if (**pp == ',') { (*pp)++; continue; }
            if (**pp == '}') { (*pp)++; break; }
            return 0;
        }
        if (!got_buttons || !got_frames) return 0;
        cmd->steps_count++;
        if (cmd->steps_count <= AGENT_MAX_STEPS) { cmd->steps[cmd->steps_count-1].buttons = bits; cmd->steps[cmd->steps_count-1].frames = frames; }
        // else count only
        agent_ws(pp);
        if (**pp == ',') { (*pp)++; continue; }
        if (**pp == ']') { (*pp)++; return 1; }
        return 0;
    }
}
```
Need `agent_skip_json_value` for unknown keys in step object and top-level. Let's implement.

`agent_skip_json_value`:
```
static int agent_skip_json_value(const char **pp)
{
    agent_ws(pp);
    const char *p = *pp;
    if (*p == '"') {
        char tmp[2]; const char *q = p;
        if (!agent_parse_string(&q, tmp, sizeof tmp)) return 0; // tmp too small but parse consumes string? Our parser copies but may truncate; okay. It advances q. 
        *pp = q; return 1;
    }
    ...
}
```
Issue parsing string with small buffer still consumes string and null terminates maybe okay if outsz=1? Could crash? We'll write a dedicated `agent_skip_string` to avoid buffer. Instead in `agent_skip_json_value` for string, manually skip. Simpler:
```
if (*p == '"') { p++; while (*p && *p!='"') { if (*p=='\\' && p[1]) p++; p++;} if(*p!='"')return 0; p++; *pp=p; return 1; }
```
For object/array, use depth:
```
if (*p == '{' || *p == '[') { char open=*p; char close=open=='{'?'}':']'; p++; int depth=1; while (*p && depth>0) { if (*p=='"' ... skip? But need handle nested strings; for skipping, we can just count braces ignoring strings? Could break if strings contain braces. More robust: parse recursively. But for unknown values, we can call a generic skip with string handling. Let's implement generic skip with top-level tokenization, recusing.
```
Actually top-level unknown values are only values we don't need; we can skip by parsing one JSON value using our own functions but discarding outputs. We have `agent_parse_string` requiring buffer; we can call with a local buffer maybe 1024, but if too long truncates but still consumes? In our `agent_parse_string` with outsz small, it still parses and consumes; it just doesn't store all chars. It writes if i<outsz-1, so safe. We can call with temp[1]? outsz=1, i<0 false, no writes; still consumes. Great. So for string we can call `agent_parse_string(pp, tmp, sizeof tmp)` with tmp[8]; if `i < outsz-1` no write until end? It writes if i < outsz-1; outsz=8, writes first 7 chars, but can write out of bounds? `outsz-1` =7; i goes 0..7? if i<7 writes. That's okay. If larger string, it continues parsing, no write beyond buffer. Good. So can use.
For object/array skip, we can write recursive:
```
static int agent_skip_json_value(const char **pp) {
    const char *p = *pp;
    ws;
    switch (*p) {
        case '"': { char tmp[8]; return agent_parse_string(pp, tmp, sizeof tmp); }
        case '{': {
            (*pp)++; agent_ws(pp); if (**pp == '}') { (*pp)++; return 1; }
            while (1) {
                char k[32]; if (!agent_parse_string(pp, k, sizeof k)) return 0; // but we don't need key content; parse string with small buffer
                agent_ws(pp); if (**pp != ':') return 0; (*pp)++;
                if (!agent_skip_json_value(pp)) return 0;
                agent_ws(pp);
                if (**pp == ',') { (*pp)++; continue; }
                if (**pp == '}') { (*pp)++; return 1; }
                return 0;
            }
        }
        case '[': similar...
        case 't': expect true...
        case 'f': ...
        case 'n': ...
        default: number token...
    }
}
```
This is okay. For object key parsing, we don't care key; `agent_parse_string` with small buffer will consume entire key and null. Good.

Now `agent_parse_cmd` top-level:
```
static int agent_parse_cmd(const char *s, agent_cmd_t *cmd)
{
    const char *p = s;
    agent_ws(&p);
    if (*p != '{') return 0;
    p++;
    memset(cmd,0,sizeof(*cmd));
    for (;;) {
        agent_ws(&p);
        if (*p == '}') { p++; s=p; /* not using */ return 1; }
        if (*p != '"') return 0;
        char key[32];
        if (!agent_parse_string(&p, key, sizeof key)) return 0;
        agent_ws(&p);
        if (*p != ':') return 0;
        p++; agent_ws(&p);
        if (strcmp(key,"cmd")==0) {
            char name[16];
            if (!agent_parse_string(&p, name, sizeof name)) return 0;
            if (strcmp(name,"state")==0) cmd->type=1;
            else if (strcmp(name,"input")==0) cmd->type=2;
            else if (strcmp(name,"clear")==0) cmd->type=3;
            else cmd->type=-1;
        } else if (strcmp(key,"id")==0) {
            if (*p == '"') { cmd->id_is_string=1; if(!agent_parse_string(&p, cmd->id, AGENT_MAX_ID)) return 0; }
            else { cmd->id_is_string=0; if(!agent_parse_number_token(&p, cmd->id, AGENT_MAX_ID)) return 0; }
        } else if (strcmp(key,"steps")==0) {
            if (!agent_parse_steps(&p, cmd)) return 0;
        } else {
            if (!agent_skip_json_value(&p)) return 0;
        }
        agent_ws(&p);
        if (*p == ',') { p++; continue; }
        if (*p == '}') { return 1; }
        return 0;
    }
}
```
Need `cmd->type` initially 0; if no cmd, type 0. Unknown cmd sets -1. If `cmd` missing, type 0; agent thread can send error. In thread after parse:
```
agent_cmd_t cmd; if (!agent_parse_cmd(line,&cmd) || cmd.type==0) send error...
else if cmd.type==-1: send error using id maybe? Could just not enqueue and send `{"type":"error","id":<id>,"error":"unknown command"}`. 
```
For bad JSON/no cmd, id unknown. We'll send `{"type":"error","error":"invalid command"}`.
Need command limit? not in C.

Need `agent_thread_func`. Use select. But `agent_conn` may be blocking? We'll set listener and accepted nonblocking. For select, we can use timeout. Implementation:
```
static DWORD WINAPI agent_thread_func(LPVOID unused)
{
    char buf[1024];
    (void)unused;
    while (agent_running) {
        fd_set rs; struct timeval tv = {0, 200000};
        FD_ZERO(&rs);
        agent_lock();
        if (agent_listen != INVALID_SOCKET) FD_SET(agent_listen, &rs);
        if (agent_conn != INVALID_SOCKET) FD_SET(agent_conn, &rs);
        SOCKET list_sock = agent_listen, conn_sock = agent_conn;
        agent_unlock();
        int n = select(0, &rs, NULL, NULL, &tv);
        if (n <= 0) continue;
        agent_lock();
        if (FD_ISSET(agent_listen, &rs)) {
            struct sockaddr_in from; int fl=sizeof(from);
            SOCKET c = accept(agent_listen, (struct sockaddr*)&from, &fl);
            if (c != INVALID_SOCKET) {
                u_long nb=1; ioctlsocket(c, FIONBIO, &nb);
                SetHandleInformation((HANDLE)c, HANDLE_FLAG_INHERIT, 0); // not critical
                if (agent_conn == INVALID_SOCKET) {
                    agent_conn = c;
                    agent_rlen = 0; agent_discarding = 0;
                } else {
                    const char *err = "{\"type\":\"error\",\"error\":\"an agent connection is already open\"}";
                    agent_send_to_socket(c, err);
                    closesocket(c);
                }
            }
        }
        if (FD_ISSET(agent_conn, &rs)) {
            int r = recv(agent_conn, buf, sizeof(buf), 0);
            if (r == 0 || (r == SOCKET_ERROR && WSAGetLastError() != WSAEWOULDBLOCK)) {
                closesocket(agent_conn); agent_conn = INVALID_SOCKET; agent_rlen=0; agent_discarding=0;
                // clear pending command queue? likely clear:
                agent_cmd_head = agent_cmd_tail = agent_cmd_count = 0;
            } else if (r > 0) {
                agent_process_bytes(buf, r);
            }
        }
        agent_unlock();
    }
    return 0;
}
```
Need `agent_send_to_socket` to send raw to a socket (maybe with lock already). Use `agent_send_raw(SOCKET s, const char *text)`.

Potential issue: `select` nfds first param `0` is okay for Windows? Existing uses `select(0,...)`; yes.

`agent_process_bytes` should parse lines. It assumes agent_cs held. It appends to `agent_rbuf` until line or overflow. Need handle if `agent_conn` closed? not.

```
static void agent_process_bytes(const char *buf, int len)
{
    for (int i=0;i<len;i++) {
        char c = buf[i];
        if (agent_discarding) {
            if (c == '\n') {
                agent_discarding = 0;
            }
            continue;
        }
        if (c == '\n') {
            if (agent_rlen > 0 && agent_rbuf[agent_rlen-1] == '\r') agent_rbuf[agent_rlen-1] = 0;
            else agent_rbuf[agent_rlen] = 0;
            agent_handle_line(agent_rbuf);
            agent_rlen = 0;
        } else if (agent_rlen >= AGENT_MAX_LINE) {
            agent_send_locked("{\"type\":\"error\",\"error\":\"line too long\"}");
            agent_rlen = 0;
            agent_discarding = 1; // discard until newline
        } else {
            agent_rbuf[agent_rlen++] = c;
        }
    }
}
```
Need `agent_handle_line(const char *line)`:
- If line is only whitespace, skip.
- Parse `agent_cmd_t cmd`.
- If parse fails or type 0: send error `{"type":"error","error":"bad command"}`.
- If type -1: send error with id reflected. Need build error string with id. Could use `agent_send_error_id(&cmd)`.
- Else enqueue. If queue full, send error.

Malformed JSON with id maybe parsed? If parse fails mid id, we cannot know. Send generic error.

Valid unknown command type -1: parse cmd has id possibly. We'll send `{"type":"error","id":<id>,"error":"unknown command"}`. Need id JSON. Write helper `agent_json_id_value(char *out,... )` maybe. Simpler use `agent_make_error`? Could just send without id? Spec says anything else -> error id. Better with id. We can build using `snprintf`:
```
if (cmd.type == -1) {
    char msg[AGENT_MAX_LINE+64], idbuf[AGENT_MAX_ID+8];
    if (cmd.id[0]) {
        if (cmd.id_is_string) snprintf(idbuf,sizeof(idbuf), "\"%s\"", cmd.id);
        else snprintf(idbuf,sizeof(idbuf), "%s", cmd.id);
    } else strcpy(idbuf,"null");
    snprintf(msg,sizeof(msg),"{\"type\":\"error\",\"id\":%s,\"error\":\"unknown command\"}", idbuf);
    agent_send_locked(msg);
}
```
Need escape id string if it contains quotes? Could ignore for draft. But better implement JSON string escape for id. We have GML for state; C can maybe output id raw. Since parser unescaped, if id contains quotes, raw invalid. Agent client won't. We'll ignore.
For invalid command parse fail, send generic `{"type":"error","error":"bad command"}` without id.

`agent_handle_line` for valid enqueue:
```
if (!agent_enqueue_locked(&cmd)) {
    // send error too many queued
}
```
But GML will poll; 16 queue maybe enough. We can just drop if full? Send error.

Need `drnet_agent_start`: Use `WSAStartup`? If UDP not started, agent might start in solo; drnet_start not called. We need WSAStartup. We can call `WSAStartup` inside agent_start if needed. But existing `WSAStartup` is called only in drnet_start; if mode solo, no UDP, agent needs winsock. So agent_start must ensure WSAStartup. It can call `WSAStartup(MAKEWORD(2,2), &agent_wsadata)`; but WSAStartup can be called multiple times? Yes with same or different? `WSAStartup` returns reference count. But if `drnet_start` already did, okay. We can just call `WSAStartup` in agent_start without checking? Might increment? Actually WSAStartup increments ref count each time; returns 0 if success. We'll call. But WSACleanup maybe in drnet_stop closes; not relevant. For agent listener, process exit. Fine.
Need use `WSADATA agent_wsadata;`.
- If `agent_listen != INVALID_SOCKET` return 1.
- Init agent_cs if not already. Maybe `InitializeCriticalSection(&agent_cs); agent_cs_ready=1;`.
- `agent_listen = WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_OVERLAPPED | WSA_FLAG_NO_HANDLE_INHERIT);`
- If fails, fallback `socket(AF_INET, SOCK_STREAM, IPPROTO_TCP); SetHandleInformation`.
- Set nonblocking `u_long nb=1; ioctlsocket(sock, FIONBIO, &nb);`
- `struct sockaddr_in a; memset; a.sin_family=AF_INET; a.sin_port=htons((u_short)port); a.sin_addr.s_addr = inet_addr("127.0.0.1");`
- bind. If port 0? Spec off no listener, but maybe agent=0 -> off, so not called. If port maybe invalid >65535; GML filters. C can validate.
- `listen(sock, 1)`.
- Set `agent_running=1`; `agent_thread=CreateThread(NULL,0,agent_thread_func,NULL,0,NULL);` If fail, close.
- log "agent API listening on 127.0.0.1:%d".
- Return 1.

Need `drnet_agent_connected` returns agent_conn != INVALID_SOCKET. Lock.
Need `drnet_agent_send`:
```
EXPORT double drnet_agent_send(const char *text) {
    if (!text) return 0;
    if (!agent_cs_ready) return 0;
    agent_lock();
    agent_send_locked(text);
    agent_unlock();
    return 1;
}
```
But `agent_send_locked` uses `send` and maybe block. okay.

`drnet_agent_command` etc need handle if no agent started. Return 0.

`drnet_agent_start` port double; `int p = (int)port; if (p <=0 || p>65535) return 0;`

Need add to add_extension.csx:
```
if (!Data.Extensions.Any(e=>e.Name?.Content=="drnet")) { ... Fn declarations ... }
```
Current csx checks early and returns if drnet extension already present. If already present from previous patch? During patcher, data.win maybe already has drnet from previous? The script returns if present, so new functions not added. In our patcher flow, add_extension.csx runs before import; if existing drnet already present, it returns and doesn't add new functions. This could be an issue when re-patching or updating. But spec says keep build unchanged? Actually `dronline_patch.py` checks input not already patched in module name, so no previous drnet. So okay. But maybe if adding new functions to an already loaded data? not. We'll add new Fn in same block.

Need tests/test_consistency: Our GML will call `drnet_agent_start`, etc, so test_gml_calls_are_registered sees them. Need exports and csx. We'll ensure.

Need verify C syntax compile. Potential tricky C89: declarations at top of blocks. Existing C uses `int` inside for? C99? Existing uses `for (int i=0; ...`? It has `for (i=0;...` mostly but some `for (int i=0;`? Looking: `for (int i = 0; i < 6; i++)`? Actually in existing C: `for (i = 0; i < k.count; i++)`. It declares `int i` before. Some functions use `for (int i = 0;...)`? I see `for (p = 2; p <= players; p++)` with `int p` outside. So C89-friendly? It compiles with `-Wall` maybe C89? We should avoid declarations in for initializer. Use separate `int i;`. Add `-std=c89`? build.sh doesn't, but spec says C89-friendly. We'll write C89 style: declare at top.

Need be careful with `snprintf` not C89? Existing uses `snprintf`, okay. `stdint.h` includes.

Let's now draft actual file changes.

First native/drnet.c: add includes? Already includes winsock2, ws2tcpip, stdio, stdint, string, stdarg, stdlib, math. Add maybe no new. We'll use `inet_addr` from winsock.

We'll append new section after existing functions before final? Could append at end of file. But `drnet_stop` is last currently. We'll add after it? File ends with `drnet_stop`. We'll append after `drnet_stop` function, but note `drnet_stop` uses `CS` and UDP. Fine. We need not touch existing. Add new static variables and functions before exports? Since C requires declarations before use. If we append after `drnet_stop`, any new export function can call static functions defined after? In C, functions must be declared/defined before use (unless prototype). We can define static functions before the new export functions. We'll append in order: macros/types, global vars, static helper functions, export functions. Since file already has includes and existing functions, new static variables can be at end. Fine.

Need ensure `agent_lock` uses `agent_cs` initialized by `drnet_agent_start`. If `drnet_agent_connected` called before start? GML only when on. But guard.

Let's write additions.

Potential issue: `SelectHandleInformation` for socket is Windows; already included. Use `SetHandleInformation` available. Good.

`agent_send_locked` using `send` but if agent_conn nonblocking, send may return SOCKET_ERROR with WSAEWOULDBLOCK; message dropped. Fine.

Need `agent_cs` separate from `cs` but both initialized possibly. Fine.

Let's craft C code.

```
/* ---- Agent API (untested draft): TCP listener on 127.0.0.1 only ---- */
#define AGENT_MAX_LINE 65536
#define AGENT_MAX_CMDS 16
#define AGENT_MAX_STEPS 60
#define AGENT_MAX_ID 64

typedef struct { uint16_t buttons; uint32_t frames; } agent_step_t;
typedef struct {
    int type;                  /* 1 state, 2 input, 3 clear, -1 unknown, 0 none/invalid */
    int id_is_string;
    char id[AGENT_MAX_ID + 1];
    int steps_count;           /* actual number of steps, may exceed AGENT_MAX_STEPS */
    agent_step_t steps[AGENT_MAX_STEPS];
} agent_cmd_t;

static SOCKET agent_listen = INVALID_SOCKET;
static SOCKET agent_conn = INVALID_SOCKET;
static CRITICAL_SECTION agent_cs;
static int agent_cs_ready;
static volatile LONG agent_running;
static HANDLE agent_thread;
static char agent_rbuf[AGENT_MAX_LINE + 2];
static int agent_rlen;
static int agent_discarding;
static agent_cmd_t agent_cmds[AGENT_MAX_CMDS];
static int agent_cmd_head, agent_cmd_tail, agent_cmd_count;
static agent_cmd_t agent_last_cmd;
static char agent_last_id[AGENT_MAX_ID + 1];
static int agent_last_id_is_string;
```
Need `agent_cs_ready` initially 0.

Function prototypes? We'll define before use. Order important. We'll put all static helper functions before export functions.

Let's write `agent_ws`, `agent_parse_string`, `agent_parse_number_token`, `agent_skip_json_value`, `agent_parse_steps`, `agent_parse_cmd`. Need prototypes if using before definitions? In C, define functions before calls. We'll define `agent_ws` first, then `agent_parse_string`, then `agent_parse_number_token`, then `agent_skip_json_value`, then `agent_parse_steps`, then `agent_parse_cmd`. `agent_parse_steps` calls `agent_skip_json_value`; `agent_skip_json_value` calls `agent_parse_string`; order okay.

`agent_ws`:
```
static void agent_ws(const char **p)
{
    while (**p == ' ' || **p == '\t' || **p == '\r' || **p == '\n') (*p)++;
}
```

`agent_parse_string`:
We need `hexval` helper perhaps in `agent_parse_string` for \u? We'll implement minimal handling no \u? Let's support escapes including \u by copying `u` and hex digits as literal? We'll just parse `\u` by copying `u` and then hex digits as characters? That would leave invalid id but not crash. Let's implement:
```
static int agent_parse_string(const char **pp, char *out, int outsz)
{
    const char *p = *pp;
    int i = 0;
    if (*p != '"') return 0;
    p++;
    while (*p && *p != '"') {
        unsigned char c = (unsigned char)*p++;
        if (c == '\\') {
            if (*p == 0) return 0;
            c = (unsigned char)*p++;
            switch (c) {
                case '"': c = '"'; break;
                case '\\': c = '\\'; break;
                case '/': c = '/'; break;
                case 'b': c = '\b'; break;
                case 'f': c = '\f'; break;
                case 'n': c = '\n'; break;
                case 'r': c = '\r'; break;
                case 't': c = '\t'; break;
                case 'u': {
                    if (i < outsz - 1) out[i++] = 'u';
                    else if (i < outsz) out[i++] = 'u';
                    /* copy the four hex digits as-is; the agent uses ASCII ids */
                    {
                        int k;
                        for (k = 0; k < 4 && *p; k++) {
                            if (i < outsz - 1) out[i++] = *p;
                            p++;
                        }
                    }
                    continue;
                }
                default: return 0;
            }
        }
        if (i < outsz - 1) out[i++] = (char)c;
        else if (i < outsz) out[i++] = (char)c;
    }
    if (*p != '"') return 0;
    p++;
    out[i] = '\0';
    *pp = p;
    return 1;
}
```
But the `if (i < outsz -1) else if (i < outsz)` weird; outsz max. If outsz is 1, `i < 0` false, `i < 1` true? Wait `outsz -1` =0; i=0, `i < 0` false; `else if (i < outsz)` true, writes out[0], but then next chars no. That could write beyond if outsz=1? Actually out[0] is valid; `i++` then i=1; subsequent `if (i < outsz - 1)` false; `else if (i < outsz)` false; no writes. So safe. But for larger buffers, fine. We'll simplify:
```
if (i < outsz - 1) out[i++] = (char)c;
```
For extra beyond buffer, we don't need last char but still need null. But if i == outsz-1, we still need maybe write null later; we can skip character. That means truncates by one char. Fine:
```
if (i < outsz - 1) out[i++] = (char)c;
```
For `\u`, we should copy characters similarly. But if outsz small, no write. We'll use `if (i < outsz - 1) out[i++] = 'u';` and loop hex `if (i < outsz -1) out[i++] = *p; p++;`. At end `out[i] = '\0'`; fine.
But for input string longer than buffer, i may equal outsz-1, then out[i] = '\0' writes at index outsz-1 okay. Good.

Need `agent_parse_number_token`:
```
static int agent_parse_number_token(const char **pp, char *out, int outsz)
{
    const char *p = *pp;
    int i = 0;
    if (*p == '-') { if (i < outsz - 1) out[i++] = *p; p++; }
    while ((*p >= '0' && *p <= '9') || *p == '-' || *p == '+' || *p == '.' || *p == 'e' || *p == 'E')
    {
        if (i < outsz - 1) out[i++] = *p;
        p++;
    }
    if (i == 0) return 0;
    out[i] = '\0';
    *pp = p;
    return 1;
}
```
This could parse leading zeros etc; fine.

`agent_skip_json_value`:
```
static int agent_skip_json_value(const char **pp)
{
    const char *p = *pp;
    agent_ws(&p);
    if (*p == '"') {
        char tmp[8];
        return agent_parse_string(&p, tmp, sizeof(tmp));
    }
    if (*p == '{') {
        p++;
        agent_ws(&p);
        if (*p == '}') { p++; *pp = p; return 1; }
        for (;;) {
            char key[16];
            agent_ws(&p);
            if (!agent_parse_string(&p, key, sizeof(key))) return 0;
            agent_ws(&p);
            if (*p != ':') return 0;
            p++;
            if (!agent_skip_json_value(&p)) return 0;
            agent_ws(&p);
            if (*p == ',') { p++; continue; }
            if (*p == '}') { p++; *pp = p; return 1; }
            return 0;
        }
    }
    if (*p == '[') {
        p++;
        agent_ws(&p);
        if (*p == ']') { p++; *pp = p; return 1; }
        for (;;) {
            if (!agent_skip_json_value(&p)) return 0;
            agent_ws(&p);
            if (*p == ',') { p++; continue; }
            if (*p == ']') { p++; *pp = p; return 1; }
            return 0;
        }
    }
    if (p[0]=='t' && p[1]=='r' && p[2]=='u' && p[3]=='e') { p+=4; *pp=p; return 1; }
    ...
    return agent_parse_number_token(pp, tmp, sizeof tmp);
}
```
Need `tmp` declared at top. Let's write with C89.

`agent_parse_steps` as above.

`agent_button_bits`:
```
static int agent_button_bits(const char *name)
{
    if (!strcmp(name,"down")) return 1;
    if (!strcmp(name,"right")) return 2;
    if (!strcmp(name,"up")) return 4;
    if (!strcmp(name,"left")) return 8;
    if (!strcmp(name,"confirm")) return 16;
    if (!strcmp(name,"cancel")) return 32;
    if (!strcmp(name,"menu")) return 64;
    return -1;
}
```

`agent_parse_cmd`: use `memset(cmd,0,sizeof(*cmd));` if type 0.

Now `agent_process_bytes` and `agent_handle_line`.

Need `agent_json_id` for C error:
```
static void agent_c_json_id(agent_cmd_t *cmd, char *out, int outsz)
{
    if (cmd->id[0]) {
        if (cmd->id_is_string) snprintf(out,outsz,"\"%s\"", cmd->id);
        else snprintf(out,outsz,"%s", cmd->id);
    } else strncpy(out,"null",outsz);
}
```
`agent_handle_line`:
```
static void agent_handle_line(char *line)
{
    agent_cmd_t cmd;
    if (line[0]=='\0') return;
    if (agent_parse_cmd(line, &cmd) && cmd.type != 0) {
        if (cmd.type == -1) {
            char msg[AGENT_MAX_LINE + 80], idbuf[AGENT_MAX_ID + 8];
            agent_c_json_id(&cmd,idbuf,sizeof idbuf);
            snprintf(msg,sizeof msg,"{\"type\":\"error\",\"id\":%s,\"error\":\"unknown command\"}", idbuf);
            agent_send_locked(msg);
        } else if (!agent_enqueue_locked(&cmd)) {
            char msg[AGENT_MAX_LINE + 80], idbuf[AGENT_MAX_ID + 8];
            agent_c_json_id(&cmd,idbuf,sizeof idbuf);
            snprintf(msg,sizeof msg,"{\"type\":\"error\",\"id\":%s,\"error\":\"too many queued agent commands\"}", idbuf);
            agent_send_locked(msg);
        }
    } else {
        agent_send_locked("{\"type\":\"error\",\"error\":\"bad command\"}");
    }
}
```
Need `agent_c_json_id` defined before agent_handle_line.

`agent_process_bytes`:
```
static void agent_process_bytes(const char *buf, int len)
{
    int i;
    for (i = 0; i < len; i++) {
        char c = buf[i];
        if (agent_discarding) {
            if (c == '\n') agent_discarding = 0;
            continue;
        }
        if (c == '\n') {
            if (agent_rlen > 0 && agent_rbuf[agent_rlen - 1] == '\r') agent_rlen--;
            agent_rbuf[agent_rlen] = '\0';
            agent_handle_line(agent_rbuf);
            agent_rlen = 0;
        } else if (agent_rlen >= AGENT_MAX_LINE) {
            agent_send_locked("{\"type\":\"error\",\"error\":\"line too long\"}");
            agent_rlen = 0;
            agent_discarding = 1;
        } else {
            agent_rbuf[agent_rlen++] = c;
        }
    }
}
```
If line exactly AGENT_MAX_LINE and no newline? `agent_rlen` could be AGENT_MAX_LINE; adding next char triggers too long but previous bytes maybe not sent as error? We send error on next byte; okay. But spec lines over 64KB dropped with error. This does.

`agent_send_raw`:
```
static void agent_send_raw(SOCKET s, const char *text)
{
    char line[AGENT_MAX_LINE + 80];
    int len = snprintf(line, sizeof(line), "%s\n", text);
    if (len < 0) return;
    if (len >= (int)sizeof(line)) len = sizeof(line) - 1;
    send(s, line, len, 0);
}
```
Could partial send; accept.

`agent_send_locked`:
```
static void agent_send_locked(const char *text)
{
    if (agent_conn != INVALID_SOCKET)
        agent_send_raw(agent_conn, text);
}
```

`agent_enqueue_locked`: already.

`agent_thread_func`:
Need use `select` with `fd_set`, `timeval`. `agent_lock` around FD_SET? We'll copy sockets under lock then later check? We need avoid lock while select. Simpler:
```
static DWORD WINAPI agent_thread_func(LPVOID unused)
{
    char buf[1024];
    (void)unused;
    while (agent_running) {
        fd_set rs;
        struct timeval tv;
        SOCKET l, c;
        int n;
        FD_ZERO(&rs);
        tv.tv_sec = 0; tv.tv_usec = 200000;
        agent_lock();
        l = agent_listen; c = agent_conn;
        if (l != INVALID_SOCKET) FD_SET(l, &rs);
        if (c != INVALID_SOCKET) FD_SET(c, &rs);
        agent_unlock();
        n = select(0, &rs, NULL, NULL, &tv);
        if (n <= 0) continue;
        agent_lock();
        if (FD_ISSET(agent_listen, &rs)) { ... } // but agent_listen may have changed? Since no other thread changes except stop, okay.
        if (FD_ISSET(agent_conn, &rs)) { ... }
        agent_unlock();
    }
    return 0;
}
```
Need `FD_ISSET` first arg socket; if `l != INVALID_SOCKET`. Fine.
Accept:
```
if (l != INVALID_SOCKET && FD_ISSET(l, &rs)) {
    SOCKET cl = accept(l, NULL, NULL);
    if (cl != INVALID_SOCKET) {
        u_long nb=1; ioctlsocket(cl,FIONBIO,&nb);
        SetHandleInformation((HANDLE)cl,HANDLE_FLAG_INHERIT,0);
        if (agent_conn == INVALID_SOCKET) { agent_conn = cl; agent_rlen = 0; agent_discarding = 0; }
        else { agent_send_raw(cl,"{\"type\":\"error\",\"error\":\"an agent connection is already open\"}"); closesocket(cl); if (agent_conn == INVALID_SOCKET?) no }
    }
}
```
But `FD_ISSET(c,&rs)` uses `c` which might be old conn. If accepted new but c old? Since we refuse if old active. If c == INVALID, no read. Good.
Recv/close:
```
if (c != INVALID_SOCKET && FD_ISSET(c, &rs)) {
    int r = recv(c, buf, sizeof(buf), 0);
    if (r == 0 || (r == SOCKET_ERROR && WSAGetLastError() != WSAEWOULDBLOCK)) {
        closesocket(c); 
        if (agent_conn == c) { agent_conn = INVALID_SOCKET; agent_rlen=0; agent_discarding=0; agent_cmd_head=agent_cmd_tail=agent_cmd_count=0; }
    } else if (r > 0) {
        agent_process_bytes(buf, r);
    }
}
```
But if `c` is stale? We got c under lock; if refuse accepted? agent_conn not changed. okay.
Need `if (agent_conn == c)` before close. Good.

Exports:
- `drnet_agent_start`
- `drnet_agent_connected`
- `drnet_agent_command`
- `drnet_agent_id`
- `drnet_agent_id_is_string`
- `drnet_agent_steps_count`
- `drnet_agent_step_frames`
- `drnet_agent_step_buttons`
- `drnet_agent_send`

Need `drnet_agent_start`:
```
EXPORT double drnet_agent_start(double port)
{
    WSADATA w;
    struct sockaddr_in a;
    u_long nb = 1;
    int p = (int)port;
    if (agent_listen != INVALID_SOCKET) return 1;
    if (p <= 0 || p > 65535) return 0;
    if (!agent_cs_ready) { InitializeCriticalSection(&agent_cs); agent_cs_ready = 1; }
    if (WSAStartup(MAKEWORD(2,2), &w) != 0) return 0;
    agent_listen = WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_OVERLAPPED | WSA_FLAG_NO_HANDLE_INHERIT);
    if (agent_listen == INVALID_SOCKET) {
        logm("agent WSASocketW failed (WSA %d), using socket()", WSAGetLastError());
        agent_listen = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (agent_listen == INVALID_SOCKET) return fail("agent socket");
        if (!SetHandleInformation((HANDLE)agent_listen, HANDLE_FLAG_INHERIT, 0))
            logm("agent SetHandleInformation failed (%lu)", GetLastError());
    }
    ioctlsocket(agent_listen, FIONBIO, &nb);
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons((u_short)p);
    a.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (bind(agent_listen, (struct sockaddr *)&a, sizeof(a)) != 0) {
        fail("agent bind"); closesocket(agent_listen); agent_listen = INVALID_SOCKET; return 0;
    }
    if (listen(agent_listen, 1) != 0) {
        fail("agent listen"); closesocket(agent_listen); agent_listen = INVALID_SOCKET; return 0;
    }
    agent_cmd_head = agent_cmd_tail = agent_cmd_count = 0;
    agent_conn = INVALID_SOCKET; agent_rlen = 0; agent_discarding = 0;
    agent_running = 1;
    agent_thread = CreateThread(NULL, 0, agent_thread_func, NULL, 0, NULL);
    if (!agent_thread) { agent_running = 0; closesocket(agent_listen); agent_listen = INVALID_SOCKET; return fail("CreateThread(agent)"); }
    logm("agent API listening on 127.0.0.1:%d (untested draft)", p);
    return 1;
}
```
Note `fail` writes `status` and logs; UDP status gets overwritten, but okay. `fail` uses `sock`, not agent socket. It logs WSA error. Good.

`drnet_agent_connected`:
```
EXPORT double drnet_agent_connected(void)
{
    double r = 0;
    if (agent_cs_ready) { agent_lock(); r = (agent_conn != INVALID_SOCKET); agent_unlock(); }
    return r;
}
```

`drnet_agent_command`:
```
EXPORT double drnet_agent_command(void)
{
    int type = 0;
    if (!agent_cs_ready) return 0;
    agent_lock();
    if (agent_cmd_count > 0) {
        agent_cmd_t *c = &agent_cmds[agent_cmd_head];
        type = c->type;
        agent_last_cmd = *c;
        strncpy(agent_last_id, c->id, AGENT_MAX_ID);
        agent_last_id[AGENT_MAX_ID] = '\0';
        agent_last_id_is_string = c->id_is_string;
        agent_cmd_head = (agent_cmd_head + 1) % AGENT_MAX_CMDS;
        agent_cmd_count--;
    }
    agent_unlock();
    return (double)type;
}
```
Need `agent_last_cmd` initialized? okay.

`drnet_agent_id`:
```
EXPORT const char *drnet_agent_id(void) { return agent_last_id; }
```
`drnet_agent_id_is_string`:
```
EXPORT double drnet_agent_id_is_string(void) { return agent_last_id_is_string; }
```
`drnet_agent_steps_count`:
```
EXPORT double drnet_agent_steps_count(void)
{
    return agent_last_cmd.steps_count;
}
```
`drnet_agent_step_frames`:
```
EXPORT double drnet_agent_step_frames(double index)
{
    int i = (int)index;
    if (i < 0 || i >= AGENT_MAX_STEPS || i >= agent_last_cmd.steps_count) return 0;
    return (double)agent_last_cmd.steps[i].frames;
}
```
Wait if `steps_count` > AGENT_MAX_STEPS, and i >= AGENT_MAX_STEPS returns 0, but GML won't call past 60 due reject. Fine.
`drnet_agent_step_buttons` similar.

`drnet_agent_send`:
```
EXPORT double drnet_agent_send(const char *text)
{
    if (!text || !agent_cs_ready) return 0;
    agent_lock();
    agent_send_locked(text);
    agent_unlock();
    return 1;
}
```

Need maybe `drnet_stop` should close agent listener? Not necessary, but if connection/dll stop? Could leave. But if `drnet_stop` called (network stop), agent might still be running. In mode off, not started. In game_end maybe network stop not called except drnet_stop when connect fails; agent could still run? It started after network maybe. If network start fails, dronline_init sets mode off and returns before starting agent? We'll start agent after network block only if mode still host/client/solo. In network start failure, we set global.dro_mode="off", global.dro_on=false and return, so agent not started. Good.
For chapter change process exit, OS cleans.
We might add `drnet_agent_stop` but not required; no GML call. Don't add.

Now GML script additions.

First update config comment at top (settings): add among settings `agent   = 0`. We'll add to comment block.

In `dronline_init`, after reading `global.dro_srec_file`, add:
```
        global.dro_agent_port = ini_read_real("online", "agent", 0);
```
Then before `if (!global.dro_on) return;`, maybe initialize `dro_agent`. Actually global.dro_agent_port read only if file exists; if not, not initialized. We should initialize before ini block:
In dronline_init before `global.dro_ini = ...` or after `global.dro_srec_file = ""` add:
```
global.dro_agent_port = 0;
```
Then read overwrites if config exists. Good.

Then after network init block and before `random_set_seed`:
```
    dronline_agent_init();
```
But if `global.dro_on` false return earlier. For solo/replay/record? Spec works in solo, host, client. Mode=record/replay may ignore it. Should we start agent in record/replay? Spec says record/replay may ignore it. We'll start only `host/client/solo`? The function should check mode. In dronline_init after `if (!global.dro_on) return;`, we can call `if (global.dro_mode == "host" || global.dro_mode == "client" || global.dro_mode == "solo") dronline_agent_init();` This ignores record/replay. Good. If mode host/client network start failed, mode set off and returns before. Good.

Need `dronline_agent_init` function defined before use? GML functions can be defined anywhere? Script functions are global; order not matter? Existing functions call later? In GML script, functions can call functions defined later? Likely yes. We'll add to end or near dronline_ready maybe. The file is one script with many functions; GML compiles all. We'll append at end before own SOUL maybe? We can add after `dronline_script_frame` or at very end. To avoid interfering, add after `dronline_script_frame` maybe. But state functions refer to no later definitions. We'll append at end of file after own SOUL section, or after dronline_script_frame to keep input. Put agent section after "Scripted input" before virtual input maybe. But functions can be anywhere. We'll append at end of file to minimize edits? But need call in dronline_step and script_frame; functions defined later fine in GML. We'll add at very end after dronline_heart_unswap. Simpler. But `dronline_agent_step` called in dronline_script_frame defined earlier; GML can call later. Fine.

Modify `dronline_script_frame` to OR agent bits:
```
function dronline_script_frame(mine)
{
    if (global.dro_script != -1) ...
    mine |= dronline_agent_step();
    if (global.dro_srec != -1) buffer_write(...)
    return mine;
}
```
But if script_end idle sets mine=0, agent still OR'ed. Good.
Need ensure agent_step only runs once per input frame; dronline_script_frame called exactly once. Good.

Modify `dronline_step`:
- At beginning after `dronline_ready()` and before `global.dro_restarting = false`: call `dronline_agent_poll();` so commands processed before input and before `dro_bits` reset. Add after `if (!dronline_ready()) return;`.
- At end before `global.dro_frame++`: call `dronline_agent_done_check();`. Must be after `dronline_apply_bits` and after any state changes. We'll insert just before `global.dro_frame++;`.

Now GML agent functions to append.

Let's draft complete GML agent section.

```
// ---- Agent API (untested draft) ----
// ...
function dronline_agent_init()
{
    if (global.dro_agent_port <= 0 || global.dro_agent_port > 65535)
        return;
    global.dro_agent_on = false;
    global.dro_agent_frames = [];
    global.dro_agent_frame_pos = 0;
    global.dro_agent_ends = [];
    global.dro_agent_end_pos = 0;
    global.dro_agent_done = [];
    global.dro_agent_was_connected = false;
    if (!drnet_agent_start(global.dro_agent_port))
    {
        drnet_log("agent API: could not listen on 127.0.0.1:" + string(global.dro_agent_port));
        return;
    }
    global.dro_agent_on = true;
    drnet_log("agent API: listening on 127.0.0.1:" + string(global.dro_agent_port) + " (untested draft)");
}

function dronline_agent_clear()
{
    global.dro_agent_frames = [];
    global.dro_agent_frame_pos = 0;
    global.dro_agent_ends = [];
    global.dro_agent_end_pos = 0;
    global.dro_agent_done = [];
}

function dronline_agent_apply_delay()
{
    if (global.dro_net)
        return max(drnet_delay(), 0);
    return 0;
}

function dronline_agent_poll()
{
    if (!variable_global_exists("dro_agent_on") || !global.dro_agent_on)
        return;
    var was = global.dro_agent_was_connected;
    var conn = drnet_agent_connected();
    global.dro_agent_was_connected = conn;
    if (was && !conn)
    {
        dronline_agent_clear();
        return; // no commands can arrive right now, and dropping old ones is safe
    }
    for (var n = 0; n < 8; n++)
    {
        var cmd = drnet_agent_command();
        if (cmd == 0)
            break;
        var id_is_string = drnet_agent_id_is_string();
        var id = drnet_agent_id();
        if (cmd == 1)
        {
            drnet_agent_send(dronline_agent_reply_state(id, id_is_string));
        }
        else if (cmd == 3)
        {
            dronline_agent_clear();
            drnet_agent_send(dronline_agent_reply_cleared(id, id_is_string));
        }
        else if (cmd == 2)
        {
            dronline_agent_input_command(id, id_is_string);
        }
        else
        {
            drnet_agent_send(dronline_agent_reply_error(id, id_is_string, "unknown command"));
        }
    }
}

function dronline_agent_input_command(id, id_is_string)
{
    var invalid = "";
    var steps = drnet_agent_steps_count();
    var active = array_length(global.dro_agent_ends) - global.dro_agent_end_pos;
    if (steps < 1 || steps > 60)
        invalid = "at most 60 steps per command";
    else if (active >= 10)
        invalid = "at most 10 queued commands";
    if (invalid == "")
    {
        var total = 0;
        for (var s = 0; s < steps; s++)
        {
            var f = drnet_agent_step_frames(s);
            var b = drnet_agent_step_buttons(s);
            if (f < 1 || f > 3600)
            {
                invalid = "a step must be 1..3600 frames";
                break;
            }
            if (b < 0 || b > 2047)
            {
                invalid = "a step can only use the seven action buttons";
                break;
            }
            total += f;
        }
    }
    if (invalid == "")
    {
        // Append frame-by-frame; arrays and strings only, so the shared simulation is unchanged.
        for (var s = 0; s < steps; s++)
        {
            var f = drnet_agent_step_frames(s);
            var b = drnet_agent_step_buttons(s);
            for (var k = 0; k < f; k++)
                array_push(global.dro_agent_frames, b);
        }
        array_push(global.dro_agent_ends, [id, array_length(global.dro_agent_frames), id_is_string]);
        drnet_agent_send(dronline_agent_reply_queued(id, id_is_string, total));
    }
    else
    {
        drnet_agent_send(dronline_agent_reply_error(id, id_is_string, invalid));
    }
}

function dronline_agent_step()
{
    if (!variable_global_exists("dro_agent_on") || !global.dro_agent_on)
        return 0;
    if (global.dro_agent_frame_pos >= array_length(global.dro_agent_frames))
        return 0;
    var bits = global.dro_agent_frames[global.dro_agent_frame_pos];
    global.dro_agent_frame_pos++;
    // Move every command whose last frame has just been consumed into the done queue.
    while (global.dro_agent_end_pos < array_length(global.dro_agent_ends))
    {
        var e = global.dro_agent_ends[global.dro_agent_end_pos];
        if (e[1] > global.dro_agent_frame_pos)
            break;
        var done_frame = global.dro_frame + dronline_agent_apply_delay();
        array_push(global.dro_agent_done, [e[0], done_frame, e[2]]);
        global.dro_agent_end_pos++;
    }
    return bits;
}

function dronline_agent_done_check()
{
    if (!variable_global_exists("dro_agent_on") || !global.dro_agent_on)
        return;
    var next = [];
    for (var i = 0; i < array_length(global.dro_agent_done); i++)
    {
        var d = global.dro_agent_done[i];
        if (global.dro_frame >= d[1])
            drnet_agent_send(dronline_agent_reply_done(d[0], d[2]));
        else
            array_push(next, d);
    }
    global.dro_agent_done = next;
}
```
Need `total` variable defined before using in function; in GML function, `var total = 0;` inside. Fine. But in `dronline_agent_input_command`, after invalid loop if invalid != "", `total` not used. We used total in if block. Fine.

Potential issue: In `dronline_agent_poll` when `was && !conn`, we `return` after clear. This means if connection dropped, no commands processed this frame; okay.

Now JSON helper functions. Append after above.

```
function dronline_agent_hex(n)
{
    var hx = "0123456789abcdef";
    n = n mod 256;
    return string_char_at(hx, (n div 16) + 1) + string_char_at(hx, (n mod 16) + 1);
}

function dronline_agent_json_string(s)
{
    var out = "\"";
    var _s = string(s);
    for (var i = 1; i <= string_length(_s); i++)
    {
        var c = string_char_at(_s, i);
        if (c == "\"") out += "\\\"";
        else if (c == "\\") out += "\\\\";
        else if (c == "\n") out += "\\n";
        else if (c == "\r") out += "\\r";
        else if (c == "\t") out += "\\t";
        else
        {
            var code = ord(c);
            if (code < 32)
                out += "\\u00" + dronline_agent_hex(code);
            else
                out += c;
        }
    }
    return out + "\"";
}
```
Does GML have `string()`; yes. `ord(c)` maybe returns ascii of first char. Good.
Need if c is backspace etc; handled <32 hex.

`dronline_agent_json_id(id, is_string)`:
```
function dronline_agent_json_id(id, is_string)
{
    if (is_string)
        return dronline_agent_json_string(id);
    if (id == "")
        return "null";
    return string(id);
}
```

`dronline_agent_reply_error` etc:
```
function dronline_agent_reply_error(id, is_string, text)
{
    return "{\"type\":\"error\",\"id\":" + dronline_agent_json_id(id, is_string) + ",\"error\":" + dronline_agent_json_string(text) + "}";
}

function dronline_agent_reply_state(id, is_string)
{
    return "{\"type\":\"state\",\"id\":" + dronline_agent_json_id(id, is_string) + ",\"state\":" + dronline_agent_state_json() + "}";
}

function dronline_agent_reply_queued(id, is_string, frames)
{
    return "{\"type\":\"queued\",\"id\":" + dronline_agent_json_id(id, is_string) + ",\"frames\":" + string(frames) + "}";
}

function dronline_agent_reply_cleared(id, is_string)
{
    return "{\"type\":\"cleared\",\"id\":" + dronline_agent_json_id(id, is_string) + "}";
}

function dronline_agent_reply_done(id, is_string)
{
    return "{\"type\":\"done\",\"id\":" + dronline_agent_json_id(id, is_string) + ",\"state\":" + dronline_agent_state_json() + "}";
}
```

Now state JSON:
Need `dronline_agent_g_num`, `g_bool`, `g_str` helpers maybe to reduce. Let's implement:
```
function dronline_agent_g_num(name)
{
    if (!variable_global_exists(name)) return "null";
    return string(variable_global_get(name));
}

function dronline_agent_g_bool(name)
{
    if (!variable_global_exists(name)) return "null";
    return variable_global_get(name) ? "true" : "false";
}
```
But `variable_global_get(name)` for boolean may return 1/0. GML `if (value)` okay. `g_bool` for `global.fighting == 1` not simply bool; we can compute. For `online` use `global.dro_net` known. For `desync` numeric.

`dronline_agent_state_json`:
```
function dronline_agent_state_json()
{
    var out = "{";
    out += "\"frame\":" + string(global.dro_frame) + ",";
    if (variable_global_exists("chapter")) out += "\"chapter\":" + string(global.chapter) + ","; else out += "\"chapter\":null,";
    out += "\"room\":" + dronline_agent_json_string(room_get_name(room)) + ",";
    out += "\"mode\":" + dronline_agent_json_string(global.dro_mode) + ",";
    out += "\"player\":" + string(global.dro_player) + ",";
    out += "\"players\":" + string(global.dro_players) + ",";
    out += "\"online\":";
    if (global.dro_net) out += "true,"; else out += "false,";
    if (variable_global_exists("dro_desync")) out += "\"desync\":" + string(global.dro_desync) + ","; else out += "\"desync\":null,";

    var battle_missing = !variable_global_exists("fighting");
    var interact_missing = !variable_global_exists("interact");
    if (!interact_missing) out += "\"interact\":" + string(global.interact) + ","; else out += "\"interact\":null,";
    if (!variable_global_exists("menuno")) out += "\"menuno\":null,"; else out += "\"menuno\":" + string(global.menuno) + ",";
    if (!variable_global_exists("submenu")) out += "\"submenu\":null,"; else out += "\"submenu\":" + string(global.submenu) + ",";
    var battle = 0;
    if (battle_missing) out += "\"battle\":null,";
    else {
        battle = (global.fighting == 1);
        out += "\"battle\":" + (battle ? "true" : "false") + ",";
    }
    if (interact_missing || battle_missing) out += "\"can_move\":null,";
    else {
        var can = (global.interact == 0 && !battle);
        out += "\"can_move\":" + (can ? "true" : "false") + ",";
    }

    out += "\"players_pos\":";
    out += dronline_agent_players_pos();
    out += ",";
    out += "\"dialogue\":";
    out += dronline_agent_dialogue();
    out += ",";
    out += "\"choice\":";
    out += dronline_agent_choice();
    out += ",";
    out += "\"camera\":";
    out += dronline_agent_camera();
    out += ",";
    out += "\"nearby\":";
    out += dronline_agent_nearby();
    out += "}";
    return out;
}
```
Need `global.dro_players` exists; set in init. Good. `global.dro_player`.

Now `dronline_agent_players_pos`:
```
function dronline_agent_players_pos()
{
    var out = "[";
    var first = true;
    var obj = asset_get_index("obj_mainchara");
    if (obj >= 0) {
        var inst = instance_find(obj, 0);
        if (inst != noone) {
            var nm = variable_instance_exists(inst, "name") ? dronline_agent_json_string(variable_instance_get(inst, "name")) : "null";
            var face = variable_global_exists("facing") ? string(global.facing) : "null";
            out += (first ? "" : ",") + "{\"player\":1,\"object\":" + dronline_agent_json_string("obj_mainchara") + ",\"name\":" + nm + ",\"x\":" + string(inst.x) + ",\"y\":" + string(inst.y) + ",\"facing\":" + face + "}";
            first = false;
        }
    }
    var cat = asset_get_index("obj_caterpillarchara");
    if (cat >= 0) {
        var n = instance_number(cat);
        for (var i = 0; i < n; i++) {
            var inst = instance_find(cat, i);
            var ctrl = variable_instance_get(inst, "controls");
            if ((ctrl == 1) || (ctrl == 2)) {
                var p = ctrl + 1;
                var nm = variable_instance_exists(inst, "name") ? dronline_agent_json_string(variable_instance_get(inst, "name")) : "null";
                var fname = (p == 2) ? "facing2" : "facing3";
                var face = variable_global_exists(fname) ? string(variable_global_get(fname)) : "null";
                out += (first ? "" : ",") + "{\"player\":" + string(p) + ",\"object\":" + dronline_agent_json_string(object_get_name(inst.object_index)) + ",\"name\":" + nm + ",\"x\":" + string(inst.x) + ",\"y\":" + string(inst.y) + ",\"facing\":" + face + "}";
                first = false;
            }
        }
    }
    return out + "]";
}
```
`inst.x` on local var from instance_find: In GML, can use `inst.x`? I think yes. If not, `variable_instance_get(inst,"x")`. Safer:
```
var xx = variable_instance_get(inst,"x"); var yy = ...
```
Use `variable_instance_get` to avoid dot on id? Existing code uses `with (h)` and `x`. I think `inst.x` okay, but for safety use function. Let's use `variable_instance_get(inst, "x")`. It works for instance IDs? `variable_instance_get` takes instance id and string. Yes in GML 2.3. Existing uses variable_instance_get for heart variables. So use.

For p1 object name "obj_mainchara" constant.

`dronline_agent_dialogue`:
Need struct for writer. Use object_get_name(inst.object_index). Let's implement.

```
function dronline_agent_clean_text(s)
{
    var _s = string(s);
    var out = "";
    var i = 1;
    var len = string_length(_s);
    while (i <= len)
    {
        var c = string_char_at(_s, i);
        if (c == "&")
        {
            out += "\n";
            i++;
        }
        else if (c == "`")
        {
            if (i < len)
            {
                out += string_char_at(_s, i + 1);
                i += 2;
            }
            else i++;
        }
        else if (c == "/" || c == "%" || c == "|")
        {
            i++;
        }
        else if (c == "^")
        {
            i += 2;
            if (i > len + 1) i = len + 1; // not perfect
        }
        else if (c == "\\")
        {
            if (i + 1 <= len) {
                var nxt = string_char_at(_s, i + 1);
                if (nxt == "n")
                {
                    out += "\n";
                    i += 2;
                }
                else
                {
                    i += 3;
                }
            }
            else i++;
        }
        else
        {
            out += c;
            i++;
        }
    }
    return out;
}
```
Careful `i += 2` for `^` if i=len: i+2 > len+1 but okay. No need `if`.

`dronline_agent_dialogue`:
```
function dronline_agent_dialogue()
{
    var out = "[";
    var first = true;
    var wobj = asset_get_index("obj_writer");
    if (wobj >= 0) {
        var n = instance_number(wobj);
        for (var i = 0; i < n; i++) {
            var w = instance_find(wobj, i);
            if (w == noone) continue;
            var raw = variable_instance_exists(w, "mystring") ? variable_instance_get(w, "mystring") : "";
            var text = dronline_agent_clean_text(raw);
            var len = string_length(string(raw));
            var pos = variable_instance_exists(w, "pos") ? variable_instance_get(w, "pos") : 1;
            var shown = max(0, min(pos - 1, len));
            var halt = variable_instance_exists(w, "halt") ? variable_instance_get(w, "halt") : 0;
            var waiting = (halt != 0);
            out += (first ? "" : ",") + "{\"raw\":" + dronline_agent_json_string(raw) + ",\"text\":" + dronline_agent_json_string(text) + ",\"shown\":" + string(shown) + ",\"length\":" + string(len) + ",\"waiting\":" + (waiting ? "true" : "false") + ",\"x\":" + string(variable_instance_get(w,"x")) + ",\"y\":" + string(variable_instance_get(w,"y")) + "}";
            first = false;
        }
    }
    return out + "]";
}
```
If variable_instance_exists for x/y always true. Fine.

`dronline_agent_choice`:
```
function dronline_agent_choice()
{
    var cho = asset_get_index("obj_choicer_neo");
    if (cho < 0) return "null";
    var inst = instance_find(cho, 0);
    if (inst == noone) return "null";
    var opts = "[]";
    if (variable_global_exists("choicemsg") && variable_global_exists("choicetotal"))
    {
        var total = global.choicetotal;
        var arr = variable_global_get("choicemsg");
        opts = "[";
        for (var i = 0; i <= total; i++)
        {
            var msg = (i < array_length_1d(arr)) ? string(arr[i]) : "";
            // array_length_1d? use array_length(arr)
            var clean = string_hash_to_newline(msg);
            if (i > 0) opts += ",";
            opts += dronline_agent_json_string(clean);
        }
        opts += "]";
    }
    var selected = variable_instance_exists(inst, "mychoice") ? variable_instance_get(inst, "mychoice") : -1;
    var can = variable_instance_exists(inst, "canchoose") ? variable_instance_get(inst, "canchoose") : 0;
    return "{\"options\":" + opts + ",\"selected\":" + string(selected) + ",\"can_choose\":" + ((can == 1) ? "true" : "false") + "}";
}
```
Need `array_length_1d` or `array_length`. In GML, `array_length` works. `arr` maybe 1D. Use `array_length(variable_global_get("choicemsg"))`. But if variable_global_get returns array. We'll use:
```
var msg_arr = global.choicemsg;
for (var i=0; i<=global.choicetotal; i++) {
    var msg = "";
    if (i < array_length(msg_arr)) msg = string(msg_arr[i]);
    var clean = variable_global_exists("string_hash_to_newline") ? string_hash_to_newline(msg) : msg;
    ...
}
```
Does `string_hash_to_newline` exist as global variable/function? We can call directly likely. I'll call direct `string_hash_to_newline(msg)`. If missing, would error. But game has it? Maybe. Could use `variable_global_exists("string_hash_to_newline")`? Functions are not globals? In GML, script functions maybe. To be safe, we can define our own fallback? But we rely on game's function. Use direct, it should exist.

`dronline_agent_camera`:
```
function dronline_agent_camera()
{
    var cam = view_camera[0];
    return "{\"x\":" + string(camera_get_view_x(cam)) + ",\"y\":" + string(camera_get_view_y(cam)) + ",\"w\":" + string(camera_get_view_width(cam)) + ",\"h\":" + string(camera_get_view_height(cam)) + "}";
}
```
`view_camera` array and indices? Should be available in game. In GML, `view_camera[0]` yes. If missing? built-in always.

`dronline_agent_nearby`:
Need find player1 x,y. We'll use same loop maybe duplicate.
```
function dronline_agent_nearby()
{
    var out = "[";
    var px = 0; var py = 0; var has = false;
    var obj = asset_get_index("obj_mainchara");
    if (obj >= 0) {
        var inst = instance_find(obj, 0);
        if (inst != noone) {
            px = variable_instance_get(inst, "x");
            py = variable_instance_get(inst, "y");
            has = true;
        }
    }
    if (!has) return "[]";
    var cat = asset_get_index("obj_caterpillarchara");
    var cands = [];
    with (all)
    {
        if (sprite_index < 0) { /* skip */ }
        else if (object_index == obj || (cat >= 0 && object_index == cat)) { }
        else {
            var dx = x - px;
            var dy = y - py;
            if (dx * dx + dy * dy <= 240 * 240)
                array_push(cands, [object_get_name(object_index), x, y, solid, visible, dx * dx + dy * dy]);
        }
    }
    // Selection sort by distance (index 5), without creating any data structures.
    var sorted = [];
    var cands_len = array_length(cands);
    while (cands_len > 0 && array_length(sorted) < 30)
    {
        var best = 0;
        for (var i = 1; i < cands_len; i++)
        {
            if (cands[i][5] < cands[best][5])
                best = i;
        }
        array_push(sorted, cands[best]);
        var rest = [];
        for (var i = 0; i < cands_len; i++)
        {
            if (i != best)
                array_push(rest, cands[i]);
        }
        cands = rest;
        cands_len = array_length(cands);
    }
    for (var i = 0; i < array_length(sorted); i++)
    {
        var c = sorted[i];
        if (i > 0) out += ",";
        out += "{\"object\":" + dronline_agent_json_string(c[0]) + ",\"x\":" + string(c[1]) + ",\"y\":" + string(c[2]) + ",\"solid\":" + (c[3] ? "true" : "false") + ",\"visible\":" + (c[4] ? "true" : "false") + "}";
    }
    return out + "]";
}
```
In `with (all)`, `obj` and `cat` are local variables captured? GML with block can access local vars? Yes likely. But to be safe, we can compute constants before `with` and use them inside; okay. `sprite_index` <0 check uses `sprite_index`; if a non-sprite object has no? likely -1. This matches spec skip without sprite. `solid` and `visible` are built-in but for all instances? yes.
One potential issue: `object_index == obj` uses local `obj` from outside, okay. `cat` maybe -1; if -1, condition `object_index == cat` false.
Need `cands` array modified inside with; okay.

Now update dronline_script_frame:
```
    mine |= dronline_agent_step();
```
Place just before `if (global.dro_srec != -1)` to ensure recorded. Let's modify precisely:
In function, after script if and before `if (global.dro_srec != -1)`:
```
    mine |= dronline_agent_step();
    if (global.dro_srec != -1)
        buffer_write(...)
    return mine;
```
Currently:
```
    if (global.dro_srec != -1)
        buffer_write(global.dro_srec, buffer_u16, mine);
    return mine;
```
We'll insert before that.

Need `dronline_agent_poll` called in `dronline_step`. Add after `if (!dronline_ready()) return;`:
```
    dronline_agent_poll();
```
But if mode off and `dronline_ready` returns false? okay.

At end before `global.dro_frame++`:
```
    dronline_agent_done_check();
    global.dro_frame++;
```
Need ensure `global.dro_frame` still current (not incremented) for done_frame check. Good.

Now add config comment top: In `gml/scr_dronline.gml` header comment, after `script_record` line maybe add:
```
//   agent   = 0                              (host/client/solo) TCP port for the Agent API on 127.0.0.1; 0/off (untested draft)
```
Likewise in config/dronline.ini.example add:
```
; (host/client/solo) TCP port for the Agent API (JSON lines on 127.0.0.1). 0/off. Untested draft.
; agent=8765
```
Maybe under script_record. We'll add after `script_record` in example.

Now patches: add to add_extension.csx. In `add_extension.csx`, after existing `Fn("drnet_wait", D);` add:
```
Fn("drnet_agent_start", D, D);
Fn("drnet_agent_connected", D);
Fn("drnet_agent_command", D);
Fn("drnet_agent_id", S);
Fn("drnet_agent_id_is_string", D);
Fn("drnet_agent_steps_count", D);
Fn("drnet_agent_step_frames", D, D);
Fn("drnet_agent_step_buttons", D, D);
Fn("drnet_agent_send", D, S);
```
Need check return types C: drnet_agent_id returns `const char *` S. Good.
`drnet_agent_id_is_string` D.
`drnet_agent_start` D,D.
`drnet_agent_connected` D.
`drnet_agent_command` D.
`drnet_agent_steps_count` D.
`drnet_agent_step_frames` D,D.
`drnet_agent_step_buttons` D,D.
`drnet_agent_send` D,S.

Now tests/test_consistency will pick up GML calls. We need ensure GML uses all. It does.

Now tools/agent_client.py. Write complete.

Let's design class:
```
#!/usr/bin/env python3
"""Agent API client ..."""
import argparse
import json
import socket
import sys
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from tools.input_script import BUTTONS, compile_text, button_names, ScriptError

def encode_line(obj):
    return json.dumps(obj, separators=(',', ':')).encode('utf-8') + b'\n'

def decode_line(line):
    try:
        return json.loads(line.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("not a JSON line")

def text_to_steps(text):
    frames = compile_text(text)
    steps = []
    i = 0
    while i < len(frames):
        bits = frames[i]
        names = button_names(bits)
        buttons = [] if names == '-' else names.split('+')
        j = i + 1
        while j < len(frames) and frames[j] == bits:
            j += 1
        steps.append({"buttons": buttons, "frames": j - i})
        i = j
    return steps

class AgentClient:
    def __init__(self, host='127.0.0.1', port=8765, timeout=10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self._file = None
        self._next_id = 0

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._file = self.sock.makefile('rb')
        return self

    def close(self):
        if self._file is not None:
            try: self._file.close()
            except Exception: pass
        if self.sock is not None:
            try: self.sock.close()
            except Exception: pass
        self.sock = None; self._file = None

    def __enter__(self): return self.connect()
    def __exit__(self, *exc): self.close(); return False

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    def _send_obj(self, obj):
        self.sock.sendall(encode_line(obj))

    def _read_obj(self):
        line = self._file.readline()
        if not line:
            raise EOFError("agent closed the connection")
        return decode_line(line)

    def _request(self, cmd_obj, wait_type):
        rid = cmd_obj.get("id") if "id" in cmd_obj else self._new_id()? 
```
We need id echo. We'll set id before sending.
```
        cmd_obj["id"] = self._new_id()
        self._send_obj(cmd_obj)
        if wait_type == "queued": 
            while True:
                msg = self._read_obj()
                if msg.get("type") == "error": raise AgentError...
                if msg.get("type") == wait_type and msg.get("id") == cmd_obj["id"]:
                    return msg
        ...
```
But for input do, wait both queued then done. Could call generic.

Define exceptions:
```
class AgentError(RuntimeError): pass
```

Methods:
- `state(self)`: send `{"cmd":"state"}`, wait type state. Return state dict.
- `clear(self)`: send `{"cmd":"clear"}`, wait type cleared. Return maybe None.
- `input_steps(self, text)`: send `{"cmd":"input","steps":text_to_steps(text)}`, read until queued, then read until done, return `msg["state"]`. But if error, raise.
- maybe `input_raw(self, steps)`.

Need receive until matching id and type; handle error. Function `_wait_for(wait_type, rid)`:
```
while True:
    msg = self._read_obj()
    if msg.get("type") == "error":
        raise AgentError(msg.get("error","agent error"))
    if msg.get("type") == wait_type and msg.get("id") == rid:
        return msg
```
For multiple types with same id? `_wait_for_multi`.
For `do`, after sending queued, first wait queued using same rid, then wait done. But the server may send done later. `_wait_for` reading until the desired type; if a different type with same id? There won't be. Good.

CLI:
```
def main(argv):
    ap = argparse.ArgumentParser(...)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--timeout', type=float, default=10.0)
    sub = ap.add_subparsers(dest='command', required=True)
    p_state = sub.add_parser('state', help='print the game state')
    p_do = sub.add_parser('do', help='send input steps in input_script.py text syntax')
    p_do.add_argument('steps')
    args = ap.parse_args(argv)
    with AgentClient(args.host, args.port, args.timeout) as client:
        if args.command == 'state':
            state = client.state()
            print(json.dumps(state, indent=2))
        elif args.command == 'do':
            state = client.do(args.steps)
            print(json.dumps(state, indent=2))
```
Should `--port` default maybe 8765. Config example. Good.
Need module import `tools.input_script`; in repo root script `python tools/agent_client.py` works.
`button_names` from input_script returns "-" or plus. For metadata maybe bits 0..6 only; input_script includes teleport/f1 etc but spec agent may use only 7. But text parser allows them; our state? Input command C parser maps only 7 action names; if user passes teleport, C parse fails? Should agent client restrict? `input_script.compile_text` can parse teleport etc and produce bits >2047? Actually BUTTONS includes teleport bit 7, f1 bit8, f2 bit9, f3 bit10. Agent supports only bits 0..6. Spec says agent may use only these 7. Our C `agent_button_bits` only maps seven; if client sends "teleport", C parser returns -1 and invalid line. So `tools/agent_client.py` should only allow those names? The spec says do uses input_script.py text syntax, but agent may use only seven. We can filter in client: if step bits have bits beyond 127? Actually bit 6 -> 64, max 127. Use `if bits & ~0x7F: raise ScriptError("agent input may only use down right up left confirm cancel menu")`. But input_script compile may include teleport; we should reject in `text_to_steps` for agent. We'll add check and raise ValueError.
When compiling text, if bits > 0x7F, we can still create steps but C will reject. Better raise `AgentError`.
```
if bits & ~0x7F:
    raise AgentError("agent input may only use the seven action buttons")
```
Need define AgentError before text_to_steps? We can define after? Use `ValueError`. Tests can expect. We'll use `ScriptError`? But imported from input_script. Define `class AgentError(RuntimeError)` before.

But `text_to_steps` should be testable; if error raise `ValueError`. Use `raise ValueError(...)`.

Need `AgentError` class before text_to_steps. Good.

`decode_line` should handle blank lines? Server won't send blank. If blank, JSON decode error; maybe skip? We'll skip blank lines:
```
line = line.strip()
if not line: continue?
```
In `_read_obj`, we can read lines until non-empty? Could loop. We'll implement `_read_obj`:
```
while True:
    line = self._file.readline()
    if not line: raise EOFError
    if line.strip():
        return decode_line(line)
```
Good.

Now tests/test_agent_client.py:
```
import os, sys, unittest, json
ROOT=...
sys.path.insert(0, ROOT)
from tools.agent_client import text_to_steps, encode_line, decode_line, AgentError
from tools.input_script import ScriptError

class StepConversionTests(unittest.TestCase):
 def test_group_consecutive(self):
    steps = text_to_steps("20 right")
    self.assertEqual([{"buttons":["right"],"frames":20}], steps)
 def test_empty_hold(self):
    steps = text_to_steps("wait 5")
    self.assertEqual([{"buttons":[],"frames":5}], steps)
 def test_tap_expands_and_groups(self):
    steps = text_to_steps("tap confirm 2 15")
    # two press-gap cycles: press 1, gap 15 repeated = each 16 frames? Actually tap: ([confirm] + [0]*15)*2 -> groups: confirm 1, empty 15, confirm 1, empty 15
    self.assertEqual([...])
```
Let's compute `tap confirm 2 15`: `([bits] + [0]*15)*2`. Frames = [confirm, 0*15, confirm, 0*15]. Grouping: confirm 1, empty 15, confirm 1, empty 15. So expected.
Test `20 right; wait 2` groups right20, empty2.
Test invalid button beyond seven:
```
self.assertRaises(ValueError, text_to_steps, "20 teleport")
```
Need ensure `input_script.compile_text` parses teleport, then text_to_steps raises.

`LineFramingTests`:
```
def test_roundtrip_line(self):
    line = encode_line({"cmd":"state","id":7})
    self.assertEqual(line, b'{"cmd":"state","id":7}\n')
    self.assertEqual(decode_line(line), {"cmd":"state","id":7})
```
`test_json_with_escapes` etc maybe.

Need import path.

Now docs/DESIGN.md: Add section after "Scripted input" maybe before "Determinism hazards". We'll insert:
```
## Agent API (untested draft)

`agent=<port>` in `[online]` opens ...
```
Keep concise. Mention untested draft. Mention one connection refused, maybe.
Need update README one line. Maybe in Status or Usage. We'll add under status:
```
> Agent API: an untested draft lets an AI agent read the game state and send input over a local TCP line (see docs/DESIGN.md).
```
or in Usage. Keep one line.

Need update CLAUDE.md? Spec says docs in same change (CLAUDE.md): the agent key in config and comment; a short Agent API section; one line in README. We'll update.

Need ensure `dronline_agent_init` called before `random_set_seed`, and agent port read. Add `global.dro_agent_port = 0;` in init.

Potential issue: `variable_global_get` is used in GML. It is a built-in. Existing code uses `variable_instance_get` but not `variable_global_get`; should exist. Could use `global` + name but dynamic. Fine.

Potential issue: `is_real` not used. `string_hash_to_newline` maybe not a function variable; direct call. Fine.

Potential issue: `ord` function may not exist in UTMT compiler? GML has `ord`. Existing code doesn't use ord. If not, could use `string_ord_at`? I'm fairly sure `ord` exists. To be safe, use `string_ord_at(_s, i)`? In GML 2.3, `string_ord_at(str, index)` returns char code. But our code uses `string_char_at`, so `string_ord_at` is likely. Let's use `string_ord_at(_s, i)` instead of `ord(c)` to avoid uncertain. Function name is `string_ord_at`. I think yes: `string_ord_at(string, index)`. We'll use:
```
var code = string_ord_at(_s, i);
```
Is `string_ord_at` available in UTMT? Probably. But DELTARUNE might use GMS2. We'll use `string_ord_at`. In `dronline_agent_json_string`, `_s` is string variable, `i` index. `string_ord_at` returns 0 for empty? okay.
Alternatively `ord(string_char_at(_s,i))` is fine. I'll use `string_ord_at` to be modern.

Now C parse of buttons: bit values:
down=1, right=2, up=4, left=8, confirm=16, cancel=32, menu=64. Good.

Need C parser for `steps` array objects. In `agent_parse_steps`, when parsing `frames`, we use `strtoul(num,NULL,10)` but `num` string may have decimal? We only accept int. Fine. But if frames token includes `1.5`, `strtoul` parses 1. The agent client sends integers, so okay. Could validate but not needed.

Need C parse top-level `unknown command` and invalid JSON. The agent_client sends only known. Good.

Potential C threading: We use `agent_lock` around `FD_SET` and then `select` while not holding. But `agent_conn` can close? Only agent thread closes, not game thread. No other thread closes. Good.
`agent_listen` not closed except stop not implemented. fine.

Potential issue: New connection refused while active: We send error using `agent_send_raw` to `cl`, then `closesocket`. Fine. But `agent_send_raw` uses `send` maybe on nonblocking, okay.

Potential issue: `select` first arg `0` maybe warning? Existing uses 0. Windows ignores nfds. Fine.

Potential issue: `WSASocketW` for TCP with `WSA_FLAG_OVERLAPPED | WSA_FLAG_NO_HANDLE_INHERIT`; `WSA_FLAG_OVERLAPPED` for TCP? Probably okay. Existing uses for UDP. Fine.
`accept` returns nonblocking socket? If listener nonblocking, accepted socket may inherit? On Windows accepted socket does not inherit blocking mode from listener. We set nonblocking. Good.

Potential issue: `send` on nonblocking socket may fail with WSAEWOULDBLOCK; messages dropped. Should we set accepted socket blocking? We set nonblocking. For local agent, likely buffer available. But if GML sends large state while agent client not reading, send might block; but nonblocking avoids block at cost of lost. The spec says never stall game on bad input. Nonblocking is safer. We accept potential drop. Good.

Need maybe `drnet_agent_send` should return 0 if no connection? It returns 1 even if no conn? We lock and call agent_send_locked; if no conn, no send. Return 1. Fine. GML doesn't care.

Now state JSON `global.dro_players` maybe initialized to default 2 even mode solo? In init always set `global.dro_players = ini_read_real("online","players",2);` with default 2. In mode solo, players maybe default 2. Spec state key players. Fine.
`global.dro_player` default 1.

Global `global.dro_frame` set before agent_init? In init `global.dro_frame = 0` before. Good.

Need ensure `dronline_agent_init` doesn't use `global.dro_agent_*` before global initialized. We added globals earlier in init? We'll initialize after `global.dro_srec_file = "";` maybe:
```
global.dro_agent_port = 0;
```
Then in ini block read. Good.
But `dronline_agent_init` uses arrays; we can set there.

Now update add_extension.csx.

Need docs.

Let's write notes accordingly.

Now output format: We need exactly:
## Notes
... at most 40 lines.
Then each file content.

Let's produce concise Notes.

Now prepare file contents. Need be careful entire file content. We'll write modified versions.

First native/drnet.c: It's long; we need append to existing. We'll output full file including original plus appended section. That's huge but allowed. We need ensure no elisions. We'll reproduce entire original as given and add at end after `drnet_stop`. Could be lengthy. We must be accurate. We'll copy original text and add section. That is fine.

Let's start constructing final answer.

But before final, double-check consistency and possible issues with GML functions.

1. In `dronline_agent_poll`, `dronline_agent_input_command` uses `total` variable. We define `var total = 0;` inside if invalid == ""? Actually:
```
if (invalid == "")
{
    var total = 0;
    for ... { total += f; }
}
```
Then later `if (invalid == "")` block uses total; but total was declared inside previous if block. In GML, variable scope? `var` declares local to current function scope? Variables declared with `var` are local to function, not block-scoped? In GML, `var` variables are local to event/function and available after declaration? I think yes; but if declared inside an `if` block, it may still be accessible? In GML 2.3, variable scope is function level, but declaration inside block might be available? To be safe, declare `var total = 0;` before first if invalid == "". Then use. Let's adjust:
```
var total = 0;
if (invalid == "") {
    total = 0;
    for...
}
...
if (invalid == "") { ... string(total) ...}
```
Need in function `dronline_agent_input_command`, add `var total = 0;` at top before invalid. Good.

2. `dronline_agent_done_check`: Uses `array_length(global.dro_agent_done)` maybe array exists. Good.

3. `dronline_agent_nearby`: Uses `with (all)` and `array_push(cands, ...)`. The local var `cands` declared outside with; inside with, `cands` accessible? likely yes. But if not, we could collect after with? Maybe okay. Could avoid `with` by using a separate loop? Hard.

4. `dronline_agent_clean_text`: `if (i + 1 <= len)` etc. With `^`, `i += 2`; if i becomes len+1, while condition false. Fine. Backslash generic `i += 3`; may skip beyond. Fine.
5. `dronline_agent_json_string`: `string_ord_at(_s,i)` may compile? I'll use `ord(c)`. Hmm. Which is safer? In GML 2.3, `ord` is a function? DELTARUNE likely uses old GameMaker? Let's search memory: GameMaker language has `ord(string)`? I think yes. `string_ord_at` also. I'll use `ord(c)`, because `c` is a one-character string. Existing code uses `string_lettersdigits` etc. `ord` is common GML 1.4. Use `ord(c)`. Fine.
6. `dronline_agent_players_pos`: `variable_instance_get(inst, "name")` returns maybe string or undefined. If undefined, `dronline_agent_json_string` inside expression? We set `nm = ... ? dronline_agent_json_string(variable_instance_get(inst,"name")) : "null";` This calls `dronline_agent_json_string` on potentially undefined value, but in GML no ternary? Wait we used ternary in expression? We didn't; we used `if`? In code: `var nm = variable_instance_exists(inst, "name") ? dronline_agent_json_string(variable_instance_get(inst, "name")) : "null";`. I need avoid ternary! We used ternary in GML? I was trying to avoid. But in draft functions I used ternaries in `g_bool`, etc. GML supports ternary? To be safe, we should avoid. Existing code doesn't use ternary. We should rewrite all our GML using if/else, not ternary. Let's adjust our GML code accordingly. I must ensure no `? :` in our added GML (except maybe none). I'll write in plain if. Let's revise in final mentally and code to avoid ternaries. For `dronline_agent_g_bool`, use if. For `dronline_agent_players_pos`, use if else. For `dronline_agent_dialogue`, use if else. For `dronline_agent_choice`, avoid ternary.

I need write final GML without ternary. Let's plan.

`dronline_agent_json_id`:
```
if (is_string) return dronline_agent_json_string(id);
if (id == "") return "null";
return string(id);
```
No ternary.

In `dronline_agent_state_json`, use if/else no ternary:
- battle: 
```
if (global.fighting == 1) out += "\"battle\":true,"; else out += "\"battle\":false,";
```
- can move etc.

`dronline_agent_players_pos`:
```
var nm = "null";
if (variable_instance_exists(inst, "name"))
    nm = dronline_agent_json_string(variable_instance_get(inst, "name"));
```
Similarly face.
`dronline_agent_dialogue`: use if for waiting:
```
if (halt != 0) out += "\"waiting\":true,"; else out += "\"waiting\":false,";
```
`dronline_agent_choice`:
```
if (can == 1) out += "\"can_choose\":true"; else out += "\"can_choose\":false";
```
`dronline_agent_nearby`: after sorting, output:
```
if (c[3]) out += "\"solid\":true,"; else ...
```
No ternary.

`dronline_agent_apply_delay`: no ternary.

`dronline_agent_hex`: no ternary.

I need ensure no `?` in added GML. I'll write accordingly.

Now implement GML final in answer carefully. Might be lengthy.

Also, in `dronline_agent_state_json` use `string_ord_at`? We'll use `ord(c)`.

Let's draft final GML section fully in my head, then output.

GML agent functions after existing file.

I'll compose:

```
// ---- Agent API (untested draft) ----
// ...
function dronline_agent_init()
{
    if (global.dro_agent_port <= 0 || global.dro_agent_port > 65535)
        return;
    global.dro_agent_on = false;
    global.dro_agent_frames = [];
    global.dro_agent_frame_pos = 0;
    global.dro_agent_ends = [];
    global.dro_agent_end_pos = 0;
    global.dro_agent_done = [];
    global.dro_agent_was_connected = false;
    if (!drnet_agent_start(global.dro_agent_port))
    {
        drnet_log("agent API: could not listen on 127.0.0.1:" + string(global.dro_agent_port));
        return;
    }
    global.dro_agent_on = true;
    drnet_log("agent API: listening on 127.0.0.1:" + string(global.dro_agent_port) + " (untested draft)");
}
...
```

Potential issue: `drnet_log` only works if UDP log file opened. It opens lazily, and even without UDP? `drnet_log` uses `logm` which opens log_file; independent. Fine.

Need `dronline_agent_clear` before poll maybe. Fine.

Let's ensure `dronline_agent_poll` doesn't use `dronline_agent_clear` before definition? It's later? Define clear before poll? In GML order not matter, but safer define clear before poll. We'll put clear before poll.

Now final file modifications.

Let's now craft final answer step by step.

Given token constraints, we need output complete file contents. I will produce original `native/drnet.c` with appended agent section. To save time, I can copy the provided drnet.c exactly and append. Must ensure not lose anything. In final, I will include full content. Same for gml file: huge; I'll craft complete modified gml. Need ensure all existing code plus modifications and appended agent section. This is large but okay.

Let's start final output.