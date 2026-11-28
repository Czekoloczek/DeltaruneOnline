/*
 * Standalone test for drnet.dll (no game needed).
 *   test_drnet host 2 1 <frames> [lag_ms] [delay]           (host, 2 players, player 1)
 *   test_drnet client 2 2 <frames> [lag_ms] [delay] [ip]    (client, player 2)
 *   ... [ip] [quit_at]: at that frame say goodbye with drnet_bye and exit without drnet_stop, as the game does
 *   when its window is closed; the other side must then report "left" at once instead of the 10 s timeout.
 *   quit_at "lobby" or "lobby-crash" (a client): say HELLO for 2 s without being welcomed (the host still waits
 *   for another player), then leave before everybody is there, with a goodbye (drnet_bye, as a closed window) or
 *   without a word (as a killed game). The host must stop counting this player (issue #58, test_handshake.sh).
 *   After the goodbye it lingers 0.5 s, as a closing game does, so with lag_ms the HELLOs still in the lag queue
 *   reach the host after the goodbye; the host must ignore them.
 * While connecting, a host of 3 prints "lobby: N of 3 players here" each time drnet_lobby (the waiting screen's
 * count) changes.
 * delay -1 (default) = automatic. lag_ms adds test latency to every packet this side sends.
 *
 * Each side feeds pseudo-random bits and events, folds every frame's result (all players' bits and
 * events) into a running checksum and prints it: lockstep works if all sides print the same checksums.
 * It also prints the delay, ping, average wait and frame time, so a slowdown shows up as frame time > 33 ms.
 * The host asks for session flags 5 and every client for 0: after connecting, every side must print the host's
 * ("session 5"), as the game applies the host's settings everywhere (immortal SOUL).
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

/* GetTickCount moves in 15.6 ms steps even after timeBeginPeriod(1), so frame times use the performance counter */
static double now_ms(void)
{
    static LARGE_INTEGER freq;
    LARGE_INTEGER c;
    if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&c);
    return (double)c.QuadPart * 1000.0 / (double)freq.QuadPart;
}

static unsigned bits_for(int player, int frame) { return (unsigned)((frame * 2654435761u + player * 40503u) >> 7) & 0x7FF; }

int main(int argc, char **argv)
{
    /* Optional `-game <path>` (same flag DELTARUNE uses) is read from the process command line by drnet.dll;
       strip it here so positional arguments keep their indices. */
    const char *pos[16];
    int npos = 0, ai;
    for (ai = 1; ai < argc && npos < 16; ai++) {
        if (strcmp(argv[ai], "-game") == 0 && ai + 1 < argc) { ai++; continue; }
        pos[npos++] = argv[ai];
    }
    if (npos < 4) { printf("usage: %s host|client players me frames [lag_ms] [delay] [ip] [quit_at|lobby|lobby-crash] [-game path]\n", argv[0]); return 2; }
    const char *role = pos[0]; int players = atoi(pos[1]), me = atoi(pos[2]), frames = atoi(pos[3]);
    double lag = npos > 4 ? atof(pos[4]) : 0, dly = npos > 5 ? atof(pos[5]) : -1;
    const char *ip = npos > 6 ? pos[6] : "127.0.0.1";
    int quit_at = npos > 7 ? atoi(pos[7]) : -1;
    const char *lobby_quit = npos > 7 && strncmp(pos[7], "lobby", 5) == 0 ? pos[7] : NULL;
    int f, p;
    unsigned long long sum = 1469598103934665603ull;
    /* Windows rounds Sleep() up to its 15.6 ms timer, so a 33 ms frame took 46.8 ms (3 ticks) on native Windows
       (online test 6, 2026-11-23). Ask for a 1 ms timer; winmm is loaded at run time so
       the build command needs no extra library. */
    HMODULE mm = LoadLibraryA("winmm.dll");
    if (mm) { typedef UINT (WINAPI *f_tbp)(UINT); f_tbp tbp = (f_tbp)GetProcAddress(mm, "timeBeginPeriod"); if (tbp) tbp(1); }
    HMODULE h = LoadLibraryA("drnet.dll");
    if (!h) { printf("cannot load drnet.dll\n"); return 1; }
    f_start start = (f_start)GetProcAddress(h, "drnet_start");
    f_str set_host = (f_str)GetProcAddress(h, "drnet_set_host");
    f_sd option = (f_sd)GetProcAddress(h, "drnet_option");
    f_d connect_poll = (f_d)GetProcAddress(h, "drnet_connect_poll");
    f_dd exchange = (f_dd)GetProcAddress(h, "drnet_exchange");
    f_d set_aux = (f_d)GetProcAddress(h, "drnet_set_aux");
    f_d aux = (f_d)GetProcAddress(h, "drnet_aux");
    f_dd hash = (f_dd)GetProcAddress(h, "drnet_hash");
    f_v desync = (f_v)GetProcAddress(h, "drnet_desync");
    f_v delay = (f_v)GetProcAddress(h, "drnet_delay");
    f_v ping = (f_v)GetProcAddress(h, "drnet_ping");
    f_v waitms = (f_v)GetProcAddress(h, "drnet_wait");
    f_v pending = (f_v)GetProcAddress(h, "drnet_pending_count");
    f_s status = (f_s)GetProcAddress(h, "drnet_status");
    f_v stop = (f_v)GetProcAddress(h, "drnet_stop");
    f_str bye = (f_str)GetProcAddress(h, "drnet_bye");
    f_v session = (f_v)GetProcAddress(h, "drnet_session");
    f_v lobby = (f_v)GetProcAddress(h, "drnet_lobby");
    if (!session) { printf("drnet.dll has no drnet_session\n"); return 1; }
    set_host(ip);
    if (lag > 0) option("lag", lag);
    option("session", strcmp(role, "host") == 0 ? 5 : 0);
    if (!start(strcmp(role, "host") == 0, 7845, players, me, dly)) { printf("start failed: %s\n", status()); return 1; }
    double r, here = -1;
    if (lobby_quit) {
        /* polled like the game; with a 2 s timeout it gives up before the others are in */
        int crash = strcmp(lobby_quit, "lobby-crash") == 0;
        while ((r = connect_poll(2000)) == 0) Sleep(33);
        if (r > 0) { printf("%s was welcomed: the host did not wait for the others\n", role); stop(); return 1; }
        printf("%s %s the lobby before everybody is there\n", role, crash ? "crashes in" : "leaves");
        fflush(stdout);
        if (!crash) { bye("test lobby quit"); Sleep(500); }
        ExitProcess(0);
    }
    printf("%s: connecting...\n", status());
    /* like the game: one drnet_connect_poll per 33 ms frame (it draws its waiting screen in between) */
    while ((r = connect_poll(30000)) == 0) {
        if (players > 2 && strcmp(role, "host") == 0 && lobby() != here) {
            here = lobby();
            printf("lobby: %g of %d players here\n", here, players);
            fflush(stdout);
        }
        Sleep(33);
    }
    if (r < 0) { printf("connect failed: %s\n", status()); stop(); return 1; }
    printf("%s session %g\n", role, session());
    double t0 = now_ms(), tblock = t0;
    for (f = 0; f < frames; f++) {
        double tf = now_ms();
        if (f == quit_at) { printf("%s quits at frame %d (drnet_bye, no drnet_stop)\n", role, f); fflush(stdout); bye("test quit"); ExitProcess(0); }
        set_aux(f % 37 == 0 ? 100000 + f * 3 + me : 0);
        double all = exchange(f, bits_for(me, f));
        /* like the game (dronline_step): stop the network, which says goodbye to whoever is still there */
        if (all == -2) { printf("%s: %s at frame %d, noticed after %.0f ms\n", role, status(), f, now_ms() - tf); stop(); return 0; }
        if (all < 0) { printf("exchange timeout at frame %d\n", f); stop(); return 1; }
        unsigned long long v = (unsigned long long)all;
        for (p = 1; p <= players; p++) v = v * 1099511628211ull + (unsigned long long)aux(p);
        sum = (sum ^ v) * 1099511628211ull;
        if (f % 30 == 0) hash(f, (double)(f * 7 + 1));
        if (f % 150 == 149) {
            printf("%s frame %4d sum %016llx delay %g pending %g ping %.1f wait %.1f ms frame %.1f ms\n", role, f + 1, sum,
                   delay(), pending(), ping(), waitms(), (now_ms() - tblock) / 150.0);
            fflush(stdout);
            tblock = now_ms();
        }
        double pace = me == 1 ? 33 : 32;   /* a game frame: work + sleep to 30 fps */
        Sleep(5);
        double used = now_ms() - tf;
        if (used < pace) Sleep((DWORD)(pace - used + 0.5));
    }
    printf("%s done: %d frames in %.0f ms, final sum %016llx, desync %g\n", role, frames, now_ms() - t0, sum, desync());
    stop();
    return 0;
}
