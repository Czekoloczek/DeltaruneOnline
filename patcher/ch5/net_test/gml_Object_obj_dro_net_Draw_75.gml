// Draw GUI End: the last event of a frame. The freeze test starts and ends here,
// so the game is always paused between two whole frames.
dn_phase = 4;
if (dn_freeze_request)
{
    dn_freeze_request = false;
    dro_net_freeze_begin();
}
else if (dn_freeze_timer > 0)
{
    dn_freeze_timer -= 1;
    if (dn_freeze_timer == 0)
        dro_net_freeze_finish();
}
