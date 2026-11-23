// Step: test hotkeys and timeouts.
dn_phase = 2;
if (dn_prompt)
{
    // Typing the host's address. Only digits, dots and one colon are kept.
    var _s = keyboard_string;
    var _out = "";
    for (var _i = 1; _i <= string_length(_s); _i++)
    {
        var _c = string_char_at(_s, _i);
        if (string_pos(_c, "0123456789.:") > 0)
            _out += _c;
    }
    if (string_length(_out) > 21)
        _out = string_copy(_out, 1, 21);
    if (_out != _s)
        keyboard_string = _out;
    dn_prompt_text = _out;
    if (keyboard_check_pressed(vk_enter) || keyboard_check_pressed(vk_f6))
    {
        dn_prompt = false;
        dro_net_join(dn_prompt_text);
    }
    else if (keyboard_check_pressed(vk_f7))
    {
        dn_prompt = false;
        dn_status = "Join cancelled.";
        dn_status_color = c_white;
    }
}
else
{
    if (keyboard_check_pressed(vk_f5))
    {
        dro_net_host();
    }
    else if (keyboard_check_pressed(vk_f6))
    {
        if (dn_state == 0)
        {
            dn_prompt = true;
            keyboard_string = dn_last_ip;
            dn_prompt_text = dn_last_ip;
            if (dn_show == 0)
                dn_show = 1;
        }
        else
        {
            dn_status = "Already online. Press F7 to stop first.";
            dn_status_color = c_yellow;
        }
    }
    else if (keyboard_check_pressed(vk_f7))
    {
        if (dn_state != 0)
            dro_net_close("Stopped with F7.", true, false);
    }
    if (keyboard_check_pressed(vk_f8) && dn_freeze_timer == 0 && dn_freeze_check == 0)
        dn_freeze_request = true;
    if (keyboard_check_pressed(vk_f9))
        dn_show = (dn_show + 1) mod 3;
}
if (dn_state == 2 || dn_state == 3)
{
    dn_timer += 1;
    if (dn_timer > 450)
    {
        if (dn_state == 2)
            dro_net_close("Could not reach " + dn_remote_ip + ":" + string(dn_remote_port) + ". Check the IP, that the host pressed F5, and the firewall.", false, false);
        else
            dro_net_close("Partner did not answer the version check.", false, true);
    }
}
if (dn_state == 4 && (current_time - dn_last_msg) > 15000)
    dro_net_close("Partner stopped answering for 15 seconds.", true, true);
