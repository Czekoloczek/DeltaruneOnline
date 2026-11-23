// End Step: send this frame's message. obj_time has already read input this frame.
dn_phase = 3;
if (dn_state == 4 && dn_freeze_timer == 0)
{
    dn_link_frame += 1;
    dro_net_send_begin(2);
    buffer_write(dn_buf, buffer_u32, dn_link_frame);
    buffer_write(dn_buf, buffer_u16, dro_net_input_mask());
    buffer_write(dn_buf, buffer_u32, current_time);
    dro_net_send_end();
    dn_frames_sent += 1;
    if ((dn_link_frame mod 30) == 0)
    {
        dro_net_send_begin(3);
        buffer_write(dn_buf, buffer_u32, current_time);
        dro_net_send_end();
    }
    if (dn_remote_frame >= 0 && dn_link_frame > 30)
    {
        dn_diff = dn_link_frame - dn_remote_frame;
        if (!dn_diff_init)
        {
            dn_diff_min = dn_diff;
            dn_diff_max = dn_diff;
            dn_diff_init = true;
        }
        else
        {
            dn_diff_min = min(dn_diff_min, dn_diff);
            dn_diff_max = max(dn_diff_max, dn_diff);
        }
    }
    if ((dn_link_frame mod 900) == 0)
        dro_net_write_summary("every 30 seconds");
    if (dn_send_errors > 30)
        dro_net_close("Connection lost (sending failed).", false, true);
}
