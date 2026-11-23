// DeltaruneOnline: virtual input layer + lockstep driver.
// Imported by the patcher as gml_GlobalScript_scr_dronline.
// The patcher rewrites every keyboard/gamepad read in the game and the mod to the dro_* functions below.
// This file itself is NOT rewritten, so it can still read the real devices.
//
// Config: dronline.ini in the game's save folder (%LOCALAPPDATA%\DELTARUNE), or dronline_<role>.ini
// when the environment variable DRONLINE_ROLE=<role> is set and that file exists (two copies on one PC).
//   [online]
//   mode   = off | solo | record | replay | host | client
//   players = 2 | 3                            (host/client) number of players; also the Local Multiplayer player count
//   host   = 192.168.1.10                      (client) host address
//   port   = 7845                              (host/client) UDP port, the host must have it open
//   delay  = auto                              (host/client) input delay in frames: auto = from the measured ping, so the game
//                                              never waits; your own SOUL is still drawn without delay. A number fixes it.
//   maxdelay = 6                               (host/client) upper limit for delay=auto
//   lag    = 0                                 (testing) extra one-way latency in ms added to every packet this machine sends
//   jitter = 0                                 (testing) random extra 0..jitter ms per packet
//   player = 1 | 2 | 3                         local player
//   seed   = 1234                              shared random seed
//   pad    = -1                                local gamepad index, -1 = first connected
//   script = dronline_script.bin               (host/client/solo) take the local player's input from this file instead of
//                                              the keyboard and gamepad: one little-endian u16 of action bits per input
//                                              frame, written by script_record or by tools/input_script.py from a text script
//   script_end = idle | keys | quit            what follows the last scripted frame: no input (default), the real
//                                              keyboard and gamepad, or closing the game (the others see "left")
//   script_record = dronline_rec.bin           (host/client/solo) write the local player's input of every frame to this
//                                              file, to replay it later with script=. An existing file is kept: the
//                                              recording then goes to <name>.2, <name>.3, ...
// With no file (or mode=off) the game behaves exactly like the unpatched one.

function dronline_init()
{
    global.dro_mode = "off";
    global.dro_debug = 0;
    global.dro_player = 1;
    global.dro_seed = 1234;
    global.dro_pad = -1;
    global.dro_delay = -1;
    global.dro_maxdelay = 6;
    global.dro_lag = 0;
    global.dro_jitter = 0;
    global.dro_script_file = "";
    global.dro_script_end = "idle";
    global.dro_srec_file = "";
    // Two copies on one PC share the save folder. With the environment variable DRONLINE_ROLE=<name>
    // (letters and digits, at most 16), a copy reads dronline_<name>.ini instead, if that file exists.
    // drnet.dll follows the same rule for its log (drnet_<name>.log). See tools/local_test.bat.
    global.dro_ini = "dronline.ini";
    var role = environment_get_variable("DRONLINE_ROLE");
    if (role != "" && string_length(role) <= 16 && string_lettersdigits(role) == role && file_exists("dronline_" + role + ".ini"))
        global.dro_ini = "dronline_" + role + ".ini";
    if (file_exists(global.dro_ini))
    {
        ini_open(global.dro_ini);
        global.dro_mode = string_lower(ini_read_string("online", "mode", "off"));
        global.dro_player = clamp(ini_read_real("online", "player", 1), 1, 3);
        global.dro_seed = ini_read_real("online", "seed", 1234);
        global.dro_pad = ini_read_real("online", "pad", -1);
        global.dro_players = ini_read_real("online", "players", 2);
        global.dro_host = ini_read_string("online", "host", "127.0.0.1");
        global.dro_port = ini_read_real("online", "port", 7845);
        var d = string_lower(ini_read_string("online", "delay", "auto"));
        global.dro_delay = (d == "auto" || string_digits(d) == "") ? -1 : real(string_digits(d));
        global.dro_maxdelay = ini_read_real("online", "maxdelay", 6);
        global.dro_lag = ini_read_real("online", "lag", 0);
        global.dro_jitter = ini_read_real("online", "jitter", 0);
        global.dro_debug = ini_read_real("online", "debug", 0);
        global.dro_script_file = ini_read_string("online", "script", "");
        global.dro_script_end = string_lower(ini_read_string("online", "script_end", "idle"));
        global.dro_srec_file = ini_read_string("online", "script_record", "");
        ini_close();
    }
    global.dro_on = (global.dro_mode != "off");
    global.dro_frame = 0;
    global.dro_held = array_create(256, 0);
    global.dro_prev = array_create(256, 0);
    global.dro_supp = array_create(256, 0);
    global.dro_bits = array_create(4, 0);
    global.dro_rec = -1;
    global.dro_rec_pos = 0;
    global.dro_script = -1;
    global.dro_script_pos = 0;
    global.dro_script_done = false;
    global.dro_srec = -1;
    global.dro_hashlog = -1;
    global.dro_net = false;
    global.dro_connecting = false;
    global.dro_leaving = false;
    global.dro_restarting = false;
    global.dro_netframe = 0;
    global.dro_desync = -1;
    global.dro_msg = "";
    global.dro_lagf = array_create(4, 0);
    global.dro_hit = array_create(4, 0);
    global.dro_px = 0;
    global.dro_py = 0;
    global.dro_swap = 0;
    global.dro_sx = 0;
    global.dro_sy = 0;
    global.dro_obj_heart = asset_get_index("obj_heart");
    global.dro_obj_bullet = asset_get_index("obj_collidebullet");
    global.dro_obj_solid = asset_get_index("obj_battlesolid");
    global.dro_obj_purple = asset_get_index("obj_purplecontrols");
    if (!global.dro_on)
        return;
    if (global.dro_mode == "host" || global.dro_mode == "client")
    {
        if (global.dro_mode == "host")
            global.dro_player = 1;
        drnet_set_host(global.dro_host);
        drnet_option("maxdelay", global.dro_maxdelay);
        if (global.dro_lag > 0)
            drnet_option("lag", global.dro_lag);
        if (global.dro_jitter > 0)
            drnet_option("jitter", global.dro_jitter);
        if (!drnet_start(global.dro_mode == "host", global.dro_port, global.dro_players, global.dro_player, global.dro_delay))
        {
            var why = drnet_status();
            drnet_stop();
            show_message("DeltaruneOnline: could not connect (" + global.dro_mode + "): " + why + ". Starting offline. Details in drnet.log in the save folder.");
            global.dro_mode = "off";
            global.dro_on = false;
            return;
        }
        // The session decides the Local Multiplayer player count, not each machine's keyconfig_mod.ini
        // (the game read it at startup; later reads go through dro_ini_player_amount).
        global.playeramount = global.dro_players;
        // Connect on the next frame: frame 0 runs without input on every machine (so they stay identical)
        // and draws the "waiting" text, then frame 1 blocks until everybody is there.
        global.dro_net = true;
        global.dro_connecting = true;
        drnet_log("init: network started, room " + room_get_name(room) + ", config " + global.dro_ini);
        if (global.dro_debug)
        {
            // hits travel as plain instance numbers: check that the runtime accepts them as ids
            var nid = real(id);
            var ok = false;
            with (nid)
                ok = true;
            drnet_log("id test: " + string(nid) + " exists " + string(instance_exists(nid)) + " with " + string(ok));
        }
        global.dro_netframe = 0;
        global.dro_msg = drnet_status();
    }
    random_set_seed(global.dro_seed);
    if (global.dro_mode == "host" || global.dro_mode == "client" || global.dro_mode == "solo")
        dronline_script_init();
    if (global.dro_mode == "record")
    {
        global.dro_rec = buffer_create(4096, buffer_grow, 1);
        global.dro_hashlog = file_text_open_write("dronline_hash_record.txt");
    }
    else if (global.dro_mode == "replay")
    {
        if (file_exists("dronline_input.bin"))
            global.dro_rec = buffer_load("dronline_input.bin");
        global.dro_hashlog = file_text_open_write("dronline_hash_replay.txt");
    }
}

function dronline_ready()
{
    if (!variable_global_exists("dro_mode"))
        dronline_init();
    return global.dro_on;
}

// ---- local devices -> action bits of the local player ----
// bit 0 down, 1 right, 2 up, 3 left, 4 confirm, 5 cancel, 6 menu,
// bit 7 teleport (mod), 8..10 F1..F3 player amount (mod)
function dronline_local_bits()
{
    var b = 0;
    if (!variable_global_exists("input_k"))
        return 0;
    for (var a = 0; a < 10; a++)
    {
        var k = global.input_k[a];
        if (keyboard_check(k))
            b |= (1 << dronline_action_of(a));
    }
    for (var e = 0; e < 4; e++)
    {
        if (array_length(global.input_k) > (800 + e) && keyboard_check(global.input_k[800 + e]))
            b |= (1 << (7 + e));
    }
    var dev = global.dro_pad;
    if (dev < 0)
    {
        for (var i = 0; i < 12; i++)
        {
            if (gamepad_is_connected(i))
            {
                dev = i;
                break;
            }
        }
    }
    if (dev >= 0 && gamepad_is_connected(dev))
    {
        for (var a = 0; a < 10; a++)
        {
            if (array_length(global.input_g) > a && gamepad_button_check(dev, global.input_g[a]))
                b |= (1 << dronline_action_of(a));
        }
        var ax = gamepad_axis_value(dev, gp_axislh);
        var ay = gamepad_axis_value(dev, gp_axislv);
        if (ay > 0.5) b |= 1;
        if (ax > 0.5) b |= 2;
        if (ay < -0.5) b |= 4;
        if (ax < -0.5) b |= 8;
    }
    return b;
}

// ---- scripted input: unattended tests, and replaying a recorded session ----
// A script covers one launch of the chapter: after a chapter change the new game process starts it again.
function dronline_script_init()
{
    if (global.dro_script_file != "")
    {
        if (file_exists(global.dro_script_file))
        {
            global.dro_script = buffer_load(global.dro_script_file);
            drnet_log("script " + global.dro_script_file + ": " + string(buffer_get_size(global.dro_script) div 2) + " frames, then " + global.dro_script_end);
        }
        else
        {
            drnet_log("script " + global.dro_script_file + " not found, using the keyboard and gamepad");
        }
    }
    if (global.dro_srec_file != "")
    {
        var f = global.dro_srec_file;
        for (var n = 2; file_exists(f) && n < 100; n++)
            f = global.dro_srec_file + "." + string(n);
        if (file_exists(f))
        {
            drnet_log("not recording: " + global.dro_srec_file + " and .2 to .99 all exist, and recordings are never overwritten");
        }
        else
        {
            global.dro_srec_file = f;
            global.dro_srec = buffer_create(4096, buffer_grow, 1);
            drnet_log("recording the local input to " + f);
        }
    }
    if ((global.dro_script != -1 || global.dro_srec != -1) && (global.dro_mode == "host" || global.dro_mode == "client") && global.dro_delay < 0)
        drnet_log("delay=auto: a script replays a recording frame for frame only with the same delay, so set delay=<frames> on every machine");
}

// The local input of one frame (devices or replay bits in, the bits to use out). Called once per input frame:
// every frame offline, every exchanged frame online.
function dronline_script_frame(mine)
{
    if (global.dro_script != -1)
    {
        if (global.dro_script_pos + 2 <= buffer_get_size(global.dro_script))
        {
            mine = buffer_peek(global.dro_script, global.dro_script_pos, buffer_u16) & 2047;
            global.dro_script_pos += 2;
        }
        else
        {
            if (!global.dro_script_done)
            {
                global.dro_script_done = true;
                drnet_log("script finished at frame " + string(global.dro_frame) + " (input frame " + string(global.dro_script_pos div 2) + "), then " + global.dro_script_end);
                // the normal way out: Game End says goodbye (dronline_shutdown), so the others see "left" at once
                if (global.dro_script_end == "quit")
                    game_end();
            }
            if (global.dro_script_end != "keys")
                mine = 0;
        }
    }
    if (global.dro_srec != -1)
        buffer_write(global.dro_srec, buffer_u16, mine);
    return mine;
}

// P1 slots 7..9 are the alternate confirm/cancel/menu keys
function dronline_action_of(a)
{
    if (a >= 7)
        return a - 3;
    return a;
}

// ---- action bits of every player -> virtual keyboard ----
function dronline_apply_bits()
{
    if (!variable_global_exists("input_k"))
        return;
    array_copy(global.dro_prev, 0, global.dro_held, 0, 256);
    for (var i = 0; i < 256; i++)
        global.dro_held[i] = 0;
    for (var p = 1; p <= 3; p++)
    {
        var bits = global.dro_bits[p];
        if (bits == 0)
            continue;
        var base = (p == 1) ? 0 : ((p == 2) ? 200 : 300);
        for (var a = 0; a < 6; a++)
        {
            if (bits & (1 << a))
                dronline_press(global.input_k[base + a]);
        }
        if ((bits & 64) && p == 1)
            dronline_press(global.input_k[6]);
        for (var e = 0; e < 4; e++)
        {
            if ((bits & (1 << (7 + e))) && array_length(global.input_k) > (800 + e))
                dronline_press(global.input_k[800 + e]);
        }
    }
    for (var i = 0; i < 256; i++)
    {
        if (!global.dro_held[i])
            global.dro_supp[i] = 0;
    }
}

function dronline_press(k)
{
    if (k >= 0 && k < 256)
        global.dro_held[k] = 1;
}

// ---- called first in obj_time Begin Step, once per frame ----
function dronline_step()
{
    if (!dronline_ready())
        return;
    global.dro_restarting = false;
    dronline_heart_unswap();
    for (var p = 0; p < 4; p++)
        global.dro_bits[p] = 0;
    var mine = dronline_local_bits();
    if (global.dro_mode == "replay")
    {
        mine = 0;
        if (global.dro_rec != -1 && global.dro_rec_pos + 2 <= buffer_get_size(global.dro_rec))
        {
            mine = buffer_peek(global.dro_rec, global.dro_rec_pos, buffer_u16);
            global.dro_rec_pos += 2;
        }
    }
    else if (global.dro_mode == "record")
    {
        buffer_write(global.dro_rec, buffer_u16, mine);
    }
    if (!global.dro_net)
        mine = dronline_script_frame(mine);
    global.dro_bits[global.dro_player] = mine;
    if (global.dro_net && global.dro_frame < 5)
        drnet_log("step frame " + string(global.dro_frame) + " room " + room_get_name(room) + " connecting " + string(global.dro_connecting));
    if (global.dro_net && global.dro_connecting)
    {
        if (global.dro_frame == 0)
            mine = 0;
        else if (!drnet_connect(120000))
        {
            var why = drnet_status();
            drnet_stop();
            global.dro_net = false;
            global.dro_connecting = false;
            show_message("DeltaruneOnline: could not connect (" + global.dro_mode + "): " + why + ". Closing the game.");
            game_end();
            return;
        }
        else
        {
            global.dro_connecting = false;
            drnet_log("connected at frame " + string(global.dro_frame));
        }
        global.dro_bits[global.dro_player] = mine;
    }
    if (global.dro_net && !global.dro_connecting)
    {
        // the script and the recording count exchanged frames, so waiting for the others does not use them up
        mine = dronline_script_frame(mine);
        drnet_set_aux(dronline_detect_hit());
        var _all = drnet_exchange(global.dro_netframe, mine);
        global.dro_netframe++;
        if (_all < 0)
        {
            // -2: another player said goodbye (closed the game) and their input ran out; -1: no answer for 10 s
            var why = drnet_status();
            drnet_stop();
            global.dro_net = false;
            if (global.dro_leaving)
            {
                // this game is changing chapter too (dro_game_change), so the others leaving is expected
                drnet_log("network closed while changing chapter at frame " + string(global.dro_frame));
                return;
            }
            if (_all == -2)
                show_message("DeltaruneOnline: " + why + " (frame " + string(global.dro_frame) + "). Closing the game.");
            else
                show_message("DeltaruneOnline: connection lost at frame " + string(global.dro_frame) + ".");
            game_end();
            return;
        }
        for (var p = 1; p <= 3; p++)
        {
            var raw = _all % 65536;
            _all = floor(_all / 65536);
            global.dro_bits[p] = raw & 2047;
            global.dro_lagf[p] = (raw >> 11) & 1;
            global.dro_hit[p] = (p <= global.dro_players) ? drnet_aux(p) : 0;
        }
    }
    dronline_apply_bits();
    if (global.dro_net && (global.dro_frame % 300) == 0 && global.dro_frame > 0)
        drnet_log("frame " + string(global.dro_frame) + " room " + room_get_name(room) + " desync " + string(global.dro_desync) + " ping " + string(round(drnet_ping())) + " delay " + string(drnet_delay()) + " wait " + string_format(drnet_wait(), 1, 1));
    if ((global.dro_hashlog != -1 || global.dro_net) && (global.dro_frame % 30) == 0)
        dronline_write_hash();
    if ((global.dro_mode == "record" || global.dro_srec != -1) && (global.dro_frame % 300) == 299)
        dronline_flush();
    // Do not close the network here: DELTARUNE fires Game End when it restarts itself at chapter start
    // (room_gms_debug_failsafe -> ROOM_INITIALIZE), which closed the socket before the first connect.
    // The socket is closed by the system when the game really exits.
    global.dro_frame++;
}

// Cheap state fingerprint for desync checks: frame, room, instances, summed positions.
function dronline_write_hash()
{
    var sx = 0;
    var sy = 0;
    with (all)
    {
        sx += floor(x);
        sy += floor(y);
    }
    var plot = variable_global_exists("plot") ? global.plot : -1;
    if (global.dro_debug && global.dro_frame <= 2400)
        dronline_dump_state();
    if (global.dro_hashlog != -1)
    {
        file_text_write_string(global.dro_hashlog, string(global.dro_frame) + " " + string(room) + " " + string(instance_count) + " " + string(sx) + " " + string(sy) + " " + string(plot));
        file_text_writeln(global.dro_hashlog);
    }
    if (global.dro_net)
    {
        var h = abs(((global.dro_frame * 31 + room * 131 + instance_count * 7 + sx * 3 + sy + plot * 17) mod 2147483647));
        drnet_hash(global.dro_frame, h);
        // the parts, so that after a desync the two logs show at once whether the room, the instances or a position differ
        if (global.dro_debug || global.dro_frame <= 2400)
            drnet_log("hash frame " + string(global.dro_frame) + " " + room_get_name(room) + " instances " + string(instance_count) + " x " + string(sx) + " y " + string(sy) + " plot " + string(plot) + " = " + string(h));
        if (global.dro_desync < 0)
            global.dro_desync = drnet_desync();
    }
}

// debug=1: list every instance at each checkpoint (first 2400 frames) to find what differs after a desync.
function dronline_dump_state()
{
    var f = file_text_open_append("dronline_state.txt");
    file_text_write_string(f, "== frame " + string(global.dro_frame) + " room " + room_get_name(room));
    file_text_writeln(f);
    with (all)
    {
        file_text_write_string(f, object_get_name(object_index) + " " + string(floor(x)) + " " + string(floor(y)) + " " + string(floor(depth)));
        file_text_writeln(f);
    }
    file_text_close(f);
}

// Small status line, drawn from obj_time's Draw GUI (hooked by the patcher).
function dronline_draw()
{
    if (!variable_global_exists("dro_mode") || !global.dro_on)
        return;
    if (global.dro_net && global.dro_connecting)
    {
        draw_set_color(c_black);
        draw_rectangle(0, 0, display_get_gui_width(), display_get_gui_height(), false);
        draw_set_color(c_white);
        draw_set_halign(fa_center);
        draw_text(display_get_gui_width() / 2, display_get_gui_height() / 2 - 10, "Waiting for other players...");
        draw_set_halign(fa_left);
        return;
    }
    var txt = "ONLINE " + global.dro_mode;
    if (global.dro_net)
        txt += " | " + drnet_status();
    if (global.dro_script != -1)
        txt += global.dro_script_done ? " | script done" : (" | script " + string(global.dro_script_pos div 2) + "/" + string(buffer_get_size(global.dro_script) div 2));
    if (global.dro_srec != -1)
        txt += " | REC";
    if (global.dro_desync >= 0)
        txt += " | DESYNC at frame " + string(global.dro_desync);
    draw_set_alpha(0.8);
    draw_set_color((global.dro_desync >= 0) ? c_red : c_white);
    draw_text(4, 4, txt);
    draw_set_alpha(1);
    draw_set_color(c_white);
}

// Writes the recorded input when the game ends (called from obj_time Game End / Clean Up by the patcher).
function dronline_flush()
{
    if (global.dro_mode == "record" && global.dro_rec != -1)
        buffer_save_ext(global.dro_rec, "dronline_input.bin", 0, buffer_tell(global.dro_rec));
    if (global.dro_srec != -1)
        buffer_save_ext(global.dro_srec, global.dro_srec_file, 0, buffer_tell(global.dro_srec));
}

function dronline_shutdown()
{
    if (!variable_global_exists("dro_mode"))
        return;
    // Game End: the window was closed, Esc was held or game_end ran, so tell the others now, while the game still
    // runs (a goodbye sent by the DLL at process exit never arrived). Not on game_restart: the game goes on.
    // The socket stays open: DELTARUNE also fires Game End when it restarts itself at chapter start.
    if (global.dro_net && !global.dro_restarting)
        drnet_bye("game end");
    dronline_flush();
    if (global.dro_hashlog != -1)
    {
        file_text_close(global.dro_hashlog);
        global.dro_hashlog = -1;
    }
}

// ---- replacements for the built-in input functions ----
// Keys that only affect this machine (hold Esc to quit, F4 fullscreen) read the real keyboard.
function dronline_local_key(k)
{
    return k == vk_escape || k == vk_f4;
}

function dro_kb_check(k)
{
    if (!dronline_ready() || dronline_local_key(k))
        return keyboard_check(k);
    if (k == vk_anykey)
    {
        for (var i = 0; i < 256; i++)
        {
            if (global.dro_held[i] && !global.dro_supp[i])
                return true;
        }
        return false;
    }
    if (k < 0 || k >= 256)
        return false;
    return global.dro_held[k] && !global.dro_supp[k];
}

function dro_kb_pressed(k)
{
    if (!dronline_ready() || dronline_local_key(k))
        return keyboard_check_pressed(k);
    if (k == vk_anykey)
    {
        for (var i = 0; i < 256; i++)
        {
            if (global.dro_held[i] && !global.dro_prev[i] && !global.dro_supp[i])
                return true;
        }
        return false;
    }
    if (k < 0 || k >= 256)
        return false;
    return global.dro_held[k] && !global.dro_prev[k] && !global.dro_supp[k];
}

function dro_kb_released(k)
{
    if (!dronline_ready() || dronline_local_key(k))
        return keyboard_check_released(k);
    if (k < 0 || k >= 256)
        return false;
    return !global.dro_held[k] && global.dro_prev[k];
}

function dro_kb_clear(k)
{
    if (!dronline_ready())
        return keyboard_clear(k);
    if (k >= 0 && k < 256)
        global.dro_supp[k] = 1;
}

function dro_io_clear()
{
    if (!dronline_ready())
        return io_clear();
    for (var i = 0; i < 256; i++)
        global.dro_supp[i] = global.dro_held[i];
}

// In online modes every gamepad is reported as disconnected: pads are read locally above
// and reach the game only as action bits on the virtual keyboard.
function dro_gp_connected(d)
{
    if (!dronline_ready())
        return gamepad_is_connected(d);
    return false;
}

function dro_gp_count()
{
    return gamepad_get_device_count();
}

function dro_gp_check(d, b)
{
    if (!dronline_ready())
        return gamepad_button_check(d, b);
    return false;
}

function dro_gp_pressed(d, b)
{
    if (!dronline_ready())
        return gamepad_button_check_pressed(d, b);
    return false;
}

function dro_gp_released(d, b)
{
    if (!dronline_ready())
        return gamepad_button_check_released(d, b);
    return false;
}

function dro_gp_axis(d, a)
{
    if (!dronline_ready())
        return gamepad_axis_value(d, a);
    return 0;
}

// Replaces game_change (patcher): the chapter select starts a new game process and this one exits. Every
// machine does that in the same frame, so goodbyes that arrive meanwhile must not show "player left".
function dro_game_change(_dir, _params)
{
    if (variable_global_exists("dro_net") && global.dro_net)
    {
        global.dro_leaving = true;
        drnet_bye("chapter change at frame " + string(global.dro_frame));
    }
    return game_change(_dir, _params);
}

// Replaces game_restart (patcher): it fires Game End, but the game goes on, so Game End must not say goodbye.
// Every machine restarts in the same frame; the flag is cleared again in the next dronline_step.
function dro_game_restart()
{
    if (variable_global_exists("dro_net") && global.dro_net)
    {
        global.dro_restarting = true;
        drnet_log("game_restart at frame " + string(global.dro_frame));
    }
    game_restart();
}

function dro_randomize()
{
    if (!dronline_ready())
        return randomize();
    random_set_seed(global.dro_seed);
}

// Replaces the mod's ini_read_real("PLAYER_AMOUNT", ...) (patcher). Online, every machine uses the session's
// player count from dronline.ini instead of its own keyconfig_mod.ini, so new game, loading a save and the
// config menu agree everywhere. The game has the ini open while it calls this, so no other file is touched.
function dro_ini_player_amount(_section, _key, _default)
{
    if (variable_global_exists("dro_net") && global.dro_net)
        return global.dro_players;
    return ini_read_real(_section, _key, _default);
}

// ---- virtual audio clock ----
// Music and sounds start with a different latency on every machine, and some game logic follows the
// playback position (the chapter 5 logo, rhythm sections). Online, positions and "is playing" are
// computed from the lockstep frame counter instead of the real audio device, so every machine agrees.
// The real sound still plays; only what the game logic reads is virtual.
// entry (array, always written back with ds_map_set):
//   [asset, start_frame, offset_s, paused_frame (-1), stopped, loop, length_s, pitch]
function dro_aud_map()
{
    if (!variable_global_exists("dro_aud"))
        global.dro_aud = ds_map_create();
    return global.dro_aud;
}

function dro_aud_add(sid, snd, loop, offset, pitch)
{
    if (sid >= 0)
        ds_map_set(dro_aud_map(), sid, [snd, global.dro_frame, offset, -1, 0, loop, audio_sound_length(snd), pitch]);
    return sid;
}

function dro_aud_pos(e)
{
    if (e[4])
        return 0;
    var f = (e[3] >= 0) ? e[3] : global.dro_frame;
    var t = e[2] + ((f - e[1]) / game_get_speed(gamespeed_fps)) * e[7];
    if (e[5] && e[6] > 0)
        t = t mod e[6];
    else if (e[6] > 0)
        t = min(t, e[6]);
    return t;
}

function dro_aud_playing(e)
{
    if (e[4])
        return false;
    if (e[5])
        return true;
    return dro_aud_pos(e) < e[6];
}

// keys of the entries matching an instance id, or of every instance of a sound asset
function dro_aud_keys(idx)
{
    var m = dro_aud_map();
    if (ds_map_exists(m, idx))
        return [idx];
    var out = [];
    var k = ds_map_find_first(m);
    while (!is_undefined(k))
    {
        var e = ds_map_find_value(m, k);
        if (e[0] == idx)
            array_push(out, k);
        k = ds_map_find_next(m, k);
    }
    return out;
}

function dro_audio_play_sound(snd, prio, loop, gain = 1, offset = 0, pitch = 1)
{
    var sid = audio_play_sound(snd, prio, loop, gain, offset, pitch);
    if (dronline_ready())
    {
        dro_aud_add(sid, snd, loop, offset, pitch);
        dro_aud_failed(sid, snd);
    }
    return sid;
}

function dro_audio_play_sound_on(emitter, snd, loop, prio, gain = 1, offset = 0, pitch = 1)
{
    var sid = audio_play_sound_on(emitter, snd, loop, prio, gain, offset, pitch);
    if (dronline_ready())
    {
        dro_aud_add(sid, snd, loop, offset, pitch);
        dro_aud_failed(sid, snd);
    }
    return sid;
}

// A sound that does not play on this machine (no audio device, or a music file not found) returns -1, and game
// logic that waits for its position stops here but not elsewhere. Example: Chapter 5 started without the
// "launcher" parameter looks for its music in chapter5_windows/mus/, and the logo never finishes.
function dro_aud_failed(sid, snd)
{
    if (sid >= 0 || !global.dro_net)
        return;
    drnet_log("sound " + string(snd) + " did not play at frame " + string(global.dro_frame) + " (audio device, or music file not found), so the game can go out of sync");
}

function dro_audio_get_pos(idx)
{
    if (!dronline_ready())
        return audio_sound_get_track_position(idx);
    var ks = dro_aud_keys(idx);
    if (array_length(ks) == 0)
        return 0;
    return dro_aud_pos(ds_map_find_value(dro_aud_map(), ks[0]));
}

function dro_audio_set_pos(idx, t)
{
    audio_sound_set_track_position(idx, t);
    if (!dronline_ready())
        return;
    var m = dro_aud_map();
    var ks = dro_aud_keys(idx);
    for (var i = 0; i < array_length(ks); i++)
    {
        var e = ds_map_find_value(m, ks[i]);
        e[2] = t;
        e[1] = global.dro_frame;
        if (e[3] >= 0)
            e[3] = global.dro_frame;
        ds_map_set(m, ks[i], e);
    }
}

function dro_audio_pitch(idx, p)
{
    audio_sound_pitch(idx, p);
    if (!dronline_ready())
        return;
    var m = dro_aud_map();
    var ks = dro_aud_keys(idx);
    for (var i = 0; i < array_length(ks); i++)
    {
        var e = ds_map_find_value(m, ks[i]);
        e[2] = dro_aud_pos(e);
        e[1] = global.dro_frame;
        if (e[3] >= 0)
            e[3] = global.dro_frame;
        e[7] = p;
        ds_map_set(m, ks[i], e);
    }
}

function dro_audio_is_playing(idx)
{
    if (!dronline_ready())
        return audio_is_playing(idx);
    var m = dro_aud_map();
    var ks = dro_aud_keys(idx);
    for (var i = 0; i < array_length(ks); i++)
    {
        if (dro_aud_playing(ds_map_find_value(m, ks[i])))
            return true;
    }
    return false;
}

function dro_audio_is_paused(idx)
{
    if (!dronline_ready())
        return audio_is_paused(idx);
    var ks = dro_aud_keys(idx);
    if (array_length(ks) == 0)
        return false;
    var e = ds_map_find_value(dro_aud_map(), ks[0]);
    return e[3] >= 0 && !e[4];
}

function dro_aud_set_paused(ks, paused)
{
    var m = dro_aud_map();
    for (var i = 0; i < array_length(ks); i++)
    {
        var e = ds_map_find_value(m, ks[i]);
        if (paused && e[3] < 0)
            e[3] = global.dro_frame;
        else if (!paused && e[3] >= 0)
        {
            e[1] += global.dro_frame - e[3];
            e[3] = -1;
        }
        ds_map_set(m, ks[i], e);
    }
}

function dro_audio_pause(idx)
{
    audio_pause_sound(idx);
    if (dronline_ready())
        dro_aud_set_paused(dro_aud_keys(idx), true);
}

function dro_audio_resume(idx)
{
    audio_resume_sound(idx);
    if (dronline_ready())
        dro_aud_set_paused(dro_aud_keys(idx), false);
}

function dro_aud_all_keys()
{
    var out = [];
    var m = dro_aud_map();
    var k = ds_map_find_first(m);
    while (!is_undefined(k))
    {
        array_push(out, k);
        k = ds_map_find_next(m, k);
    }
    return out;
}

function dro_audio_pause_all()
{
    audio_pause_all();
    if (dronline_ready())
        dro_aud_set_paused(dro_aud_all_keys(), true);
}

function dro_audio_resume_all()
{
    audio_resume_all();
    if (dronline_ready())
        dro_aud_set_paused(dro_aud_all_keys(), false);
}

function dro_audio_stop(idx)
{
    audio_stop_sound(idx);
    if (!dronline_ready())
        return;
    var m = dro_aud_map();
    var ks = dro_aud_keys(idx);
    for (var i = 0; i < array_length(ks); i++)
        ds_map_delete(m, ks[i]);
}

function dro_audio_stop_all()
{
    audio_stop_all();
    if (dronline_ready())
        ds_map_clear(dro_aud_map());
}

// ---- own SOUL: the local player sees and dodges with their heart without input delay ----
// With an input delay of D frames (automatic, from the ping), the shared simulation moves a heart D frames
// after its owner pressed the keys. Its owner instead sees the heart where it is going to be: the shared
// position plus the moves already sent for later frames (drnet_pending). Bullets hit a heart where its
// owner sees it: the owner's machine checks the collision and sends the bullet's instance number with its
// input, and every machine applies that hit in the same frame through the game's own hit code.
// Frames sent without delay (LAG_BIT clear) keep the game's normal collisions: there the shared
// position already is what the owner sees. Hooks in obj_heart come from the patcher (dro_heart_* below it).

function dronline_ownhit_mode()
{
    if (!dro_heart_hooked() || global.dro_obj_heart < 0 || global.dro_obj_bullet < 0)
        return false;
    if (global.dro_obj_purple >= 0 && instance_exists(global.dro_obj_purple))
        return false;
    var h = instance_find(global.dro_obj_heart, 0);
    if (h == noone)
        return false;
    if (variable_instance_exists(h, "inMode7") && h.inMode7)
        return false;
    return true;
}

// Does the owner of heart p decide its hits in this frame? Same answer on every machine.
function dronline_ownhit(p)
{
    if (!variable_global_exists("dro_net") || !global.dro_net || global.dro_connecting)
        return false;
    return global.dro_lagf[p] && dronline_ownhit_mode();
}

// The bullet that hit heart p in its owner's view, applied in this frame, or noone.
function dronline_hit_bullet(p)
{
    var b = global.dro_hit[p];
    if (b < 100000 || !instance_exists(b))
        return noone;
    var ok = false;
    with (b)
        ok = (object_index == global.dro_obj_bullet) || object_is_ancestor(object_index, global.dro_obj_bullet);
    return ok ? b : noone;
}

// Replaces instance_place(x2, y2, obj_collidebullet) and (x3, y3) in obj_heart's Step (patcher).
function dronline_place(p, hx, hy)
{
    if (!dronline_ownhit(p))
        return instance_place(hx, hy, global.dro_obj_bullet);
    return dronline_hit_bullet(p);
}

// Start of obj_heart's End Step (patcher): heart 1's collision event is skipped while its owner decides,
// so the reported hit runs the same event code here, with the reported bullet as `other`.
function dronline_heart_endstep()
{
    if (!dronline_ownhit(1))
        return;
    var b = dronline_hit_bullet(1);
    if (b != noone)
        dro_heart_collide_p1(b);
}

// Before sending this frame's input: did a bullet touch my heart where I saw it in the last frame?
function dronline_detect_hit()
{
    if (drnet_pending_count() <= 0 && drnet_delay() <= 0)
        return 0;
    if (!dronline_ownhit_mode())
        return 0;
    var h = instance_find(global.dro_obj_heart, 0);
    var p = global.dro_player;
    var found = 0;
    with (h)
    {
        dronline_predict(p);
        var ox = x;
        var oy = y;
        x = global.dro_px;
        y = global.dro_py;
        with (global.dro_obj_bullet)
        {
            if (found == 0 && place_meeting(x, y, other) && (!variable_instance_exists(id, "active") || active == 1))
                found = real(id);
        }
        x = ox;
        y = oy;
    }
    return found;
}

// Where the owner sees heart p: the shared position plus the moves already sent for later frames.
// Mirrors obj_heart's red SOUL movement (speed, slow walk while cancel is held, walls of the box);
// in the other SOUL modes it keeps the shared position. Call from the heart instance.
function dronline_predict(p)
{
    var hx = x;
    var hy = y;
    if (p == 2)
    {
        hx = x2;
        hy = y2;
    }
    else if (p == 3)
    {
        hx = x3;
        hy = y3;
    }
    global.dro_px = hx;
    global.dro_py = hy;
    var n = drnet_pending_count();
    if (n <= 0 || color != 0 || global.dro_obj_solid < 0)
        return;
    var suf = (p == 1) ? "" : string(p);
    var w = variable_instance_get(id, "wspeed" + suf);
    var cm = variable_instance_get(id, "canmove" + suf);
    var ds = variable_instance_get(id, "disableslow" + suf);
    var bf = variable_instance_get(id, "box_factor" + suf);
    var gh = variable_instance_get(id, "grav_h" + suf);
    var dh = variable_instance_get(id, "dash_h" + suf);
    var da = variable_instance_get(id, "p" + string(p) + "DeathAnimation");
    if (is_undefined(w) || is_undefined(cm) || !cm)
        return;
    if ((!is_undefined(bf) && bf != 0) || (!is_undefined(gh) && gh != 0) || (!is_undefined(dh) && dh != 0) || (!is_undefined(da) && da))
        return;
    var slow = (is_undefined(ds) || ds == 0) && global.flag[22] == 0;
    for (var i = 0; i < n; i++)
    {
        var b = drnet_pending(i);
        var px = 0;
        var py = 0;
        if (b & 2)
            px = w;
        if (b & 8)
            px = -w;
        if (b & 1)
            py = w;
        if (b & 4)
            py = -w;
        if ((b & 32) && slow)
        {
            px = ceil(px * 0.5);
            py = ceil(py * 0.5);
        }
        while (px != 0 && place_meeting(hx + px, hy, global.dro_obj_solid))
            px -= sign(px);
        hx += px;
        while (py != 0 && place_meeting(hx, hy + py, global.dro_obj_solid))
            py -= sign(py);
        hy += py;
    }
    global.dro_px = hx;
    global.dro_py = hy;
}

// Around obj_heart's Draw (patcher): draw my own heart where I see it, then put the shared position back.
// Only the heart's own Draw sees the other position; every other object keeps the shared state.
function dronline_heart_draw(start)
{
    if (!start)
    {
        dronline_heart_unswap();
        return;
    }
    if (!variable_global_exists("dro_net") || !global.dro_net || global.dro_connecting || drnet_pending_count() <= 0)
        return;
    var p = global.dro_player;
    dronline_predict(p);
    if (p == 1 && (global.dro_px != x || global.dro_py != y))
    {
        global.dro_sx = x;
        global.dro_sy = y;
        x = global.dro_px;
        y = global.dro_py;
        global.dro_swap = 1;
    }
    else if (p == 2 && (global.dro_px != x2 || global.dro_py != y2))
    {
        global.dro_sx = x2;
        global.dro_sy = y2;
        x2 = global.dro_px;
        y2 = global.dro_py;
        global.dro_swap = 2;
    }
    else if (p == 3 && (global.dro_px != x3 || global.dro_py != y3))
    {
        global.dro_sx = x3;
        global.dro_sy = y3;
        x3 = global.dro_px;
        y3 = global.dro_py;
        global.dro_swap = 3;
    }
}

function dronline_heart_unswap()
{
    if (!variable_global_exists("dro_swap") || global.dro_swap == 0)
        return;
    var h = instance_find(global.dro_obj_heart, 0);
    if (h != noone)
    {
        with (h)
        {
            if (global.dro_swap == 1)
            {
                x = global.dro_sx;
                y = global.dro_sy;
            }
            else if (global.dro_swap == 2)
            {
                x2 = global.dro_sx;
                y2 = global.dro_sy;
            }
            else
            {
                x3 = global.dro_sx;
                y3 = global.dro_sy;
            }
        }
    }
    global.dro_swap = 0;
}
