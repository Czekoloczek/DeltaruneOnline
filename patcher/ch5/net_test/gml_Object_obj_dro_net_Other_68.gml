// Async Networking: connections and messages from the partner.
var _type = ds_map_find_value(async_load, "type");
var _id = ds_map_find_value(async_load, "id");
if (_type == network_type_connect)
{
    if (dn_server >= 0 && _id == dn_server)
    {
        var _new = ds_map_find_value(async_load, "socket");
        if (dn_sock >= 0)
        {
            network_destroy(_new);
            dro_net_log("Refused a second partner from " + string(ds_map_find_value(async_load, "ip")));
            exit;
        }
        dn_sock = _new;
        dn_remote_ip = string(ds_map_find_value(async_load, "ip"));
        dn_hello_sent = false;
        dn_state = 3;
        dn_timer = 0;
        dro_net_set_status("Partner connected from " + dn_remote_ip + ". Checking version...", c_yellow);
        dro_net_send_hello();
    }
    exit;
}
if (_type == network_type_non_blocking_connect)
{
    if (dn_sock >= 0 && _id == dn_sock && dn_state == 2)
    {
        if (ds_map_find_value(async_load, "succeeded"))
            dro_net_connected();
        else
            dro_net_close("Could not connect to " + dn_remote_ip + ":" + string(dn_remote_port) + ". Check the IP, that the host pressed F5, and the firewall.", false, false);
    }
    exit;
}
if (_type == network_type_disconnect)
{
    var _gone = ds_map_find_value(async_load, "socket");
    if (dn_sock >= 0 && (_gone == dn_sock || _id == dn_sock))
        dro_net_close("Partner disconnected.", false, true);
    exit;
}
if (_type != network_type_data)
    exit;
if (dn_sock < 0 || _id != dn_sock)
    exit;
if (dn_state == 2)
    dro_net_connected();
dn_last_msg = current_time;
var _b = ds_map_find_value(async_load, "buffer");
var _size = ds_map_find_value(async_load, "size");
if (_size < 1)
    exit;
buffer_seek(_b, buffer_seek_start, 0);
var _msg = buffer_read(_b, buffer_u8);
if (_msg == 1)
{
    dro_net_on_hello(_b, _size);
}
else if (_msg == 2)
{
    if (dn_state != 4 || _size < 11)
        exit;
    var _f = buffer_read(_b, buffer_u32);
    var _m = buffer_read(_b, buffer_u16);
    dn_frames_got += 1;
    if (dn_remote_frame >= 0)
    {
        if (_f > (dn_remote_frame + 1))
            dn_frames_missing += (_f - dn_remote_frame - 1);
        else if (_f <= dn_remote_frame)
            dn_frames_late += 1;
    }
    if (_f > dn_remote_frame)
    {
        dn_remote_frame = _f;
        dn_remote_mask = _m;
    }
    dn_arrivals += 1;
    dn_hist_phase[dn_phase] += 1;
    if (dn_timing_logged < 5)
    {
        dn_timing_logged += 1;
        var _phase_names = ["start", "BeginStep", "Step", "EndStep", "after draw"];
        dro_net_log("Timing sample: partner frame " + string(_f) + " arrived at my frame " + string(dn_link_frame) + ", after my " + _phase_names[dn_phase] + " event");
    }
}
else if (_msg == 3)
{
    if (_size < 5)
        exit;
    var _t = buffer_read(_b, buffer_u32);
    dro_net_send_begin(4);
    buffer_write(dn_buf, buffer_u32, _t);
    dro_net_send_end();
}
else if (_msg == 4)
{
    if (dn_state != 4 || _size < 5)
        exit;
    var _sent = buffer_read(_b, buffer_u32);
    dn_ping = current_time - _sent;
    if (dn_ping_min < 0 || dn_ping < dn_ping_min)
        dn_ping_min = dn_ping;
    if (dn_ping > dn_ping_max)
        dn_ping_max = dn_ping;
    dn_ping_sum += dn_ping;
    dn_ping_count += 1;
}
else if (_msg == 5)
{
    dro_net_close("Partner left the session.", false, true);
}
