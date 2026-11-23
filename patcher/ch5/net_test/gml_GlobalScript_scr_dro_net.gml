// DeltaruneOnline, Phase 1 network test (Chapter 5 + Local Multiplayer v19).
// Helper functions for obj_dro_net. They are always called from obj_dro_net,
// so the dn_* names below are that object's own variables.
//
// Messages (one TCP packet each, first byte = type):
//   1 HELLO  u32 magic, u16 protocol, string version
//   2 FRAME  u32 frame number, u16 P1 buttons held, u32 sender clock (ms)
//   3 PING   u32 sender clock (ms)
//   4 PONG   u32 the clock value from the PING
//   5 BYE    (no data)
//
// This code only reads the game's state (input and object list). It never changes
// gameplay, except during the F8 freeze test, which pauses and resumes all objects.

function dro_net_pad(_n)
{
    _n = floor(_n);
    if (_n < 10)
        return "0" + string(_n);
    return string(_n);
}

function dro_net_clock()
{
    var _d = date_current_datetime();
    return dro_net_pad(date_get_hour(_d)) + ":" + dro_net_pad(date_get_minute(_d)) + ":" + dro_net_pad(date_get_second(_d));
}

function dro_net_duration(_ms)
{
    var _s = floor(_ms / 1000);
    return string(floor(_s / 60)) + ":" + dro_net_pad(_s mod 60);
}

// Log files go to the game's save folder (%LOCALAPPDATA%\DELTARUNE on Windows).
function dro_net_log(_line)
{
    var _name = "online_test_offline.txt";
    if (dn_role == 1)
        _name = "online_test_host.txt";
    else if (dn_role == 2)
        _name = "online_test_join.txt";
    var _f = file_text_open_append(_name);
    if (_f < 0)
        return;
    file_text_write_string(_f, dro_net_clock() + "  " + _line);
    file_text_writeln(_f);
    file_text_close(_f);
}

function dro_net_set_status(_text, _color)
{
    dn_status = _text;
    dn_status_color = _color;
    dro_net_log(_text);
}

// P1's buttons as a bit mask, read from the slots that obj_time fills every frame:
// bit 0 down, 1 right, 2 up, 3 left, 4 Z, 5 X, 6 C, 7 Enter, 8 Shift, 9 Ctrl.
function dro_net_input_mask()
{
    if (!variable_global_exists("input_held"))
        return 0;
    var _m = 0;
    for (var _i = 0; _i < 10; _i++)
    {
        if (global.input_held[_i])
            _m |= (1 << _i);
    }
    return _m;
}

function dro_net_mask_text(_m)
{
    var _names = ["DOWN", "RIGHT", "UP", "LEFT", "Z", "X", "C", "ENTER", "SHIFT", "CTRL"];
    var _s = "";
    for (var _i = 0; _i < 10; _i++)
    {
        if (_m & (1 << _i))
            _s += _names[_i] + " ";
    }
    if (_s == "")
        _s = "-";
    return _s;
}

function dro_net_reset_stats()
{
    dn_link_frame = 0;
    dn_link_start = current_time;
    dn_frames_sent = 0;
    dn_frames_got = 0;
    dn_frames_missing = 0;
    dn_frames_late = 0;
    dn_remote_frame = -1;
    dn_remote_mask = 0;
    dn_arrivals = 0;
    dn_hist_per_frame = [0, 0, 0, 0];
    dn_hist_phase = [0, 0, 0, 0, 0];
    dn_quiet = 0;
    dn_quiet_max = 0;
    dn_diff = 0;
    dn_diff_min = 0;
    dn_diff_max = 0;
    dn_diff_init = false;
    dn_ping = -1;
    dn_ping_min = -1;
    dn_ping_max = -1;
    dn_ping_sum = 0;
    dn_ping_count = 0;
    dn_last_msg = current_time;
    dn_slow_frame = 0;
    dn_timing_logged = 0;
    dn_send_errors = 0;
}

function dro_net_send_begin(_type)
{
    buffer_seek(dn_buf, buffer_seek_start, 0);
    buffer_write(dn_buf, buffer_u8, _type);
}

function dro_net_send_end()
{
    if (dn_sock < 0)
        return;
    var _r = network_send_packet(dn_sock, dn_buf, buffer_tell(dn_buf));
    if (_r < 0)
        dn_send_errors += 1;
}

function dro_net_send_hello()
{
    if (dn_hello_sent)
        return;
    dn_hello_sent = true;
    dro_net_send_begin(1);
    buffer_write(dn_buf, buffer_u32, dn_magic);
    buffer_write(dn_buf, buffer_u16, dn_proto);
    buffer_write(dn_buf, buffer_string, dn_version);
    dro_net_send_end();
}

function dro_net_host()
{
    if (dn_state != 0)
    {
        dn_status = "Already online. Press F7 to stop first.";
        dn_status_color = c_yellow;
        return;
    }
    dn_role = 1;
    dro_net_log("==== " + dn_version + ": hosting on port " + string(dn_port) + " ====");
    dn_server = network_create_server(network_socket_tcp, dn_port, 1);
    if (dn_server < 0)
    {
        dn_server = -1;
        dro_net_set_status("Could not open port " + string(dn_port) + ". Is the other game already hosting?", c_red);
        dn_role = 0;
        return;
    }
    dn_state = 1;
    dro_net_set_status("Hosting on port " + string(dn_port) + ". Waiting for partner...", c_yellow);
}

function dro_net_join(_addr)
{
    if (dn_state != 0)
        return;
    var _ip = _addr;
    var _port = dn_port;
    var _c = string_pos(":", _addr);
    if (_c > 0)
    {
        _ip = string_copy(_addr, 1, _c - 1);
        var _p = string_digits(string_delete(_addr, 1, _c));
        if (_p != "")
            _port = real(_p);
    }
    if (_ip == "")
    {
        dn_status = "No address typed. Press F6 and type the host's IP.";
        dn_status_color = c_red;
        return;
    }
    dn_role = 2;
    dn_remote_ip = _ip;
    dn_remote_port = _port;
    dn_last_ip = _addr;
    ini_open("deltarune_online.ini");
    ini_write_string("online", "last_ip", _addr);
    ini_close();
    dro_net_log("==== " + dn_version + ": joining " + _ip + ":" + string(_port) + " ====");
    dn_hello_sent = false;
    dn_sock = network_create_socket(network_socket_tcp);
    if (dn_sock < 0)
    {
        dn_sock = -1;
        dro_net_set_status("Could not create a network socket.", c_red);
        return;
    }
    var _r = network_connect(dn_sock, _ip, _port);
    dro_net_log("network_connect returned " + string(_r));
    dn_state = 2;
    dn_timer = 0;
    dro_net_set_status("Connecting to " + _ip + ":" + string(_port) + "...", c_yellow);
}

// The TCP connection is open; now both sides exchange HELLO.
function dro_net_connected()
{
    dn_state = 3;
    dn_timer = 0;
    dro_net_set_status("Connected to " + dn_remote_ip + ". Checking version...", c_yellow);
    dro_net_send_hello();
}

function dro_net_on_hello(_b, _size)
{
    if (_size < 8)
    {
        dro_net_close("Something else answered on that port (not this mod).", false, true);
        return;
    }
    var _magic = buffer_read(_b, buffer_u32);
    var _proto = buffer_read(_b, buffer_u16);
    var _ver = buffer_read(_b, buffer_string);
    if (_magic != dn_magic)
    {
        dro_net_close("Something else answered on that port (not this mod).", false, true);
        return;
    }
    if (_proto != dn_proto)
    {
        dro_net_close("Partner has a different test version: " + string(_ver), true, true);
        return;
    }
    dn_partner_version = string(_ver);
    if (dn_state == 3)
    {
        dro_net_send_hello();
        dro_net_reset_stats();
        dn_state = 4;
        if (dn_show == 1)
            dn_show = 2;
        dro_net_set_status("Linked with " + dn_remote_ip + " (" + dn_partner_version + ").", c_lime);
    }
}

// Closes the partner connection. A host keeps listening when _keep_hosting is true.
function dro_net_close(_reason, _send_bye, _keep_hosting)
{
    var _was_linked = (dn_state == 4);
    if (dn_sock >= 0)
    {
        if (_send_bye && dn_state >= 3)
        {
            dro_net_send_begin(5);
            dro_net_send_end();
        }
        network_destroy(dn_sock);
        dn_sock = -1;
    }
    if (_was_linked)
        dro_net_write_summary(_reason);
    dn_hello_sent = false;
    if (_keep_hosting && dn_role == 1 && dn_server >= 0)
    {
        dn_state = 1;
        dro_net_set_status(_reason + " Still hosting, waiting for partner...", c_yellow);
        return;
    }
    if (dn_server >= 0)
    {
        network_destroy(dn_server);
        dn_server = -1;
    }
    dn_state = 0;
    dro_net_set_status(_reason, c_orange);
    dn_role = 0;
}

function dro_net_stat_lines()
{
    var _l = [];
    array_push(_l, "Partner " + dn_remote_ip + ", linked for " + dro_net_duration(current_time - dn_link_start));
    var _ping = "Ping: waiting";
    if (dn_ping_count > 0)
        _ping = "Ping " + string(dn_ping) + " ms (min " + string(dn_ping_min) + ", max " + string(dn_ping_max) + ", avg " + string(round(dn_ping_sum / dn_ping_count)) + ")";
    array_push(_l, _ping);
    array_push(_l, "Frame: me " + string(dn_link_frame) + ", partner " + string(max(dn_remote_frame, 0)) + ", diff " + string(dn_diff) + " (" + string(dn_diff_min) + " to " + string(dn_diff_max) + ")");
    array_push(_l, "Got " + string(dn_frames_got) + " of partner's frames, missing " + string(dn_frames_missing) + ", late " + string(dn_frames_late) + ", send errors " + string(dn_send_errors));
    var _h = dn_hist_per_frame;
    array_push(_l, "Per frame 0:" + string(_h[0]) + " 1:" + string(_h[1]) + " 2:" + string(_h[2]) + " 3+:" + string(_h[3]) + ", longest wait " + string(dn_quiet_max) + " fr");
    var _p = dn_hist_phase;
    array_push(_l, "Arrived in BeginStep " + string(_p[1]) + ", Step " + string(_p[2]) + ", EndStep " + string(_p[3]) + ", after draw " + string(_p[4]));
    array_push(_l, "Partner holds: " + dro_net_mask_text(dn_remote_mask));
    array_push(_l, "FPS " + string(fps) + ", slowest frame " + string(round(dn_slow_frame)) + " ms");
    return _l;
}

function dro_net_write_summary(_why)
{
    dro_net_log("Summary (" + _why + "):");
    var _l = dro_net_stat_lines();
    for (var _i = 0; _i < array_length(_l); _i++)
        dro_net_log("    " + _l[_i]);
}

// Freeze test (F8): the same trick the game uses for its console pause.
// List all objects, deactivate everything except obj_dro_net for 60 frames,
// then wake exactly those objects again and check that the list is unchanged.
function dro_net_snapshot()
{
    var _ids = [];
    var _xs = [];
    var _ys = [];
    with (all)
    {
        if (object_index != obj_dro_net)
        {
            array_push(_ids, id);
            array_push(_xs, x);
            array_push(_ys, y);
        }
    }
    return [_ids, _xs, _ys];
}

function dro_net_freeze_begin()
{
    dn_freeze_before = dro_net_snapshot();
    audio_pause_all();
    instance_deactivate_all(true);
    dn_freeze_timer = 60;
    dn_freeze_result = "Freeze test running (2 seconds)...";
    dro_net_log("Freeze test: froze " + string(array_length(dn_freeze_before[0])) + " objects for 60 frames");
}

function dro_net_freeze_finish()
{
    var _ids = dn_freeze_before[0];
    for (var _i = 0; _i < array_length(_ids); _i++)
        instance_activate_object(_ids[_i]);
    audio_resume_all();
    dn_freeze_now = dro_net_snapshot();
    dn_freeze_check = 1;
}

function dro_net_same_order(_a, _b)
{
    if (array_length(_a) != array_length(_b))
        return false;
    for (var _i = 0; _i < array_length(_a); _i++)
    {
        if (_a[_i] != _b[_i])
            return false;
    }
    return true;
}

function dro_net_freeze_compare()
{
    var _before = dn_freeze_before;
    var _now = dn_freeze_now;
    var _later = dro_net_snapshot();
    var _n = array_length(_before[0]);
    var _n_now = array_length(_now[0]);
    var _same_now = dro_net_same_order(_before[0], _now[0]);
    var _same_later = dro_net_same_order(_before[0], _later[0]);
    var _moved = 0;
    if (_same_now)
    {
        for (var _i = 0; _i < _n; _i++)
        {
            if (_now[1][_i] != _before[1][_i] || _now[2][_i] != _before[2][_i])
                _moved += 1;
        }
    }
    var _verdict = "FAILED";
    if (_same_now)
        _verdict = "OK";
    else if (_same_later)
        _verdict = "OK one frame later";
    dn_freeze_result = "Freeze test " + _verdict + ": " + string(_n) + " objects, " + string(_n_now) + " back at once, moved " + string(_moved);
    dro_net_log(dn_freeze_result + ". Order right after wake: " + (_same_now ? "same" : "different") + ", one frame later: " + (_same_later ? "same" : "different") + " (" + string(array_length(_later[0])) + " objects)");
}
