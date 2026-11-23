/*
 * A stand-in for the game, to test the Agent API of drnet.dll without the game (native/test_agent.py drives it).
 *   test_agent solo <agent_port> [fps] [pad]
 *   test_agent host|client <agent_port> [fps] [pad] [delay] [lag_ms] [wave]     (2 players; the host is player 1)
 * agent_port 0 = this side has no agent. pad adds that many bytes to the state, to test long replies. delay -1
 * (default) = automatic; lag_ms adds test latency to every packet this side sends. wave 1: the latency is there
 * only in the first 3 of every 10 seconds, so an automatic delay keeps growing and shrinking.
 *
 * Every frame it calls the DLL the way gml/scr_dronline.gml does (dronline_step): drnet_agent_frame, and
 * drnet_agent_state with the state when asked; drnet_agent_bits for the local input; online, drnet_exchange. Then
 * it applies the input of every player: right/left move x, down/up move y, and the presses of confirm, cancel and
 * menu are counted (a button held for several frames is one press). The state says where that got every player:
 *   {"frame":120,"mode":"solo","player":1,"delay":0,"delay_changes":0,
 *    "players_pos":[{"player":1,"x":30,"y":0,"confirm":2,"cancel":0,"menu":0}],"pad":""}
 * It ends when a player holds all seven buttons in one frame, when the other side leaves, or after 5 minutes.
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef double (*f_start)(double, double, double, double, double);
typedef double (*f_str)(const char *);
typedef double (*f_sd)(const char *, double);
typedef double (*f_d)(double);
typedef double (*f_dd)(double, double);
typedef double (*f_v)(void);
typedef const char *(*f_s)(void);

static double now_ms(void)
{
    static LARGE_INTEGER freq;
    LARGE_INTEGER c;
    if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&c);
    return (double)c.QuadPart * 1000.0 / (double)freq.QuadPart;
}

int main(int argc, char **argv)
{
    const char *mode; int agent_port, fps, pad, online, players, me, f, p, n, wave, quit = 0, changes = 0;
    double dly, lag, t0, last_delay = -1;
    long x[3] = { 0, 0, 0 }, y[3] = { 0, 0, 0 }, confirm[3] = { 0, 0, 0 }, cancel[3] = { 0, 0, 0 }, menu[3] = { 0, 0, 0 };
    unsigned before[3] = { 0, 0, 0 };
    char *state;
    HMODULE mm, h;
    f_start start; f_str set_host, agent_state; f_sd option; f_d connect, agent_start; f_dd exchange;
    f_v delay, stop, agent_frame, agent_bits; f_s status;

    if (argc < 3) { printf("usage: %s solo|host|client agent_port [fps] [pad] [delay] [lag_ms] [wave]\n", argv[0]); return 2; }
    mode = argv[1]; agent_port = atoi(argv[2]);
    fps = argc > 3 ? atoi(argv[3]) : 30; pad = argc > 4 ? atoi(argv[4]) : 0;
    dly = argc > 5 ? atof(argv[5]) : -1; lag = argc > 6 ? atof(argv[6]) : 0; wave = argc > 7 ? atoi(argv[7]) : 0;
    online = strcmp(mode, "solo") != 0;
    players = online ? 2 : 1; me = strcmp(mode, "client") == 0 ? 2 : 1;
    if (fps < 1 || pad < 0) { printf("bad fps or pad\n"); return 2; }
    state = (char *)malloc((size_t)pad + 1024);
    if (!state) { printf("out of memory\n"); return 1; }

    /* a 1 ms timer for Sleep, as in test_drnet.c */
    mm = LoadLibraryA("winmm.dll");
    if (mm) { typedef UINT (WINAPI *f_tbp)(UINT); f_tbp tbp = (f_tbp)GetProcAddress(mm, "timeBeginPeriod"); if (tbp) tbp(1); }
    h = LoadLibraryA("drnet.dll");
    if (!h) { printf("cannot load drnet.dll\n"); return 1; }
    start = (f_start)GetProcAddress(h, "drnet_start");
    set_host = (f_str)GetProcAddress(h, "drnet_set_host");
    option = (f_sd)GetProcAddress(h, "drnet_option");
    connect = (f_d)GetProcAddress(h, "drnet_connect");
    exchange = (f_dd)GetProcAddress(h, "drnet_exchange");
    delay = (f_v)GetProcAddress(h, "drnet_delay");
    status = (f_s)GetProcAddress(h, "drnet_status");
    stop = (f_v)GetProcAddress(h, "drnet_stop");
    agent_start = (f_d)GetProcAddress(h, "drnet_agent_start");
    agent_frame = (f_v)GetProcAddress(h, "drnet_agent_frame");
    agent_state = (f_str)GetProcAddress(h, "drnet_agent_state");
    agent_bits = (f_v)GetProcAddress(h, "drnet_agent_bits");
    if (!agent_start || !agent_frame || !agent_state || !agent_bits) { printf("drnet.dll has no Agent API\n"); return 1; }

    /* the order of dronline_init: the agent's listener first, then the network */
    if (agent_port > 0 && !agent_start(agent_port)) { printf("agent start failed on port %d\n", agent_port); return 1; }
    if (online) {
        set_host("127.0.0.1");
        if (lag > 0) option("lag", lag);
        if (!start(me == 1, 7845, players, me, dly)) { printf("start failed: %s\n", status()); return 1; }
        if (!connect(30000)) { printf("connect timeout\n"); return 1; }
    }
    printf("%s ready, agent port %d\n", mode, agent_port);
    fflush(stdout);

    t0 = now_ms();
    for (f = 0; !quit && now_ms() - t0 < 300000; f++) {
        double tf = now_ms(), used;
        unsigned bits[3] = { 0, 0, 0 };
        if (online && wave && f % (10 * fps) == 0) option("lag", lag);
        if (online && wave && f % (10 * fps) == 3 * fps) option("lag", 0);
        if (online && delay() != last_delay) { if (last_delay >= 0) changes++; last_delay = delay(); }
        if (agent_frame()) {
            n = sprintf(state, "{\"frame\":%d,\"mode\":\"%s\",\"player\":%d,\"delay\":%g,\"delay_changes\":%d,\"players_pos\":[",
                        f, mode, me, online ? delay() : 0.0, changes);
            for (p = 1; p <= players; p++)
                n += sprintf(state + n, "%s{\"player\":%d,\"x\":%ld,\"y\":%ld,\"confirm\":%ld,\"cancel\":%ld,\"menu\":%ld}",
                             p > 1 ? "," : "", p, x[p], y[p], confirm[p], cancel[p], menu[p]);
            n += sprintf(state + n, "],\"pad\":\"");
            memset(state + n, 'p', (size_t)pad); n += pad;
            strcpy(state + n, "\"}");
            agent_state(state);
        }
        bits[me] = (unsigned)agent_bits() & 127;
        if (online) {
            double all = exchange(f, bits[me]);
            if (all < 0) { printf("%s: %s at frame %d\n", mode, all == -2 ? status() : "exchange timeout", f); stop(); return all == -2 ? 0 : 1; }
            for (p = 1; p <= players; p++) { bits[p] = (unsigned)((unsigned long long)all >> (16 * (p - 1))) & 127; }
        }
        for (p = 1; p <= players; p++) {
            x[p] += ((bits[p] >> 1) & 1) - ((bits[p] >> 3) & 1);
            y[p] += (bits[p] & 1) - ((bits[p] >> 2) & 1);
            confirm[p] += (bits[p] & ~before[p]) >> 4 & 1; cancel[p] += (bits[p] & ~before[p]) >> 5 & 1; menu[p] += (bits[p] & ~before[p]) >> 6 & 1;
            before[p] = bits[p];
            if (bits[p] == 127) quit = 1;
        }
        used = now_ms() - tf;
        if (used < 1000.0 / fps) Sleep((DWORD)(1000.0 / fps - used + 0.5));
    }
    printf("%s done: %d frames, player %d at %ld %ld\n", mode, f, me, x[me], y[me]);
    if (online) stop();
    return 0;
}
