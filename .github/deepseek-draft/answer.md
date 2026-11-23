## Notes
Built the Agent API v1 draft: TCP listener on 127.0.0.1 in `drnet.dll`, GML-side JSON state builder and input queue, Python agent client, tests, docs, ini example and settings comment. A second concurrent agent connection is **refused** with an error while one is open, so the active GML step queue does not need to be replaced mid-stream.

Key decisions:
- DLL parses the small command subset and queues typed commands; GML pulls one command per frame. State JSON and input steps live in GML.
- Input steps are flattened into a frame array and OR'ed inside `dronline_script_frame`, so script recording includes agent input.
- `done` is sent after `global.dro_frame + drnet_delay()` online, checked after `dronline_apply_bits` and before the frame increment.
- The accepted TCP socket is non-blocking and non-inheritable; the listener uses `WSA_FLAG_NO_HANDLE_INHERIT`.
- The agent queue uses arrays and strings only; no `ds_*`/buffer/surface creation in the read-only state path.

Unsure about:
- Exact GML `string_hash_to_newline` availability in the Local Multiplayer mod; used directly per spec.
- Exact `global.chapter` presence/name; read with `variable_global_exists` and output `null` if missing.
- The 64 KB line-overflow error path discards through the next newline and keeps the connection open.

Did not follow:
- `record`/`replay` ignore the agent as the spec allows.
- Did not add a `drnet_agent_stop` export; the process exit cleans up the listener, matching the UDP socket lifecycle.

<<<FILE native/drnet.c>>>
/*
 * drnet.dll: lockstep input exchange for DeltaruneOnline (GameMaker extension, Windows / Proton).
 *
 * Star topology over UDP: clients talk only to the host, the host relays every player's input to
 * everyone, so only the host needs an open port. Every INPUT packet carries the sender's last
 * WINDOW frames, so lost packets are covered by the next one. A background thread receives packets
 * the moment they arrive (exact ping measurement, immediate relaying) and sends delayed test packets.
 *
 * Input delay is per player and automatic by default: each machine measures its ping and sends its
 * input just as many frames ahead as the network needs, so the game never has to wait. The other
 * machines do not need to know a player's delay: every frame of every player is filled exactly once
 * (a sample is repeated when the delay grows and merged into the next one when it shrinks), and the
 * frames themselves are what everybody agrees on. Frames filled ahead of time carry LAG_BIT, so the
 * game knows the local player sees their own SOUL ahead of the shared simulation (see scr_dronline.gml).
 *
 * Goodbye: a game that closes (window closed, Esc held, chapter change) sends BYE with its last inputs, through
 * drnet_bye (Game End and chapter change, called by scr_dronline.gml) or drnet_stop. The others then stop
 * waiting for it at once instead of after the 10 s timeout, but only once they run out of its input, so a
 * partner that is a frame behind still reaches the same last frame (both sides of a chapter change leave in the
 * same frame). It must be sent while the game still runs: sent from DllMain at process exit it never arrived.
 *
 * GameMaker calling convention: cdecl, numbers are double, strings are char*.
 *
 *   drnet_option(name, value)                                     -> 1 (before drnet_start: "lag", "jitter",
 *                                                                    "maxdelay", "slack"; see below)
 *   drnet_set_host(host_ip)                                       -> 1 (client: address of the host)
 *   drnet_start(is_host, port, players, my_player, delay)         -> 1 ok, 0 error (delay -1 = automatic)
 *   (GameMaker refuses DLL functions with more than 4 arguments unless all are numbers.)
 *   drnet_connect(timeout_ms)                                     -> 1 all players connected, 0 timeout
 *   drnet_set_aux(value)                                          -> 1 (event sent with the next input, e.g. a hit)
 *   drnet_exchange(frame, my_bits)                                -> bits of all players for `frame`
 *                                                                    (p1 | p2 << 16 | p3 << 32), -1 on timeout,
 *                                                                    -2 when a player whose input is missing left
 *   drnet_aux(player)                                             -> that player's event for the last exchanged frame
 *   drnet_pending_count()                                         -> own inputs already sent for later frames
 *   drnet_pending(i)                                              -> bits of the i-th of them (oldest first)
 *   drnet_delay()                                                 -> current own input delay in frames
 *   drnet_ping()                                                  -> round trip in ms (worst peer), -1 unknown
 *   drnet_wait()                                                  -> average ms per frame spent waiting (last 30 frames)
 *   drnet_hash(frame, hash)                                       -> 1 (sends a state fingerprint)
 *   drnet_desync()                                                -> first frame whose fingerprints differ, -1 none
 *   drnet_status()                                                -> short text for the screen
 *   drnet_bye(reason)                                             -> 1 sent, 0 not connected (goodbye, socket stays open)
 *   drnet_stop()                                                  -> 1 (says goodbye to the others first)
 *   drnet_log(text)                                               -> 1 (writes to drnet.log)
 *
 * Options: lag = extra one-way latency in ms added to every sent packet (testing), jitter = random extra
 * 0..jitter ms (testing), maxdelay = upper limit of the automatic delay (default 6), slack = ms of one-way
 * latency that fit into a frame without waiting (default 16).
 */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdarg.h>
#include <stdlib.h>
#include <math.h>

#define EXPORT __declspec(dllexport)
#define MAXP 3
#define RING 512
#define WINDOW 16
#define HRING 64
#define MAGIC 0x44524F4E /* "DRON" */
#define PROTO 3   /* 3: BYE */
#define LAG_BIT 0x0800
#define FRAME_MS (1000.0 / 30.0)
#define QSIZE 1024

enum { T_HELLO = 1, T_WELCOME = 2, T_INPUT = 3, T_HASH = 4, T_BYE = 5 };   /* BYE: an input packet that also says goodbye */

#pragma pack(push, 1)
typedef struct { uint32_t magic; uint8_t type; uint8_t player; uint8_t players; uint8_t proto; } hdr_t;
typedef struct {
    hdr_t h;
    uint32_t ts;        /* sender's clock in microseconds */
    uint32_t echo;      /* last ts the sender got from the receiver, 0 = none yet */
    uint32_t echo_age;  /* microseconds between receiving `echo` and sending this packet */
    uint16_t relay_ms;  /* host -> client: one-way latency of the slowest other client (3 players) */
    uint32_t first;
    uint8_t count;
    uint16_t bits[WINDOW];
    uint32_t aux[WINDOW];
} input_pkt;
typedef struct { hdr_t h; uint32_t frame; uint32_t hash; } hash_pkt;
#pragma pack(pop)

typedef struct { uint64_t due; struct sockaddr_in to; int len; char data[sizeof(input_pkt)]; } qitem;

static SOCKET sock = INVALID_SOCKET;
static CRITICAL_SECTION cs;
static int cs_ready;
static HANDLE ev_input, thr;
static volatile LONG running;
static int is_host, players, me, connected;
static struct sockaddr_in peer[MAXP + 1];   /* host: client addresses; client: peer[1] = host */
static int have_peer[MAXP + 1];
static int32_t frame_of[MAXP + 1][RING];    /* which frame the slot currently holds */
static uint16_t bits_of[MAXP + 1][RING];
static uint32_t aux_of[MAXP + 1][RING];
static int32_t next_target;                 /* next frame my input goes to */
static uint16_t held_bits;                  /* sampled but not yet assigned (after the delay shrank) */
static uint32_t held_aux, next_aux;
static int32_t last_frame = -1;             /* last frame returned by drnet_exchange */
static int delay, delay_auto, max_delay = 6;
static double slack_ms = 16, lag_ms, jitter_ms;
static double srtt[MAXP + 1], rttvar[MAXP + 1];   /* ms, 0 = not measured yet */
static uint32_t rx_ts[MAXP + 1];
static uint64_t rx_at[MAXP + 1];
static uint16_t relay_ms;                   /* client: from the host */
static uint64_t below_since;
static double wait_hist[30];
static int wait_pos;
static int32_t hash_frame[MAXP + 1][HRING];
static uint32_t hash_val[MAXP + 1][HRING];
static int32_t desync_at = -1;
static int gone[MAXP + 1];                  /* player said goodbye */
static int32_t bye_last[MAXP + 1];          /* last frame in that goodbye */
static char status[256];
static FILE *log_file;
static char host_addr[64] = "127.0.0.1";
static qitem queue[QSIZE];
static int qcount;
static uint64_t qpf;

static uint64_t now_us(void)
{
    LARGE_INTEGER c;
    if (!qpf) { LARGE_INTEGER f; QueryPerformanceFrequency(&f); qpf = (uint64_t)f.QuadPart; }
    QueryPerformanceCounter(&c);
    return (uint64_t)c.QuadPart / qpf * 1000000 + (uint64_t)c.QuadPart % qpf * 1000000 / qpf;
}

/* DRONLINE_ROLE: letters and digits, at most 16 (the same rule as dronline_init in scr_dronline.gml). */
static int valid_role(const char *r)
{
    int i;
    if (!r || !*r) return 0;
    for (i = 0; r[i]; i++)
        if (i >= 16 || !((r[i] >= 'a' && r[i] <= 'z') || (r[i] >= 'A' && r[i] <= 'Z') || (r[i] >= '0' && r[i] <= '9'))) return 0;
    return 1;
}

/* Log to %LOCALAPPDATA%\\DELTARUNE\\drnet.log (the game's save folder), one file per Wine prefix / PC.
 * Two copies on one PC share that folder: a copy started with DRONLINE_ROLE=<name> that reads its own
 * dronline_<name>.ini (scr_dronline.gml) logs to drnet_<name>.log instead. */
static void logm(const char *fmt, ...)
{
    va_list ap;
    if (!log_file) {
        char path[600]; const char *la = getenv("LOCALAPPDATA"), *role = getenv("DRONLINE_ROLE");
        if (!la) la = ".";
        snprintf(path, sizeof(path), "%s\\DELTARUNE\\dronline_%s.ini", la, valid_role(role) ? role : "");
        if (valid_role(role) && GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES)
            snprintf(path, sizeof(path), "%s\\DELTARUNE\\drnet_%s.log", la, role);
        else
            snprintf(path, sizeof(path), "%s\\DELTARUNE\\drnet.log", la);
        log_file = fopen(path, "a");
        if (!log_file) return;
    }
    fprintf(log_file, "[%lu] ", (unsigned long)GetTickCount());
    va_start(ap, fmt); vfprintf(log_file, fmt, ap); va_end(ap);
    fputc('\n', log_file); fflush(log_file);
}

static int fail(const char *what)
{
    snprintf(status, sizeof(status), "%s failed (WSA %d)", what, WSAGetLastError());
    logm("%s", status);
    return 0;
}

static void lock(void) { EnterCriticalSection(&cs); }
static void unlock(void) { LeaveCriticalSection(&cs); }

static void put_input(int p, int32_t f, uint16_t b, uint32_t a)
{
    if (p < 1 || p > MAXP || f < 0) return;
    frame_of[p][f % RING] = f;
    bits_of[p][f % RING] = b;
    aux_of[p][f % RING] = a;
}

static int has_input(int p, int32_t f) { return f >= 0 && frame_of[p][f % RING] == f; }

static int sends_logged;
static void raw_send(const struct sockaddr_in *a, const void *buf, int len)
{
    int r = sendto(sock, (const char *)buf, len, 0, (const struct sockaddr *)a, sizeof(*a));
    if (sends_logged < 5) { sends_logged++; logm("sendto %s:%d len %d -> %d (WSA %d)", inet_ntoa(a->sin_addr), ntohs(a->sin_port), len, r, r < 0 ? WSAGetLastError() : 0); }
}

/* Sends now, or after the configured test latency (the network thread sends queued packets). */
static void send_to(const struct sockaddr_in *a, const void *buf, int len)
{
    qitem *q;
    if (lag_ms <= 0 && jitter_ms <= 0) { raw_send(a, buf, len); return; }
    if (qcount >= QSIZE || len > (int)sizeof(q->data)) return;           /* full: drop, like a real network */
    q = &queue[qcount++];
    q->due = now_us() + (uint64_t)((lag_ms + (jitter_ms > 0 ? jitter_ms * (rand() % 1000) / 1000.0 : 0)) * 1000);
    q->to = *a; q->len = len; memcpy(q->data, buf, len);
}

static void flush_queue(uint64_t now)
{
    int i = 0;
    while (i < qcount) {
        if (queue[i].due <= now) { raw_send(&queue[i].to, queue[i].data, queue[i].len); queue[i] = queue[--qcount]; }
        else i++;
    }
}

static void fill_hdr(hdr_t *h, int type, int player)
{
    h->magic = MAGIC; h->type = (uint8_t)type; h->player = (uint8_t)player;
    h->players = (uint8_t)players; h->proto = PROTO;
}

/* One-way latency estimate to peer q in ms (mean + 2 deviations, halved), -1 unknown. */
static double one_way(int q) { return srtt[q] > 0 ? (srtt[q] + 2 * rttvar[q]) / 2 : -1; }

/* Worst one-way latency my input needs to reach every other player, -1 unknown. */
static double my_path_ms(void)
{
    double worst = -1; int q;
    if (is_host) {
        for (q = 2; q <= players; q++) if (one_way(q) > worst) worst = one_way(q);
        return worst;
    }
    if (one_way(1) < 0) return -1;
    return one_way(1) + (players > 2 ? relay_ms : 0);   /* to the host, then relayed to the other client */
}

static void stamp(input_pkt *k, int dest)
{
    uint64_t now = now_us();
    k->ts = (uint32_t)now; if (!k->ts) k->ts = 1;
    k->echo = rx_ts[dest];
    k->echo_age = rx_ts[dest] ? (uint32_t)(now - rx_at[dest]) : 0;
    k->relay_ms = 0;
    if (is_host && players > 2) {
        double worst = 0; int q;
        for (q = 2; q <= players; q++) if (q != dest && one_way(q) > worst) worst = one_way(q);
        k->relay_ms = (uint16_t)(worst + 0.5);
    }
}

/* Send player p's last WINDOW known frames ending at `last` to everyone who should get them. */
static void send_window(int p, int32_t last)
{
    input_pkt k; int32_t first = last - WINDOW + 1; int i;
    if (last < 0) return;
    if (first < 0) first = 0;
    memset(&k, 0, sizeof(k));
    fill_hdr(&k.h, gone[p] ? T_BYE : T_INPUT, p);   /* the host keeps relaying a player who left as BYE */
    k.first = (uint32_t)first; k.count = (uint8_t)(last - first + 1);
    for (i = 0; i < k.count; i++) { k.bits[i] = bits_of[p][(first + i) % RING]; k.aux[i] = aux_of[p][(first + i) % RING]; }
    if (is_host) {
        for (i = 2; i <= players; i++)
            if (have_peer[i] && i != p) { stamp(&k, i); send_to(&peer[i], &k, sizeof(k)); }
    } else if (have_peer[1]) {
        stamp(&k, 1); send_to(&peer[1], &k, sizeof(k));
    }
}

static int32_t last_known(int p)
{
    int32_t f = next_target + 64;             /* search a little ahead of us */
    for (; f >= 0 && f > next_target - RING + 64; f--) if (frame_of[p][f % RING] == f) return f;
    return -1;
}

static void measure(int q, const input_pkt *k, uint64_t now)
{
    rx_ts[q] = k->ts; rx_at[q] = now;
    if (k->echo) {
        int32_t us = (int32_t)((uint32_t)now - k->echo - k->echo_age);
        if (us >= 0 && us < 5000000) {
            double ms = us / 1000.0;
            if (srtt[q] <= 0) { srtt[q] = ms; rttvar[q] = ms / 4; }
            else { rttvar[q] = 0.75 * rttvar[q] + 0.25 * fabs(ms - srtt[q]); srtt[q] = 0.875 * srtt[q] + 0.125 * ms; }
        }
    }
}

/* Status text once player p's goodbye made drnet_exchange give up (lock held). */
static void left_status(int p)
{
    if (players == 2) snprintf(status, sizeof(status), "the other player left the game");
    else snprintf(status, sizeof(status), "player %d left the game", p);
}

/* Runs on the network thread, with the lock held. */
static void handle(const char *buf, int len, const struct sockaddr_in *from, uint64_t now)
{
    const hdr_t *h = (const hdr_t *)buf;
    if (len < (int)sizeof(hdr_t) || h->magic != MAGIC) return;
    if (h->type == T_HELLO || h->type == T_WELCOME) logm("got %s from %s:%d player %d", h->type == T_HELLO ? "HELLO" : "WELCOME", inet_ntoa(from->sin_addr), ntohs(from->sin_port), h->player);
    if (h->proto != PROTO) {
        static int warned;
        if (!warned++) { snprintf(status, sizeof(status), "other player runs a different DeltaruneOnline version"); logm("%s (protocol %d, mine %d)", status, h->proto, PROTO); }
        return;
    }
    if (h->type == T_HELLO && is_host && h->player >= 2 && h->player <= players) {
        hdr_t w;
        peer[h->player] = *from; have_peer[h->player] = 1;
        fill_hdr(&w, T_WELCOME, 1);
        send_to(from, &w, sizeof(w));
    } else if (h->type == T_WELCOME && !is_host) {
        connected = 1;
    } else if ((h->type == T_INPUT || h->type == T_BYE) && len >= (int)sizeof(input_pkt)) {
        const input_pkt *k = (const input_pkt *)buf; int i;
        if (k->h.player < 1 || k->h.player > players || k->h.player == me) return;
        if (is_host) measure(k->h.player, k, now);          /* every packet at the host comes straight from its client */
        else if (k->h.player == 1) { measure(1, k, now); relay_ms = k->relay_ms; }   /* others are relayed */
        for (i = 0; i < k->count && i < WINDOW; i++) put_input(k->h.player, (int32_t)k->first + i, k->bits[i], k->aux[i]);
        if (is_host) {                        /* relay to the other clients right away */
            int q; input_pkt r = *k;
            for (q = 2; q <= players; q++)
                if (have_peer[q] && q != k->h.player) { stamp(&r, q); send_to(&peer[q], &r, sizeof(r)); }
        }
        /* Back after a goodbye only with input newer than the goodbye. An older input packet can still arrive after
           the goodbye (sent just before it, reordered, or held in a queue), and must not undo it (test 4b, 2026-11-23). */
        if (h->type == T_INPUT && gone[k->h.player] && k->count && (int32_t)(k->first + k->count - 1) > bye_last[k->h.player]) {
            gone[k->h.player] = 0;
            logm("player %d sends input again", k->h.player);
        }
        if (h->type == T_BYE && !gone[k->h.player]) {
            gone[k->h.player] = 1;
            bye_last[k->h.player] = k->count ? (int32_t)(k->first + k->count - 1) : -1;
            logm("player %d said goodbye (last frame %d)", k->h.player, bye_last[k->h.player]);
        }
        SetEvent(ev_input);
    } else if (h->type == T_HASH && len >= (int)sizeof(hash_pkt)) {
        const hash_pkt *x = (const hash_pkt *)buf; int s = x->frame % HRING;
        if (x->h.player < 1 || x->h.player > players) return;
        hash_frame[x->h.player][s] = (int32_t)x->frame; hash_val[x->h.player][s] = x->hash;
        if (hash_frame[me][s] == (int32_t)x->frame && hash_val[me][s] != x->hash && desync_at < 0)
            { desync_at = (int32_t)x->frame; logm("DESYNC at frame %d", desync_at); }
        if (is_host) {
            int q;
            for (q = 2; q <= players; q++)
                if (have_peer[q] && q != x->h.player) send_to(&peer[q], x, sizeof(*x));
        }
    }
}

static DWORD WINAPI net_thread(LPVOID unused)
{
    char buf[1024]; struct sockaddr_in from; int fl, n;
    (void)unused;
    while (running) {
        fd_set rs; struct timeval tv;
        FD_ZERO(&rs); FD_SET(sock, &rs);
        tv.tv_sec = 0; tv.tv_usec = 1000;
        n = select(0, &rs, NULL, NULL, &tv);
        lock();
        if (n > 0) {
            for (;;) {
                fl = sizeof(from);
                n = recvfrom(sock, buf, sizeof(buf), 0, (struct sockaddr *)&from, &fl);
                if (n <= 0) break;
                handle(buf, n, &from, now_us());
            }
        } else if (n < 0) {
            static int sel_err;
            if (sel_err++ < 3) logm("select error WSA %d", WSAGetLastError());
            unlock(); Sleep(5); lock();
        }
        flush_queue(now_us());
        unlock();
    }
    return 0;
}

/* Tell the system the game is alive while we block (avoids the "not responding" state).
 * Only peek: dispatching would run GameMaker's window procedure in the middle of a frame,
 * which deadlocked both games in testing. */
static void pump_window_messages(void)
{
    MSG m;
    PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE);
}

static void update_status(void)
{
    double ms = srtt[is_host ? 2 : 1];
    int q;
    if (is_host) for (q = 3; q <= players; q++) if (srtt[q] > ms) ms = srtt[q];
    snprintf(status, sizeof(status), "%s, player %d of %d, ping %s%d ms, delay %d%s", is_host ? "host" : "client", me, players,
             ms > 0 ? "" : "? ", (int)(ms + 0.5), delay, delay_auto ? " (auto)" : "");
}

/* Automatic delay: as many frames as my input needs to reach everyone, minus what fits into a frame.
 * Grows at once (a waiting game would stutter), shrinks only after 3 s of clearly better ping. */
static void update_delay(uint64_t now)
{
    double path = my_path_ms();
    int need, need_tight;
    if (!delay_auto || path < 0) return;
    need = (int)ceil((path - slack_ms) / FRAME_MS);
    need_tight = (int)ceil((path + 5 - slack_ms) / FRAME_MS);
    if (need < 0) need = 0;
    if (need_tight < 0) need_tight = 0;
    if (need > max_delay) need = max_delay;
    if (need > delay) {
        logm("delay %d -> %d (one-way %.1f ms)", delay, need, path);
        delay = need; below_since = 0;
    } else if (need_tight < delay) {
        if (!below_since) below_since = now;
        else if (now - below_since > 3000000) {
            logm("delay %d -> %d (one-way %.1f ms)", delay, delay - 1, path);
            delay--; below_since = 0;
        }
    } else {
        below_since = 0;
    }
}

/* Goodbye: my last WINDOW frames as a BYE packet, so a partner still gets the input it needs to reach my last
 * frame. Sent three times, because UDP may lose one, and straight out (not through the test latency queue).
 * Lock held. */
static void send_bye(void)
{
    input_pkt k; int32_t last = next_target - 1, first = last - WINDOW + 1; int i, r, n = 0;
    if (sock == INVALID_SOCKET || !connected) return;
    if (first < 0) first = 0;
    memset(&k, 0, sizeof(k));
    fill_hdr(&k.h, T_BYE, me);
    if (last >= 0) {
        k.first = (uint32_t)first; k.count = (uint8_t)(last - first + 1);
        for (i = 0; i < k.count; i++) { k.bits[i] = bits_of[me][(first + i) % RING]; k.aux[i] = aux_of[me][(first + i) % RING]; }
    }
    for (r = 0; r < 3; r++) {
        if (is_host) {
            for (i = 2; i <= players; i++)
                if (have_peer[i] && !gone[i]) { stamp(&k, i); raw_send(&peer[i], &k, sizeof(k)); n++; }
        } else if (have_peer[1] && !gone[1]) {
            stamp(&k, 1); raw_send(&peer[1], &k, sizeof(k)); n++;
        }
    }
    if (n) logm("sent goodbye (last frame %d)", last);
}

EXPORT double drnet_option(const char *name, double value)
{
    if (!name) return 0;
    if (!strcmp(name, "lag")) lag_ms = value;
    else if (!strcmp(name, "jitter")) jitter_ms = value;
    else if (!strcmp(name, "maxdelay")) max_delay = value < 0 ? 0 : (value > 30 ? 30 : (int)value);
    else if (!strcmp(name, "slack")) slack_ms = value;
    else return 0;
    logm("option %s = %g", name, value);
    return 1;
}

EXPORT double drnet_set_host(const char *host_ip)
{
    snprintf(host_addr, sizeof(host_addr), "%s", host_ip ? host_ip : "");
    return 1;
}

EXPORT double drnet_start(double host_flag, double port, double nplayers, double my_player, double dly)
{
    const char *host_ip = host_addr;
    WSADATA w; struct sockaddr_in a; u_long nb = 1; int p, i;
    logm("start %s host=%s port=%g players=%g me=%g delay=%g (protocol %d)", host_flag ? "host" : "client", host_ip, port, nplayers, my_player, dly, PROTO);
    if (sock != INVALID_SOCKET) return 1;
    if (!cs_ready) { InitializeCriticalSection(&cs); ev_input = CreateEventA(NULL, FALSE, FALSE, NULL); cs_ready = 1; }
    if (WSAStartup(MAKEWORD(2, 2), &w) != 0) return fail("WSAStartup");
    is_host = host_flag != 0;
    players = (int)nplayers; me = (int)my_player;
    delay_auto = dly < 0; delay = delay_auto ? 0 : (int)dly;
    if (players < 2 || players > MAXP || me < 1 || me > players || delay > 30) { snprintf(status, sizeof(status), "bad settings"); logm("bad settings"); return 0; }
    if (is_host && me != 1) { snprintf(status, sizeof(status), "host must be player 1"); logm("%s", status); return 0; }
    /* DELTARUNE's chapter change (game_change) starts a new game process that inherits open handles, and the old
       process exits without Clean Up. An inherited socket keeps the port bound inside the new process, so hosting
       again fails until that process ends (Phase 1 test, 2026-11-23). So the socket is created non-inheritable;
       SetHandleInformation alone can fail with layered Winsock providers (antivirus, VPN), and that is logged.
       WSA_FLAG_OVERLAPPED keeps it the same kind of socket that socket() makes. */
    sock = WSASocketW(AF_INET, SOCK_DGRAM, IPPROTO_UDP, NULL, 0, WSA_FLAG_OVERLAPPED | WSA_FLAG_NO_HANDLE_INHERIT);
    if (sock == INVALID_SOCKET) {               /* Windows older than 7 SP1 does not know the flag */
        logm("WSASocketW failed (WSA %d), using socket()", WSAGetLastError());
        sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (sock == INVALID_SOCKET) return fail("socket");
        if (!SetHandleInformation((HANDLE)sock, HANDLE_FLAG_INHERIT, 0))
            logm("SetHandleInformation failed (%lu): a chapter change may keep port %g busy", GetLastError(), port);
    }
    ioctlsocket(sock, FIONBIO, &nb);
    memset(&a, 0, sizeof(a)); a.sin_family = AF_INET;
    a.sin_port = htons(is_host ? (u_short)port : 0); a.sin_addr.s_addr = INADDR_ANY;
    if (bind(sock, (struct sockaddr *)&a, sizeof(a)) != 0) { int r = fail("bind"); closesocket(sock); sock = INVALID_SOCKET; return r; }
    for (p = 0; p <= MAXP; p++) {
        have_peer[p] = 0; gone[p] = 0; bye_last[p] = -1; srtt[p] = rttvar[p] = 0; rx_ts[p] = 0;
        for (i = 0; i < RING; i++) frame_of[p][i] = -1;
        for (i = 0; i < HRING; i++) hash_frame[p][i] = -1;
    }
    if (!is_host) {
        memset(&peer[1], 0, sizeof(peer[1])); peer[1].sin_family = AF_INET;
        peer[1].sin_port = htons((u_short)port);
        if (inet_pton(AF_INET, host_ip, &peer[1].sin_addr) != 1) { snprintf(status, sizeof(status), "bad host address %s", host_ip); logm("%s", status); closesocket(sock); sock = INVALID_SOCKET; return 0; }
        have_peer[1] = 1;
    }
    connected = 0; next_target = 0; held_bits = 0; held_aux = next_aux = 0; last_frame = -1; desync_at = -1; qcount = 0;
    srand((unsigned)now_us());
    running = 1;
    thr = CreateThread(NULL, 0, net_thread, NULL, 0, NULL);
    if (!thr) { running = 0; closesocket(sock); sock = INVALID_SOCKET; return fail("CreateThread"); }
    update_status();
    logm("socket ready: %s", status);
    return 1;
}

EXPORT double drnet_connect(double timeout_ms)
{
    DWORD start = GetTickCount(), last_hello = 0, last_note = GetTickCount();
    logm("connect: begin (timeout %g ms, socket %s)", timeout_ms, sock == INVALID_SOCKET ? "INVALID" : "ok");
    if (sock == INVALID_SOCKET) return 0;
    while (GetTickCount() - start < (DWORD)timeout_ms) {
        int done = 0;
        lock();
        if (is_host) {
            int p, all = 1;
            for (p = 2; p <= players; p++) if (!have_peer[p]) all = 0;
            if (all) { connected = 1; done = 1; logm("all players connected"); }
        } else {
            if (connected) { done = 1; logm("connected to host"); }
            else if (GetTickCount() - last_hello > 200) {
                hdr_t h; fill_hdr(&h, T_HELLO, me);
                send_to(&peer[1], &h, sizeof(h)); last_hello = GetTickCount();
            }
        }
        unlock();
        if (done) return 1;
        Sleep(10);
        pump_window_messages();
        if (GetTickCount() - last_note > 5000) { last_note = GetTickCount(); logm("connect: still waiting"); }
    }
    snprintf(status, sizeof(status), "%s: nobody answered in %g s", is_host ? "host" : "client", timeout_ms / 1000);
    logm("%s", status);
    return 0;
}

EXPORT double drnet_set_aux(double value)
{
    next_aux = value > 0 ? (uint32_t)value : 0;
    return 1;
}

/* Puts the local sample of frame f into every frame it now covers (none, one, or several). */
static void assign(int32_t f, uint16_t bits)
{
    int first = 1;
    held_bits |= bits & ~LAG_BIT;
    if (next_aux && !held_aux) held_aux = next_aux;
    next_aux = 0;
    while (next_target <= f + delay) {
        put_input(me, next_target, (uint16_t)(held_bits | (next_target > f ? LAG_BIT : 0)), first ? held_aux : 0);
        next_target++;
        first = 0;
    }
    if (!first) { held_bits = 0; held_aux = 0; }
}

EXPORT double drnet_exchange(double frame, double my_bits)
{
    int32_t f = (int32_t)frame;
    uint64_t start = now_us(), last_send;
    int p, ready, left;
    if (sock == INVALID_SOCKET || !connected) return -1;
    lock();
    update_delay(start);
    assign(f, (uint16_t)my_bits);
    send_window(me, next_target - 1);
    unlock();
    last_send = now_us();
    for (;;) {
        uint64_t now;
        lock();
        ready = 1; left = 0;
        for (p = 1; p <= players; p++) if (!has_input(p, f)) { ready = 0; if (gone[p]) left = p; }
        /* a client gets the other clients' input only through the host: once the host left, nothing missing arrives */
        if (!ready && !left && !is_host && gone[1]) left = 1;
        if (left) left_status(left);
        now = now_us();
        if (!ready && now - last_send > 16000) {             /* resend while waiting */
            send_window(me, next_target - 1);
            if (is_host) for (p = 2; p <= players; p++) send_window(p, last_known(p));
            last_send = now;
        }
        unlock();
        if (ready) break;
        if (left) { logm("exchange: player %d left before frame %d", left, f); return -2; }
        if (now - start > 10000000) { logm("exchange timeout at frame %d", f); return -1; }      /* peer gone */
        WaitForSingleObject(ev_input, 2);
        if (now - start > 100000) pump_window_messages();
    }
    lock();
    if (is_host) for (p = 2; p <= players; p++) send_window(p, last_known(p));
    last_frame = f;
    wait_hist[wait_pos] = (now_us() - start) / 1000.0; wait_pos = (wait_pos + 1) % 30;
    if (f % 15 == 0) update_status();
    {
        double out = 0, mul = 1;
        for (p = 1; p <= MAXP; p++) { out += (p <= players ? bits_of[p][f % RING] : 0) * mul; mul *= 65536.0; }
        unlock();
        return out;
    }
}

EXPORT double drnet_aux(double player)
{
    int p = (int)player;
    double r = 0;
    if (p < 1 || p > MAXP || last_frame < 0) return 0;
    lock();
    if (frame_of[p][last_frame % RING] == last_frame) r = aux_of[p][last_frame % RING];
    unlock();
    return r;
}

EXPORT double drnet_pending_count(void)
{
    return last_frame < 0 ? 0 : next_target - 1 - last_frame;
}

EXPORT double drnet_pending(double i)
{
    int32_t f = last_frame + 1 + (int32_t)i;
    if (last_frame < 0 || i < 0 || f >= next_target) return 0;
    return bits_of[me][f % RING];
}

EXPORT double drnet_delay(void) { return delay; }

EXPORT double drnet_ping(void)
{
    double ms = -1; int q;
    if (is_host) { for (q = 2; q <= players; q++) if (srtt[q] > ms) ms = srtt[q]; }
    else if (srtt[1] > 0) ms = srtt[1];
    return ms;
}

EXPORT double drnet_wait(void)
{
    double s = 0; int i;
    for (i = 0; i < 30; i++) s += wait_hist[i];
    return s / 30;
}

EXPORT double drnet_hash(double frame, double hash)
{
    hash_pkt x; int s = ((int32_t)frame) % HRING, p;
    if (sock == INVALID_SOCKET) return 0;
    lock();
    hash_frame[me][s] = (int32_t)frame; hash_val[me][s] = (uint32_t)(int64_t)hash;
    for (p = 1; p <= players; p++)
        if (p != me && hash_frame[p][s] == (int32_t)frame && hash_val[p][s] != hash_val[me][s] && desync_at < 0)
            { desync_at = (int32_t)frame; logm("DESYNC at frame %d", desync_at); }
    fill_hdr(&x.h, T_HASH, me); x.frame = (uint32_t)frame; x.hash = hash_val[me][s];
    if (is_host) { for (p = 2; p <= players; p++) if (have_peer[p]) send_to(&peer[p], &x, sizeof(x)); }
    else send_to(&peer[1], &x, sizeof(x));
    unlock();
    return 1;
}

EXPORT double drnet_desync(void) { return desync_at; }

EXPORT double drnet_log(const char *msg) { logm("gml: %s", msg ? msg : ""); return 1; }

EXPORT const char *drnet_status(void) { return status; }

EXPORT double drnet_bye(const char *reason)
{
    if (sock == INVALID_SOCKET || !connected) return 0;
    logm("goodbye: %s", reason ? reason : "");
    lock(); send_bye(); unlock();
    Sleep(20);   /* the game usually exits right after this: leave the network stack a moment to send */
    return 1;
}

EXPORT double drnet_stop(void)
{
    if (sock != INVALID_SOCKET) {
        lock(); send_bye(); unlock();
        running = 0;
        if (thr) { WaitForSingleObject(thr, 1000); CloseHandle(thr); thr = NULL; }
        closesocket(sock); sock = INVALID_SOCKET; WSACleanup();
    }
    connected = 0;
    return 1;
}

/* ------------------------------------------------------------------------- */
/* Agent API (untested draft): a separate TCP listener on 127.0.0.1 only.    */
/* One active connection at a time; a second connection is refused.          */
/* The DLL parses the small JSON command subset and queues typed commands.   */
/* The GML side pulls one command per frame, builds state JSON, queues       */
/* input steps, and sends replies.                                           */
/* ------------------------------------------------------------------------- */

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

static void agent_lock(void) { EnterCriticalSection(&agent_cs); }
static void agent_unlock(void) { LeaveCriticalSection(&agent_cs); }

static void agent_ws(const char **p)
{
    while (**p == ' ' || **p == '\t' || **p == '\r' || **p == '\n')
        (*p)++;
}

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
                    int k;
                    if (i < outsz - 1) out[i++] = 'u';
                    for (k = 0; k < 4 && *p; k++) {
                        if (i < outsz - 1) out[i++] = *p;
                        p++;
                    }
                    continue;
                }
                default: return 0;
            }
        }
        if (i < outsz - 1) out[i++] = (char)c;
    }
    if (*p != '"') return 0;
    p++;
    out[i] = '\0';
    *pp = p;
    return 1;
}

static int agent_parse_number_token(const char **pp, char *out, int outsz)
{
    const char *p = *pp;
    int i = 0;
    while ((*p >= '0' && *p <= '9') || *p == '-' || *p == '+' || *p == '.' || *p == 'e' || *p == 'E') {
        if (i < outsz - 1) out[i++] = *p;
        p++;
    }
    if (i == 0) return 0;
    out[i] = '\0';
    *pp = p;
    return 1;
}

static int agent_skip_json_value(const char **pp)
{
    const char *p = *pp;
    int i;
    char tmp[16];
    agent_ws(&p);
    if (*p == '"') return agent_parse_string(&p, tmp, sizeof(tmp));
    if (*p == '{') {
        p++;
        agent_ws(&p);
        if (*p == '}') { p++; *pp = p; return 1; }
        for (;;) {
            agent_ws(&p);
            if (!agent_parse_string(&p, tmp, sizeof(tmp))) return 0;
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
    if (p[0] == 't' && p[1] == 'r' && p[2] == 'u' && p[3] == 'e') { *pp = p + 4; return 1; }
    if (p[0] == 'f' && p[1] == 'a' && p[2] == 'l' && p[3] == 's' && p[4] == 'e') { *pp = p + 5; return 1; }
    if (p[0] == 'n' && p[1] == 'u' && p[2] == 'l' && p[3] == 'l') { *pp = p + 4; return 1; }
    i = 0;
    while (i < (int)sizeof(tmp) - 1 && ((*p >= '0' && *p <= '9') || *p == '-' || *p == '+' || *p == '.' || *p == 'e' || *p == 'E')) {
        tmp[i++] = *p++;
    }
    if (i == 0) return 0;
    *pp = p;
    return 1;
}

static int agent_button_bits(const char *name)
{
    if (!strcmp(name, "down")) return 1;
    if (!strcmp(name, "right")) return 2;
    if (!strcmp(name, "up")) return 4;
    if (!strcmp(name, "left")) return 8;
    if (!strcmp(name, "confirm")) return 16;
    if (!strcmp(name, "cancel")) return 32;
    if (!strcmp(name, "menu")) return 64;
    return -1;
}

static int agent_parse_steps(const char **pp, agent_cmd_t *cmd)
{
    const char *p = *pp;
    if (*p != '[') return 0;
    p++;
    cmd->steps_count = 0;
    for (;;) {
        char key[32];
        int got_buttons = 0, got_frames = 0;
        uint32_t frames = 0;
        uint16_t bits = 0;
        agent_ws(&p);
        if (*p == ']') { p++; *pp = p; return 1; }
        if (*p != '{') return 0;
        p++;
        for (;;) {
            agent_ws(&p);
            if (*p == '}') { p++; break; }
            if (!agent_parse_string(&p, key, sizeof(keys))) return 0;
            agent_ws(&p);
            if (*p != ':') return 0;
            p++;
            agent_ws(&p);
            if (!strcmp(key, "buttons")) {
                if (*p != '[') return 0;
                p++;
                for (;;) {
                    char bname[32];
                    int b;
                    agent_ws(&p);
                    if (*p == ']') { p++; break; }
                    if (!agent_parse_string(&p, bname, sizeof(bname))) return 0;
                    b = agent_button_bits(bname);
                    if (b < 0) return 0;
                    bits |= (uint16_t)b;
                    agent_ws(&p);
                    if (*p == ',') { p++; continue; }
                    if (*p == ']') { p++; break; }
                    return 0;
                }
                got_buttons = 1;
            } else if (!strcmp(key, "frames")) {
                char num[32];
                if (!agent_parse_number_token(&p, num, sizeof(num))) return 0;
                frames = (uint32_t)strtoul(num, NULL, 10);
                got_frames = 1;
            } else {
                if (!agent_skip_json_value(&p)) return 0;
            }
            agent_ws(&p);
            if (*p == ',') { p++; continue; }
            if (*p == '}') { p++; break; }
            return 0;
        }
        if (!got_buttons || !got_frames) return 0;
        cmd->steps_count++;
        if (cmd->steps_count <= AGENT_MAX_STEPS) {
            cmd->steps[cmd->steps_count - 1].buttons = bits;
            cmd->steps[cmd->steps_count - 1].frames = frames;
        }
        agent_ws(&p);
        if (*p == ',') { p++; continue; }
        if (*p == ']') { p++; *pp = p; return 1; }
        return 0;
    }
}

static int agent_parse_cmd(const char *s, agent_cmd_t *cmd)
{
    const char *p = s;
    char key[32];
    agent_ws(&p);
    if (*p != '{') return 0;
    p++;
    memset(cmd, 0, sizeof(*cmd));
    for (;;) {
        agent_ws(&p);
        if (*p == '}') { return 1; }
        if (!agent_parse_string(&p, key, sizeof(key))) return 0;
        agent_ws(&p);
        if (*p != ':') return 0;
        p++;
        agent_ws(&p);
        if (!strcmp(key, "cmd")) {
            char name[16];
            if (!agent_parse_string(&p, name, sizeof(name))) return 0;
            if (!strcmp(name, "state")) cmd->type = 1;
            else if (!strcmp(name, "input")) cmd->type = 2;
            else if (!strcmp(name, "clear")) cmd->type = 3;
            else cmd->type = -1;
        } else if (!strcmp(key, "id")) {
            if (*p == '"') {
                cmd->id_is_string = 1;
                if (!agent_parse_string(&p, cmd->id, AGENT_MAX_ID)) return 0;
            } else {
                cmd->id_is_string = 0;
                if (!agent_parse_number_token(&p, cmd->id, AGENT_MAX_ID)) return 0;
            }
        } else if (!strcmp(key, "steps")) {
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

static void agent_send_raw(SOCKET s, const char *text)
{
    char line[AGENT_MAX_LINE + 96];
    int len = snprintf(line, sizeof(line), "%s\n", text);
    if (len < 0) return;
    if (len >= (int)sizeof(line)) len = (int)sizeof(line) - 1;
    send(s, line, len, 0);
}

static void agent_send_locked(const char *text)
{
    if (agent_conn != INVALID_SOCKET)
        agent_send_raw(agent_conn, text);
}

static void agent_c_json_id(agent_cmd_t *cmd, char *out, int outsz)
{
    if (cmd->id[0]) {
        if (cmd->id_is_string) snprintf(out, outsz, "\"%s\"", cmd->id);
        else snprintf(out, outsz, "%s", cmd->id);
    } else {
        snprintf(out, outsz, "null");
    }
}

static int agent_enqueue_locked(const agent_cmd_t *cmd)
{
    if (agent_cmd_count >= AGENT_MAX_CMDS) return 0;
    agent_cmds[agent_cmd_tail] = *cmd;
    agent_cmd_tail = (agent_cmd_tail + 1) % AGENT_MAX_CMDS;
    agent_cmd_count++;
    return 1;
}

static void agent_handle_line(char *line)
{
    agent_cmd_t cmd;
    if (line[0] == '\0') return;
    if (agent_parse_cmd(line, &cmd) && cmd.type != 0) {
        if (cmd.type == -1) {
            char msg[AGENT_MAX_LINE + 96];
            char idbuf[AGENT_MAX_ID + 16];
            agent_c_json_id(&cmd, idbuf, sizeof(idbuf));
            snprintf(msg, sizeof(msg), "{\"type\":\"error\",\"id\":%s,\"error\":\"unknown command\"}", idbuf);
            agent_send_locked(msg);
        } else if (!agent_enqueue_locked(&cmd)) {
            char msg[AGENT_MAX_LINE + 96];
            char idbuf[AGENT_MAX_ID + 16];
            agent_c_json_id(&cmd, idbuf, sizeof(idbuf));
            snprintf(msg, sizeof(msg), "{\"type\":\"error\",\"id\":%s,\"error\":\"too many queued agent commands\"}", idbuf);
            agent_send_locked(msg);
        }
    } else {
        agent_send_locked("{\"type\":\"error\",\"error\":\"bad command\"}");
    }
}

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
        tv.tv_sec = 0;
        tv.tv_usec = 200000;
        agent_lock();
        l = agent_listen;
        c = agent_conn;
        if (l != INVALID_SOCKET) FD_SET(l, &rs);
        if (c != INVALID_SOCKET) FD_SET(c, &rs);
        agent_unlock();
        n = select(0, &rs, NULL, NULL, &tv);
        if (n <= 0) continue;
        agent_lock();
        if (l != INVALID_SOCKET && FD_ISSET(l, &rs)) {
            SOCKET cl = accept(l, NULL, NULL);
            if (cl != INVALID_SOCKET) {
                u_long nb = 1;
                ioctlsocket(cl, FIONBIO, &nb);
                SetHandleInformation((HANDLE)cl, HANDLE_FLAG_INHERIT, 0);
                if (agent_conn == INVALID_SOCKET) {
                    agent_conn = cl;
                    agent_rlen = 0;
                    agent_discarding = 0;
                } else {
                    agent_send_raw(cl, "{\"type\":\"error\",\"error\":\"an agent connection is already open\"}");
                    closesocket(cl);
                }
            }
        }
        if (c != INVALID_SOCKET && FD_ISSET(c, &rs)) {
            int r = recv(c, buf, sizeof(buf), 0);
            if (r == 0 || (r == SOCKET_ERROR && WSAGetLastError() != WSAEWOULDBLOCK)) {
                closesocket(c);
                if (agent_conn == c) {
                    agent_conn = INVALID_SOCKET;
                    agent_rlen = 0;
                    agent_discarding = 0;
                    agent_cmd_head = agent_cmd_tail = agent_cmd_count = 0;
                }
            } else if (r > 0) {
                agent_process_bytes(buf, r);
            }
        }
        agent_unlock();
    }
    return 0;
}

EXPORT double drnet_agent_start(double port)
{
    WSADATA w;
    struct sockaddr_in a;
    u_long nb = 1;
    int p = (int)port;
    if (agent_listen != INVALID_SOCKET) return 1;
    if (p <= 0 || p > 65535) return 0;
    if (!agent_cs_ready) {
        InitializeCriticalSection(&agent_cs);
        agent_cs_ready = 1;
    }
    if (WSAStartup(MAKEWORD(2, 2), &w) != 0) return fail("agent WSAStartup");
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
        fail("agent bind");
        closesocket(agent_listen);
        agent_listen = INVALID_SOCKET;
        return 0;
    }
    if (listen(agent_listen, 1) != 0) {
        fail("agent listen");
        closesocket(agent_listen);
        agent_listen = INVALID_SOCKET;
        return 0;
    }
    agent_cmd_head = agent_cmd_tail = agent_cmd_count = 0;
    agent_conn = INVALID_SOCKET;
    agent_rlen = 0;
    agent_discarding = 0;
    agent_running = 1;
    agent_thread = CreateThread(NULL, 0, agent_thread_func, NULL, 0, NULL);
    if (!agent_thread) {
        agent_running = 0;
        closesocket(agent_listen);
        agent_listen = INVALID_SOCKET;
        return fail("CreateThread(agent)");
    }
    logm("agent API listening on 127.0.0.1:%d (untested draft)", p);
    return 1;
}

EXPORT double drnet_agent_connected(void)
{
    double r = 0;
    if (agent_cs_ready) {
        agent_lock();
        r = (agent_conn != INVALID_SOCKET);
        agent_unlock();
    }
    return r;
}

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

EXPORT const char *drnet_agent_id(void)
{
    return agent_last_id;
}

EXPORT double drnet_agent_id_is_string(void)
{
    return agent_last_id_is_string;
}

EXPORT double drnet_agent_steps_count(void)
{
    return agent_last_cmd.steps_count;
}

EXPORT double drnet_agent_step_frames(double index)
{
    int i = (int)index;
    if (i < 0 || i >= AGENT_MAX_STEPS || i >= agent_last_cmd.steps_count) return 0;
    return (double)agent_last_cmd.steps[i].frames;
}

EXPORT double drnet_agent_step_buttons(double index)
{
    int i = (int)index;
    if (i < 0 || i >= AGENT_MAX_STEPS || i >= agent_last_cmd.steps_count) return 0;
    return (double)agent_last_cmd.steps[i].buttons;
}

EXPORT double drnet_agent_send(const char *text)
{
    if (!text || !agent_cs_ready) return 0;
    agent_lock();
    agent_send_locked(text);
    agent_unlock();
    return 1;
}
<<<END FILE>>>

<<<FILE gml/scr_dronline.gml>>>
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
//                                              recording then goes to <name>.2, <name>.3, ... With DRONLINE_ROLE
//                                              the role goes into the name: dronline_rec_host.bin
//   agent   = 0                                (host/client/solo) TCP port for the Agent API on 127.0.0.1; 0/off.
//                                              The agent may read state and send input. Untested draft.
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
    global.dro_agent_port = 0;
    // Two copies on one PC share the save folder. With the environment variable DRONLINE_ROLE=<name>
    // (letters and digits, at most 16), a copy reads dronline_<name>.ini instead, if that file exists.
    // drnet.dll follows the same rule for its log (drnet_<name>.log). See tools/local_test.bat.
    global.dro_ini = "dronline.ini";
    global.dro_role = "";
    var role = environment_get_variable("DRONLINE_ROLE");
    if (role != "" && string_length(role) <= 16 && string_lettersdigits(role) == role && file_exists("dronline_" + role + ".ini"))
    {
        global.dro_ini = "dronline_" + role + ".ini";
        global.dro_role = role;
    }
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
        global.dro_agent_port = ini_read_real("online", "agent", 0);
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
    if (global.dro_mode == "host" || global.dro_mode == "client" || global.dro_mode == "solo")
        dronline_agent_init();
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
        // Copies on one PC (DRONLINE_ROLE) share the save folder and can start at the same moment, before either
        // has written its file: the role goes into the name (dronline_rec.bin -> dronline_rec_host.bin), so two
        // copies never pick the same file.
        if (global.dro_role != "")
        {
            var ext = filename_ext(global.dro_srec_file);
            global.dro_srec_file = string_copy(global.dro_srec_file, 1, string_length(global.dro_srec_file) - string_length(ext))
                + "_" + global.dro_role + ext;
        }
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
    // Agent input is OR'ed with the real or scripted local bits, so a person can still step in.
    // This is before script_record, so recordings include the agent's input.
    mine |= dronline_agent_step();
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
    dronline_agent_poll();
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
    // Agent done replies are sent after this frame's input was applied, but before the frame counter
    // advances, so `done.state.frame` is the frame in which the last agent step took effect.
    dronline_agent_done_check();
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

// ---- Agent API (untested draft) ----
// A local TCP agent can read the game state as JSON and queue input steps. The game polls the DLL
// once per frame, so the DLL thread never touches game state. Array/string only: no ds_* or buffer
// creation here, so this stays safe for lockstep.

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
        return;
    }
    var n;
    for (n = 0; n < 8; n++)
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
    var total = 0;
    if (steps < 1 || steps > 60)
        invalid = "at most 60 steps per command";
    else if (active >= 10)
        invalid = "at most 10 queued commands";
    if (invalid == "")
    {
        var s;
        for (s = 0; s < steps; s++)
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
        var s2;
        for (s2 = 0; s2 < steps; s2++)
        {
            var f2 = drnet_agent_step_frames(s2);
            var b2 = drnet_agent_step_buttons(s2);
            var k;
            for (k = 0; k < f2; k++)
                array_push(global.dro_agent_frames, b2);
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
    var i;
    for (i = 0; i < array_length(global.dro_agent_done); i++)
    {
        var d = global.dro_agent_done[i];
        if (global.dro_frame >= d[1])
            drnet_agent_send(dronline_agent_reply_done(d[0], d[2]));
        else
            array_push(next, d);
    }
    global.dro_agent_done = next;
}

function dronline_agent_hex(n)
{
    var hx = "0123456789abcdef";
    n = n mod 256;
    return string_char_at(hx, (n div 16) + 1) + string_char_at(hx, (n mod 16) + 1);
}

function dronline_agent_json_string(s)
{
    var _s = string(s);
    var out = "\"";
    var i;
    for (i = 1; i <= string_length(_s); i++)
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

function dronline_agent_json_id(id, is_string)
{
    if (is_string)
        return dronline_agent_json_string(id);
    if (id == "")
        return "null";
    return string(id);
}

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

function dronline_agent_state_json()
{
    var out = "{";
    var battle_missing = !variable_global_exists("fighting");
    var interact_missing = !variable_global_exists("interact");
    var battle = 0;
    out += "\"frame\":" + string(global.dro_frame) + ",";
    if (variable_global_exists("chapter"))
        out += "\"chapter\":" + string(global.chapter) + ",";
    else
        out += "\"chapter\":null,";
    out += "\"room\":" + dronline_agent_json_string(room_get_name(room)) + ",";
    out += "\"mode\":" + dronline_agent_json_string(global.dro_mode) + ",";
    out += "\"player\":" + string(global.dro_player) + ",";
    out += "\"players\":" + string(global.dro_players) + ",";
    if (global.dro_net)
        out += "\"online\":true,";
    else
        out += "\"online\":false,";
    if (variable_global_exists("dro_desync"))
        out += "\"desync\":" + string(global.dro_desync) + ",";
    else
        out += "\"desync\":null,";
    if (interact_missing)
        out += "\"interact\":null,";
    else
        out += "\"interact\":" + string(global.interact) + ",";
    if (variable_global_exists("menuno"))
        out += "\"menuno\":" + string(global.menuno) + ",";
    else
        out += "\"menuno\":null,";
    if (variable_global_exists("submenu"))
        out += "\"submenu\":" + string(global.submenu) + ",";
    else
        out += "\"submenu\":null,";
    if (battle_missing)
        out += "\"battle\":null,";
    else
    {
        battle = (global.fighting == 1);
        if (battle)
            out += "\"battle\":true,";
        else
            out += "\"battle\":false,";
    }
    if (interact_missing || battle_missing)
        out += "\"can_move\":null,";
    else
    {
        if (global.interact == 0 && !battle)
            out += "\"can_move\":true,";
        else
            out += "\"can_move\":false,";
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

function dronline_agent_players_pos()
{
    var out = "[";
    var first = true;
    var obj = asset_get_index("obj_mainchara");
    if (obj >= 0)
    {
        var inst = instance_find(obj, 0);
        if (inst != noone)
        {
            var nm = "null";
            if (variable_instance_exists(inst, "name"))
                nm = dronline_agent_json_string(variable_instance_get(inst, "name"));
            var face = "null";
            if (variable_global_exists("facing"))
                face = string(global.facing);
            out += (first ? "" : ",") + "{\"player\":1,\"object\":\"obj_mainchara\",\"name\":" + nm + ",\"x\":" + string(variable_instance_get(inst, "x")) + ",\"y\":" + string(variable_instance_get(inst, "y")) + ",\"facing\":" + face + "}";
            first = false;
        }
    }
    var cat = asset_get_index("obj_caterpillarchara");
    if (cat >= 0)
    {
        var n = instance_number(cat);
        var i;
        for (i = 0; i < n; i++)
        {
            var cinst = instance_find(cat, i);
            if (cinst == noone)
                continue;
            var ctrl = variable_instance_get(cinst, "controls");
            if (ctrl == 1 || ctrl == 2)
            {
                var p = ctrl + 1;
                var nm2 = "null";
                if (variable_instance_exists(cinst, "name"))
                    nm2 = dronline_agent_json_string(variable_instance_get(cinst, "name"));
                var fname = (p == 2 ? "facing2" : "facing3");
                var face2 = "null";
                if (variable_global_exists(fname))
                    face2 = string(variable_global_get(fname));
                out += (first ? "" : ",") + "{\"player\":" + string(p) + ",\"object\":" + dronline_agent_json_string(object_get_name(cinst.object_index)) + ",\"name\":" + nm2 + ",\"x\":" + string(variable_instance_get(cinst, "x")) + ",\"y\":" + string(variable_instance_get(cinst, "y")) + ",\"facing\":" + face2 + "}";
                first = false;
            }
        }
    }
    return out + "]";
}

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
            else
            {
                i++;
            }
        }
        else if (c == "/" || c == "%" || c == "|")
        {
            i++;
        }
        else if (c == "^")
        {
            i += 2;
        }
        else if (c == "\\")
        {
            if (i + 1 <= len && string_char_at(_s, i + 1) == "n")
            {
                out += "\n";
                i += 2;
            }
            else
            {
                i += 3;
            }
        }
        else
        {
            out += c;
            i++;
        }
    }
    return out;
}

function dronline_agent_dialogue()
{
    var out = "[";
    var first = true;
    var wobj = asset_get_index("obj_writer");
    if (wobj >= 0)
    {
        var n = instance_number(wobj);
        var i;
        for (i = 0; i < n; i++)
        {
            var w = instance_find(wobj, i);
            if (w == noone)
                continue;
            var raw = "";
            if (variable_instance_exists(w, "mystring"))
                raw = variable_instance_get(w, "mystring");
            var text = dronline_agent_clean_text(raw);
            var len = string_length(string(raw));
            var pos = 1;
            if (variable_instance_exists(w, "pos"))
                pos = variable_instance_get(w, "pos");
            var shown = max(0, min(pos - 1, len));
            var halt = 0;
            if (variable_instance_exists(w, "halt"))
                halt = variable_instance_get(w, "halt");
            out += (first ? "" : ",") + "{\"raw\":" + dronline_agent_json_string(raw) + ",\"text\":" + dronline_agent_json_string(text) + ",\"shown\":" + string(shown) + ",\"length\":" + string(len) + ",\"waiting\":";
            if (halt != 0)
                out += "true";
            else
                out += "false";
            out += ",\"x\":" + string(variable_instance_get(w, "x")) + ",\"y\":" + string(variable_instance_get(w, "y")) + "}";
            first = false;
        }
    }
    return out + "]";
}

function dronline_agent_choice()
{
    var cho = asset_get_index("obj_choicer_neo");
    if (cho < 0)
        return "null";
    var inst = instance_find(cho, 0);
    if (inst == noone)
        return "null";
    var opts = "[]";
    if (variable_global_exists("choicemsg") && variable_global_exists("choicetotal"))
    {
        var total = global.choicetotal;
        var arr = global.choicemsg;
        opts = "[";
        var i;
        for (i = 0; i <= total; i++)
        {
            var msg = "";
            if (i < array_length(arr))
                msg = string(arr[i]);
            var clean = string_hash_to_newline(msg);
            if (i > 0)
                opts += ",";
            opts += dronline_agent_json_string(clean);
        }
        opts += "]";
    }
    var selected = -1;
    if (variable_instance_exists(inst, "mychoice"))
        selected = variable_instance_get(inst, "mychoice");
    var can = 0;
    if (variable_instance_exists(inst, "canchoose"))
        can = variable_instance_get(inst, "canchoose");
    var out = "{\"options\":" + opts + ",\"selected\":" + string(selected) + ",\"can_choose\":";
    if (can == 1)
        out += "true";
    else
        out += "false";
    out += "}";
    return out;
}

function dronline_agent_camera()
{
    var cam = view_camera[0];
    return "{\"x\":" + string(camera_get_view_x(cam)) + ",\"y\":" + string(camera_get_view_y(cam)) + ",\"w\":" + string(camera_get_view_width(cam)) + ",\"h\":" + string(camera_get_view_height(cam)) + "}";
}

function dronline_agent_nearby()
{
    var out = "[";
    var px = 0;
    var py = 0;
    var has = false;
    var obj = asset_get_index("obj_mainchara");
    if (obj >= 0)
    {
        var inst = instance_find(obj, 0);
        if (inst != noone)
        {
            px = variable_instance_get(inst, "x");
            py = variable_instance_get(inst, "y");
            has = true;
        }
    }
    if (!has)
        return "[]";
    var cat = asset_get_index("obj_caterpillarchara");
    var cands = [];
    with (all)
    {
        if (sprite_index >= 0)
        {
            if (object_index != obj && (cat < 0 || object_index != cat))
            {
                var dx = x - px;
                var dy = y - py;
                if (dx * dx + dy * dy <= 240 * 240)
                    array_push(cands, [object_get_name(object_index), x, y, solid, visible, dx * dx + dy * dy]);
            }
        }
    }
    var sorted = [];
    var cands_len = array_length(cands);
    while (cands_len > 0 && array_length(sorted) < 30)
    {
        var best = 0;
        var i;
        for (i = 1; i < cands_len; i++)
        {
            if (cands[i][5] < cands[best][5])
                best = i;
        }
        array_push(sorted, cands[best]);
        var rest = [];
        for (i = 0; i < cands_len; i++)
        {
            if (i != best)
                array_push(rest, cands[i]);
        }
        cands = rest;
        cands_len = array_length(cands);
    }
    for (i = 0; i < array_length(sorted); i++)
    {
        var c = sorted[i];
        if (i > 0)
            out += ",";
        out += "{\"object\":" + dronline_agent_json_string(c[0]) + ",\"x\":" + string(c[1]) + ",\"y\":" + string(c[2]) + ",\"solid\":";
        if (c[3])
            out += "true";
        else
            out += "false";
        out += ",\"visible\":";
        if (c[4])
            out += "true";
        else
            out += "false";
        out += "}";
    }
    return out + "]";
}
<<<END FILE>>>

<<<FILE patcher/utmt/add_extension.csx>>>
// Registers drnet.dll as a GameMaker extension so GML can call drnet_* (used by dronline_patch.py).
// Must run before import_code.csx, so the compiler knows the functions.
using System.Linq;
using System.Collections.Generic;

EnsureDataLoaded();
if (Data.Extensions.Any(e => e.Name?.Content == "drnet"))
{
    Console.WriteLine("EXT_OK drnet already present");
    return;
}
uint nextId = Data.Extensions.SelectMany(e => e.Files).SelectMany(f => f.Functions).Select(fn => fn.ID).DefaultIfEmpty(0u).Max() + 1;
var S = UndertaleExtensionVarType.String;
var D = UndertaleExtensionVarType.Double;
var file = new UndertaleExtensionFile
{
    Filename = Data.Strings.MakeString("drnet.dll"),
    CleanupScript = Data.Strings.MakeString(""),
    InitScript = Data.Strings.MakeString(""),
    Kind = UndertaleExtensionKind.Dll,
};
void Fn(string name, UndertaleExtensionVarType ret, params UndertaleExtensionVarType[] args)
{
    var f = new UndertaleExtensionFunction
    {
        Name = Data.Strings.MakeString(name),
        ExtName = Data.Strings.MakeString(name),
        ID = nextId++,
        Kind = 11,            // same calling convention as the game's own DLL extensions (cdecl)
        RetType = ret,
    };
    foreach (var a in args)
        f.Arguments.Add(new UndertaleExtensionFunctionArg { Type = a });
    file.Functions.Add(f);
    // The compiler only emits a real call for names in the FUNC table; otherwise it compiles a
    // variable call and the game fails with "Variable drnet_... not set before reading it".
    if (Data.Functions.ByName(name) == null)
        Data.Functions.Add(new UndertaleFunction { Name = Data.Strings.MakeString(name) });
}
// GameMaker refuses DLL functions with more than 4 arguments unless all of them are numbers.
Fn("drnet_set_host", D, S);
Fn("drnet_start", D, D, D, D, D, D);
Fn("drnet_connect", D, D);
Fn("drnet_exchange", D, D, D);
Fn("drnet_hash", D, D, D);
Fn("drnet_desync", D);
Fn("drnet_status", S);
Fn("drnet_stop", D);
Fn("drnet_bye", D, S);
Fn("drnet_log", D, S);
Fn("drnet_option", D, S, D);
Fn("drnet_set_aux", D, D);
Fn("drnet_aux", D, D);
Fn("drnet_pending_count", D);
Fn("drnet_pending", D, D);
Fn("drnet_delay", D);
Fn("drnet_ping", D);
Fn("drnet_wait", D);
Fn("drnet_agent_start", D, D);
Fn("drnet_agent_connected", D);
Fn("drnet_agent_command", D);
Fn("drnet_agent_id", S);
Fn("drnet_agent_id_is_string", D);
Fn("drnet_agent_steps_count", D);
Fn("drnet_agent_step_frames", D, D);
Fn("drnet_agent_step_buttons", D, D);
Fn("drnet_agent_send", D, S);
var ext = new UndertaleExtension
{
    Name = Data.Strings.MakeString("drnet"),
    ClassName = Data.Strings.MakeString(""),
    FolderName = Data.Strings.MakeString(""),
    Version = Data.Strings.MakeString("1.0.0"),
};
ext.Files.Add(file);
Data.Extensions.Add(ext);
// GameMaker 2+ keeps one 16-byte product id per extension; the counts must match.
var ids = Data.FORM.EXTN.productIdData;
if (ids != null && ids.Count == Data.Extensions.Count - 1)
    ids.Add(new byte[16]);
Console.WriteLine("EXT_OK drnet added with " + file.Functions.Count + " functions, ids " + (nextId - file.Functions.Count) + ".." + (nextId - 1));
<<<END FILE>>>

<<<FILE config/dronline.ini.example>>>
; Copy to the game's save folder as dronline.ini
;   Windows: %LOCALAPPDATA%\DELTARUNE\dronline.ini
;   Linux/Proton: <prefix>/drive_c/users/steamuser/AppData/Local/DELTARUNE/dronline.ini
; No file or mode=off: the patched game behaves like the unpatched one.
; Step-by-step setup for online play: https://github.com/Czekoloczek/DeltaruneOnline/wiki/Playing-with-a-friend
; Two copies on one PC: start each with DRONLINE_ROLE=<name> to read dronline_<name>.ini instead
;   (tools\local_test.bat does that for a host and a client).
[online]
; host | client: online play (the host is player 1)
; off: like the unpatched game
; solo | record | replay: testing without a network (see docs/DESIGN.md)
mode=off
; local player: 1, 2 or 3 (the host is always 1)
player=1
; shared random seed (all players must use the same one)
seed=1234
; local gamepad index, -1 = first connected
pad=-1
; (host/client) number of players (also sets the Local Multiplayer player count online), host address (client),
; UDP port (host opens it)
players=2
host=192.168.1.10
port=7845
; input delay in frames. auto (default): picked from the measured ping so the game never waits;
; your own SOUL is drawn and hit without delay anyway. A number fixes it (0 = wait for the network
; every frame, which slows the game down above roughly 30 ms ping).
delay=auto
; upper limit for delay=auto
maxdelay=6
; testing only: extra latency in ms added to every packet this machine sends, and random extra 0..jitter ms
lag=0
jitter=0
; (host/client/solo) scripted input for unattended tests: play the local player's input from this file instead of
; the keyboard and gamepad (Esc and F4 still work). Make it with tools/input_script.py from a text script, or
; record it with script_record. Replaying a recording frame for frame needs the same fixed delay=<frames>.
;script=dronline_script.bin
; after the last scripted frame: idle (no input), keys (keyboard and gamepad again) or quit (close the game)
;script_end=idle
; write the local player's input of every frame to this file (an existing file is kept, the new one gets .2, .3, ...;
; with DRONLINE_ROLE the role goes into the name: dronline_rec_host.bin)
;script_record=dronline_rec.bin
; (host/client/solo) TCP port for the Agent API, JSON lines on 127.0.0.1 only. 0/off. Untested draft.
;agent=8765
<<<END FILE>>>

<<<FILE docs/DESIGN.md>>>
# Design

## Where input comes from

DELTARUNE computes all player input once per frame in the **Begin Step of `obj_time`**:
it reads keys from `global.input_k[]` and gamepad buttons from `global.input_g[]` and fills
`global.input_pressed[]`, `global.input_held[]` and `global.input_released[]`.

| Player | Action slots | Default keys |
|---|---|---|
| P1 | 0 down, 1 right, 2 up, 3 left, 4 confirm, 5 cancel, 6 menu, 7–9 alternates (Enter, Shift, Ctrl) | arrows, Z, X, C |
| P2 (mod) | 200–203 directions, 204 confirm, 205 cancel | WASD, J, I |
| P3 (mod) | 300–303 directions, 304 confirm, 305 cancel | TFGH, M, K |
| mod extras | 800 teleport, 801–803 player amount | P, F1–F3 |

Besides that, about 330 direct reads (`keyboard_check*`, `gamepad_*`) are spread over ~66 code entries
of the game and the mod (Chapter 5, game 0.253 + mod v19).

The **mouse** is not part of the virtual input yet. 8 entries in Chapter 5 read it; all but one are editors,
test objects and debug code. The exception is a battle attack: in `obj_attack_blue_flowerbullet` (Step) a flower
bullet explodes as soon as the mouse cursor is over it, so with the cursor in a different place on each screen
the machines would desync. Online, mouse reads must answer the same value everywhere (for example a position far
off screen).

## Virtual input layer (`gml/scr_dronline.gml`)

1. Every machine reads **its own** keyboard and gamepad and turns them into **action bits** of its local
   player, using its own key config (bit 0 down, 1 right, 2 up, 3 left, 4 confirm, 5 cancel, 6 menu,
   7 teleport, 8–10 F1–F3).
2. The bits of all players are combined into a **virtual keyboard**: player *p*'s action *a* presses
   `global.input_k[base_p + a]`. All virtual gamepads report "disconnected", so the game and the mod
   only ever see the virtual keyboard.
3. The patcher rewrites every built-in input read to the `dro_*` functions, which answer from the
   virtual keyboard (or pass through to the real device when the online mode is off).
4. `randomize()` becomes `dro_randomize()`, which seeds every machine with the same seed.

Because every machine feeds the same bits into the same game state, every machine computes the same frame.

## Lockstep

Each frame *F*, a machine sends its bits for frame *F + D* and waits until it has every player's bits
for frame *F*. Waiting has to block inside the frame, which GameMaker's asynchronous networking cannot do, so
the native DLL `native/drnet.c` (Windows build, also under Proton) handles UDP, resends and the blocking wait.
A network thread receives packets the moment they arrive, so the ping is measured exactly and the host relays
input without waiting for its own next frame. Star topology: clients talk only to the host, which relays
everyone's input, so only the host needs an open UDP port (default 7845). Every packet carries the sender's
last 16 frames, so a lost packet is covered by the next one. State fingerprints are exchanged every 30 frames
to detect desyncs.

Neither input delay nor a slowing game is acceptable in DELTARUNE, where a few milliseconds decide whether
a bullet hits, so the design avoids both:

- **Automatic delay, so the game never waits.** Each machine measures its ping (timestamps echoed in every
  packet) and picks its own *D*: the frames its input needs to reach every other player, minus the ~16 ms of a
  frame that a wait can use without delaying the frame. Below roughly 30 ms ping *D* is 0; at 80 ms ping it is
  1–2. *D* grows at once when the ping rises and shrinks after 3 s of clearly better ping. Players do not need
  to agree on it: every frame of every player is filled exactly once (a sample is repeated when *D* grows and
  merged into the next one when it shrinks), and the frames are what everybody agrees on.
- **Own SOUL without delay.** A delay would make your own heart react late, so your own heart is drawn where it
  will be once your already-sent input is applied, and bullets hit it there (next section). Everything else
  is shared state and stays in lockstep.

(Rollback, the textbook answer, would need saving and restoring the complete GameMaker state every frame:
instance ids cannot be recreated and data structure ids are plain numbers, so DELTARUNE gives no way to do it.)

**Goodbye.** A game that closes sends a BYE packet with its last inputs: from obj_time's Game End (window
closed, Esc held, `game_end`) and from `dro_game_change` (chapter change), through `drnet_bye`, and from
`drnet_stop`. The others stop waiting for it as soon as they run out of its input, then show "the other player
left the game" and close, instead of freezing for the 10 s timeout. The last inputs inside the BYE let a partner
that is a frame behind still reach the same last frame. Every machine changes chapter in the same frame, so
`dro_game_change` also marks the game as leaving: a goodbye that arrives then shows no message. `game_restart`
fires Game End too, but the game goes on, so `dro_game_restart` suppresses that goodbye, and a player who sends
input newer than their goodbye counts as back. Older input that arrives after the goodbye (sent just before it, or
reordered) does not. With 3 players, a client gets the other client's input only through the host, so once the host
said goodbye, a client counts any missing input as "left". A crash or a killed process sends nothing, so the 10 s
timeout remains for those.

`delay=<frames>` in `dronline.ini` fixes *D* instead; with a fixed 0 the game waits out the network every
frame and slows down above roughly 30 ms ping.

## Own SOUL

The shared simulation moves a heart *D* frames after its owner pressed the keys. On the owner's screen:

- **Drawing**: obj_heart's Draw temporarily moves the local player's heart to the shared position plus the
  moves already sent for later frames (`drnet_pending`), following obj_heart's red SOUL movement (speed, slow
  walk while cancel is held, walls of the box). Only the heart's own Draw sees that position, so the shared
  state never changes. Other SOUL modes keep the shared position.
- **Hits**: before sending a frame's input, the owner's machine checks whether a bullet touched the heart where
  it was drawn, and sends that bullet's instance number with the input (`drnet_set_aux`). Every machine applies
  it in the same frame through the game's own hit code: heart 1's collision event is skipped and its body runs
  from obj_heart's End Step with the reported bullet as `other`; the `instance_place` checks for hearts 2 and
  3 return the reported bullet. Instance numbers match on every machine because the simulation is identical.
- Frames sent without delay carry no `LAG_BIT` and keep the game's own collisions, since there the shared
  position already is what the owner sees. Purple/mode 7 and other special hit code stay shared too.

The price: the other players' hearts on your screen lag behind what they see by their delay, so now and then a
bullet seems to pass through another player's heart without a hit, or to hit it from a distance. Every hit
follows what the dodging player saw.

`native/test_drnet.c` checks the DLL alone: host and client exchange pseudo-random input and events under
Wine, optionally with test latency, and print running checksums of everything they received; they must match.
Results: no latency → delay 0, 33 ms frames; 30 ms each way → ping 61 ms, delay 1, 33 ms frames; 60 ms each
way → ping 121 ms, delay 2, 33 ms frames; 30 ms each way with a fixed delay 0 → 36 ms frames (the slowdown the
automatic delay avoids). Checksums identical in every run.

Session start: all machines must start from the same files (save, `dr.ini`, key config) and the same seed.
For now copy the host's save folder to the clients by hand; sending it automatically is planned. Two files in that
folder change gameplay and are easy to forget when the transfer is automated:

- **`keyconfig_mod.ini`**, the Local Multiplayer settings: `SHARE_INV` (shared inventory), `EASIER_BOSS`,
  `OLD_RHYTHM` (old rhythm game), `PREF_RALSEI`, `P2_FEATHER_INVERT` / `P3_FEATHER_INVERT`. Colours and
  controller numbers are local only. `PLAYER_AMOUNT` does not need copying: online, the player count comes from
  `players` in `dronline.ini`. It is set when the network starts, and the patcher turns the mod's reads of
  `PLAYER_AMOUNT` (new game, loading a save, the config menu) into `dro_ini_player_amount`, which returns the
  session's count. F1–F3 still change it during play; they travel as input, so every machine applies them.
- **`true_config.ini`**, which holds the language (`LANG`). English and Japanese text have different lengths, so
  dialogue would end on different frames. Without the file the game picks the language from the OS locale, so a
  Japanese Windows starts in Japanese.

The chapter select launcher cannot carry session settings: it builds a fresh parameter list for the chapter
(only `launcher`, `switch_<n>` and `returning_<n>`), so anything passed to `DELTARUNE.exe` is dropped. That is
why the settings live in `dronline.ini`.

## Automated tests (no game needed)

`bash tools/verify.sh` runs all of them in one go, with the lockstep test in three variants: 0 and 30 ms with 2
players, and 3 players. The lockstep test needs Wine on Linux (Git Bash on Windows runs it natively); without
Wine, or with `SKIP_LOCKSTEP=1`, `verify.sh` skips it, says so, and still ends with OK, so read its output.
`bash tools/setup_env.sh` installs the tools with apt, dnf or pacman, and only warns when no Wine package is
available. Cloud agents such as CodeRabbit run `setup_env.sh` as their setup script and are told to check
changes with `verify.sh`.

They also run on every pull request and push to main (`.github/workflows/tests.yml`):

- `python3 -m unittest discover -s tests -v` (standard library only):
  - `tests/test_patcher.py`: the rewrite rules (word boundaries, idempotence, only `PLAYER_AMOUNT` among the mod's
    settings) and the obj_heart hooks, including "all or nothing" when the game's code looks different. The GML
    snippets are written for the tests and only imitate the game's code.
  - `tests/test_consistency.py`: every function the patcher points the game at exists in `gml/scr_dronline.gml`,
    and every `drnet_*` call is exported by `native/drnet.c` and registered in `add_extension.csx` with the same
    types, with no function of more than 4 arguments taking a string (the pitfalls below).
- `sh native/build.sh && sh native/test_pair.sh [lag_ms] [frames] [players]`: runs `test_drnet.exe` as a host and
  its clients and fails unless every side ends with the same checksum of all received input and no desync. On
  Linux it uses Wine (`WINE=…/GE-Proton…/files/bin/wine` tests Proton's Wine), on Windows (Git Bash, `CC=gcc`)
  it runs natively. CI runs it on Linux under Wine and on a real Windows machine, each with 0, 30 and 60 ms test
  latency and with 3 players.

Tests that need the game and the mod (the headless test, record/replay, battle tests) stay local, because those
files must not be in the repository or in CI.

## Testing on one PC

`tools/local_test.sh` runs a host and a client in two Wine prefixes with copies of the same save folder.

On Windows both copies share one save folder (`%LOCALAPPDATA%\DELTARUNE`), so they would read the same
`dronline.ini`. A copy started with the environment variable `DRONLINE_ROLE=<name>` (letters and digits) reads
`dronline_<name>.ini` instead, if that file exists, and `drnet.dll` then logs to `drnet_<name>.log`.
`tools\local_test.bat "<game folder>"` creates `dronline_host.ini` and `dronline_client.ini` (client connects to
127.0.0.1) and starts both copies; choose Chapter 5 in each window. Without the variable, or without the
matching file, a copy uses `dronline.ini` and `drnet.log` as before. Windows passes environment variables on to
child processes, so the role should survive the launcher's chapter change too.

## Testing without a network: record and replay

`mode=record` stores the local bits of every frame in `dronline_input.bin` and writes a state
fingerprint every 30 frames to `dronline_hash_record.txt`. `mode=replay` plays the same bits back and
writes `dronline_hash_replay.txt`. Load the same save both times, then:

```
python3 tools/compare_hashes.py dronline_hash_record.txt dronline_hash_replay.txt
```

If the logs match, the game is deterministic for that stretch of play, which is exactly what lockstep needs.

## Scripted input: unattended tests and replays

For tests with nobody at the keys, each machine can take its own player's input from a file instead of the
keyboard and gamepad. Settings in `[online]` (host, client and solo):

- `script=<file>`: one little-endian u16 of action bits per input frame (the layout of `dronline_local_bits`), the
  same format `mode=record` writes. Esc and F4 still read the real keyboard. Online, an input frame is an
  exchanged frame, so the time spent waiting for the others does not use up the script.
- `script_end=idle|keys|quit`: after the last frame, no input (default), the real keyboard and gamepad again, or
  closing the game the normal way, so the others see "the other player left the game" (test G4 without a person).
- `script_record=<file>`: writes the input this machine used, frame by frame, in the same format. An existing file
  is kept and the recording goes to `<file>.2`, `<file>.3`, ..., because a chapter change starts a new game process.
  Copies on one PC share the save folder, so with `DRONLINE_ROLE` the role goes into the name
  (`dronline_rec.bin` becomes `dronline_rec_host.bin`) and two copies never write the same file.

`tools/input_script.py compile <script.txt> <out.bin>` turns a text script into that file
(`wait 3s`, `tap confirm 2 15`, `20 right`, `10 up+cancel`; see the tool's help), `dump` turns a recording back into
text. To replay a two-player session: record it with the same `delay=<frames>` on every machine (`delay=auto`
picks the delay from the ping, and a different delay moves every input), then give each machine its own
recording as `script=` with that delay and the same saves and seed. The status line shows `script n/total` and
`REC`, and `drnet.log` notes when the script starts and ends.

A script covers one launch of the chapter: after a chapter change the new process starts it from the top again.
The launcher itself is not patched, so a test without people starts Chapter 5 directly, in `chapter5_windows`:
`DELTARUNE.exe -game data.win launcher`. The `launcher` parameter matters: without it the chapter looks for its
music in `chapter5_windows/mus/`, the music stream fails, and the Chapter 5 logo (which follows the music
position) never finishes. Online that is a desync as soon as the other machine's logo moves on (two-PC idle test,
frame 90). A sound that fails to play online is logged in `drnet.log`, and the first 2400 frames log the parts of
every state hash (room, instance count, summed x and y, plot), so the first differing checkpoint shows what differs.

## Agent API (untested draft)

`agent=<port>` in `[online]` (host, client and solo) opens a separate TCP listener on **127.0.0.1 only**.
One agent connection is accepted at a time; a second one is refused with an error. Messages are JSON objects,
one per UTF-8 line, in both directions. An agent can:

- `{"cmd":"state"}` → `{"type":"state","id":…,"state":{…}}`, built in the next game frame.
- `{"cmd":"input","steps":[{"buttons":["right"],"frames":30}, …]}` → queued frames. The game replies with
  `queued` immediately and with `done` plus a state object once the last frame was applied (online that is
  `drnet_delay()` frames later). At most 60 steps, 3600 frames per step and 10 queued input commands.
- `{"cmd":"clear"}` drops all queued input and replies `cleared`.
- Invalid JSON, unknown commands and lines over 64 KB are answered with an `error` object.

The state JSON contains `frame`, `chapter`, `room`, `mode`, `player`, `players`, `online`, `desync`,
`interact`, `menuno`, `submenu`, `battle`, `can_move`, `players_pos`, `dialogue`, `choice`, `camera` and
`nearby`. It is built from plain GML strings and arrays only, with every global read guarded by
`variable_global_exists` and missing values returned as `null`. The DLL thread never touches game state; GML
pulls one parsed command per frame.

`tools/agent_client.py` is a small standard-library client and module for the API. Its `do "<steps>"` command
accepts the `tools/input_script.py` text syntax and converts it to input steps.

**This draft has not been tested against the game yet.**

## Determinism hazards

`tools/scan_determinism.py` lists them. Chapter 5 (game 0.253 + mod v19):

| Hazard | Uses | Entries |
|---|---|---|
| random calls | 2939 | 660 |
| system clock | 94 | 52 |
| audio state | 58 | 40 |
| fps / delta time | 19 | 11 |
| window / display | 17 | 9 |
| file reads | 20 | 11 |
| mouse | 42 | 8 |

Random calls are fine as long as every machine consumes the RNG in the same order with the same seed.
Clock, audio and fps reads are the real risks; record/replay shows which ones matter in practice.

**System clock.** Audio state already goes through the virtual audio clock, but clock reads do not. Of the
52 clock entries in Chapter 5, 30 are Draw events, which only change the picture. Of the other 22, `scr_save`
only writes the date into the save file, and `scr_screenspace` only uses the clock as a cache key for the camera
position and is called from Draw events alone. The remaining **20 are game logic** and need a frame-based clock
(`frames × 1000 / 30` ms): `scr_wave`, `obj_caterpillar_dash`, `obj_climb_susie`,
`obj_cliff_petalwarp` (and its `_old` copy), `obj_dw_flowerydash`, `obj_dw_post_fountain_close`,
`obj_flowery_runner`, `obj_flowery_shooter`, `obj_towery_flowery`, `obj_rotating_bullet_new`, `obj_waterpit`, and the platformer objects `obj_plat_coin`,
`obj_plat_enm_yellow_miniboss`, `obj_plat_finalflowery`, `obj_plat_hanging_key`, `obj_plat_pinatabell`,
`obj_plat_stringed_object`. The rhythm game times itself with `delta_time`, counted under fps / delta time.

**Other chapters** (same scanner, game 0.253 + mod v19, uses / entries):

| Hazard | Ch 1 | Ch 2 | Ch 3 | Ch 4 |
|---|---|---|---|---|
| random calls | 416 / 113 | 1951 / 524 | 2507 / 660 | 2338 / 519 |
| system clock | 1 / 1 | 3 / 3 | 23 / 13 | 133 / 63 |
| (clock outside Draw) | 1 | 3 | 7 | 34 |
| audio state | 2 / 2 | 80 / 45 | 50 / 33 | 74 / 47 |
| fps / delta time | 2 / 2 | 3 / 3 | 14 / 7 | 21 / 13 |
| window / display | 14 / 9 | 15 / 9 | 22 / 13 | 20 / 12 |
| file reads | 10 / 8 | 15 / 12 | 26 / 19 | 19 / 14 |
| mouse | 0 / 0 | 15 / 6 | 34 / 9 | 36 / 8 |

All five chapters share the same input design (`obj_time` Begin Step, slots 0–9 / 200–205 / 300–305), so the
virtual input layer carries over; the work per chapter is mostly these hazards. Chapter 4 has the most clock
reads in game logic.

## Pitfalls found while testing

- **GameMaker DLL functions with more than 4 arguments must take numbers only.** Otherwise the runner
  refuses to bind the function ("has more than 4 parameters and they are not all numbers") and every
  call silently returns 0. That is why the host address is passed separately (`drnet_set_host`).
- **Extension functions must also be in the FUNC table**, or the compiler emits a variable call and the
  game fails with "Variable drnet_... not set before reading it" (`patcher/utmt/add_extension.csx`).
- **Do not dispatch window messages while blocking.** Running GameMaker's window procedure in the
  middle of a frame deadlocked both games; peeking (`PM_NOREMOVE`) is enough to keep the window alive.
- **DELTARUNE fires Game End when it restarts itself at chapter start** (`room_gms_debug_failsafe`
  → `ROOM_INITIALIZE`), so nothing in Game End may close the network socket.
- Under Wine/Proton with two monitors, the game positions its window from the X11 monitor layout, which
  can differ from the Wayland compositor's layout, so windows may open off screen.
- **The socket must not be inheritable.** A chapter change (`game_change()`) starts a new game process that
  inherits the old one's handles, and the old process exits without Clean Up. An inherited socket kept the host
  port bound inside the new process, so hosting again failed. `drnet.c` creates its socket with
  `WSA_FLAG_NO_HANDLE_INHERIT` (making it non-inheritable afterwards passed the Windows test, 2026-11-23).
- **No networking at process exit.** A goodbye sent from `DllMain` (`DLL_PROCESS_DETACH`) was logged as sent
  but never arrived (Windows, 2026-11-23), so it is sent from Game End and the chapter change instead, while the
  game still runs.
- **Same sources, different `data.win`.** `import_code.csx` compiled the files in `Directory.GetFiles` order, which is
  the file system's own order. The import order decides where new strings, functions and variables land, so the same
  commit, input and UTMT gave `3ea8870e…` in one work folder and `64eeeb3f…` in another (2026-11-23). The import is
  now sorted (ordinal). The UTMT version changes the result too (`491b656` gave `96bbf214…`), so every player must
  build with the same UTMT commit, or use one shared build.

## Playing over WireGuard

WireGuard puts both PCs on one private network, so the game works as on a LAN:

- Only the PC that runs the **WireGuard server** needs a forwarded port on its router: WireGuard's own UDP port
  (51820 by default). The game's port needs no forwarding.
- In `dronline.ini` on the clients, `host=` is the game host's **WireGuard address** (for example `10.8.0.1`),
  not its public internet address.
- Windows Firewall often classes the WireGuard network as *Public* and blocks incoming traffic. The game host
  needs an inbound rule for **UDP 7845** (or for `DELTARUNE.exe`) on that network. With the star topology only
  the host needs it.
- Packets are small (16 frames of input per packet), so WireGuard's lower MTU is no problem.
- Any PC can be the game host; it does not have to be the one running the WireGuard server.

## Headless test

`tools/headless_test.sh` starts a host and a client of the patched chapter directly (no launcher) inside
invisible `gamescope --backend headless` instances and prints both `drnet.log` files. Result on Chapter 5
(Proton GE 10-34, one PC): connected at frame 1, 1500 frames in lockstep, no desync.

## Planned: own SOUL and split camera

Both are drawing-only changes, so they must never change game state or consume the shared RNG:

- **Own SOUL**: in battle, draw the local player's heart normally and the other players' hearts with lower
  alpha or an outline. Only the Draw event of the heart objects changes; their positions and collisions stay
  identical on every machine.
- **Split camera**: game logic keeps using the one shared camera (`camerax()`/`cameray()` are read by many
  scripts), so it must stay identical everywhere. The split view is extra rendering on top: when the players
  are further apart than a threshold, each machine additionally draws a second view around its local player.
  Extra views make GameMaker run every Draw event once more per view, and some DELTARUNE Draw events call
  `random()`, so a split decision must be made identically on all machines (it depends only on player
  positions, which are synced) or the extra view must be drawn to a surface without running Draw events.

## Virtual audio clock

Some logic follows the music: the Chapter 5 logo appears on `audio_sound_get_track_position`, rhythm sections
time notes by it, and many scripts wait for `audio_is_playing`. Audio starts with a different latency on every
machine (this caused a 1-pixel desync in the intro), so online every audio state read is answered from the
lockstep frame counter: position = offset + frames since start / 30 × pitch, with pause, resume, stop,
set-position and pitch tracked per sound instance. The real sound still plays normally.

Local-only keys (`Esc` held to quit, `F4` fullscreen) read the real keyboard; they do not change game state.
<<<END FILE>>>

<<<FILE README.md>>>
# DeltaruneOnline

Online multiplayer for DELTARUNE, built on top of the [Local Multiplayer mod](https://gamebanana.com/mods/601376) by Yomsman.
Every player runs the game on their own PC, and the games stay in sync by exchanging only controller input each frame.

> **Status:** Chapter 5 runs in lockstep between two PCs over the internet (a Windows host and a Linux client under
> Proton, about 14 ms ping): over 5 minutes on the file menu without a desync, and when one player quits, the other
> game shows it within 50 ms. Not tested yet: battles, a chapter change, and people actually playing.
> A local JSON Agent API (`agent=<port>`) for AI agents is included as an untested draft; see `docs/DESIGN.md`.

> **Vibecoded:** this project is made with AI (Claude): code, analysis and documentation.

## How it works

- **You build it locally.** The patcher takes files you already have: your copy of DELTARUNE and the Local Multiplayer mod
  downloaded from GameBanana. It applies the changes on your machine. No game or mod files are distributed here.
- **Input lockstep.** All players run the full game from the same save. Each frame, every game sends the pressed buttons
  of its local player and receives everyone else's. Because every game receives the same input, they all compute the
  same result, so no game objects need to be synchronized over the network.
- **No input lag, no slowdown.** Each game measures its ping and sends its input just early enough that nobody ever
  waits, so the game keeps its full 30 fps. Your own SOUL is still drawn without delay, and bullets hit it where you
  see it: your game decides your hits and tells the others, so dodging feels like single player.

### Roadmap

1. **Virtual input layer**: route every keyboard and gamepad read (game and mod) through one layer that returns
   network input for remote players.
2. **Transport**: a small native DLL (UDP, star topology through the host, automatic input delay from the ping).
3. **Determinism**: shared random seed; game logic must not depend on audio playback state or the system clock.
   The virtual audio clock is done; still open are system clock reads in gameplay code (20 entries in Chapter 5)
   and the mouse (a Chapter 5 battle attack reacts to the cursor). See [docs/DESIGN.md](docs/DESIGN.md).
4. **Desync detection**: every N frames compare a hash of the game state (positions, RNG state, flags).
5. **Patcher**: UndertaleModTool CLI scripts that apply the changes to the user's own files.

6. **Own SOUL**: your heart reacts without delay and is hit where you see it (done for the red SOUL); next: your
   character in the overworld and the FIGHT timing bar.
7. **Online-only quality of life** (planned):
   - **your own SOUL stands out in battles**: each player's heart is drawn clearly on their own screen and the
     other players' hearts are dimmed, so several hearts are no longer confused;
   - **shared camera that splits when players drift apart**: one camera while everyone is close, split
     screen once a player walks too far away.
8. **Session start**: send the host's save, mod settings (`keyconfig_mod.ini`) and language to every player
   automatically; for now they are copied by hand.
9. **Chapters 1 to 4**: port the patch; the hazard counts per chapter are in [docs/DESIGN.md](docs/DESIGN.md).

The first prototype targets Chapter 5 and is tested with two copies of the game on one PC before going over a network.

## Usage

Step-by-step guides for players (building, playing with a friend, settings, troubleshooting) are in the
[wiki](https://github.com/Czekoloczek/DeltaruneOnline/wiki).

You need Python 3.11 or newer, the [UndertaleModTool](https://github.com/UnderminersTeam/UndertaleModTool) CLI
and `drnet.dll` (build it with `native/build.sh`). Every player must build UndertaleModTool from the same commit:
a different version writes a different `data.win` from the same files.

The easiest way is `patcher/build.py`. It checks that your Chapter 5 and the mod are exactly the versions it knows,
applies the mod with xdelta3, runs `dronline_patch.py`, and writes `data.win` and `drnet.dll` to
`build/chapter5_windows/`. At the end it prints the SHA-256 of the new `data.win`:

```
python3 patcher/build.py --game "DELTARUNE" --mod "deltarunelocalmultiplayer_v19" \
    --utmt PATH/UndertaleModCli.dll --out build
```

Or run the patcher yourself on a Chapter 5 `data.win` that already has the mod. It writes `chapter5_online.win`
and puts `drnet.dll` next to it:

```
python3 patcher/dronline_patch.py --utmt PATH/UndertaleModCli.dll \
    --data "DELTARUNE/chapter5_windows/data.win" --out chapter5_online.win
```

To install, whichever way you built it:

1. Copy your whole DELTARUNE folder and play from the copy. Leave the Steam folder as it is: Steam can put its own
   files back when it updates or verifies the game, and then your `data.win` no longer matches your partner's.
2. Put both files into the copy's `chapter5_windows` folder: the patched file as `data.win` (rename
   `chapter5_online.win` if you ran the patcher yourself) and `drnet.dll` next to it.
3. Compare the SHA-256 of that `data.win` with every other player's. `build.py` prints it; otherwise run
   `certutil -hashfile data.win SHA256` on Windows or `sha256sum data.win`. If they differ, the games will not stay
   in sync: check that everyone started from the same game, mod and UndertaleModTool versions.

Without a config file the patched game behaves like the original. To enable a mode, copy
`config/dronline.ini.example` to the save folder as `dronline.ini`. See [docs/DESIGN.md](docs/DESIGN.md) for how it
works and how to test determinism.

## Requirements

Every player needs:

- their own copy of DELTARUNE (current Steam version),
- the Local Multiplayer mod by Yomsman, downloaded from [GameBanana](https://gamebanana.com/mods/601376).

## Known challenges

From a review of the Chapter 5 code (game patch 0.253 + Local Multiplayer v19):

- random number calls in about 660 code files,
- about 36 files that depend on audio state (for example the rhythm minigame),
- about 52 files that read the system clock,
- input is read directly in many places of both the game and the mod,
- every chapter is a separate `data.win`, so changes have to be redone after game and mod updates.

## Contributing

- **Found a bug?** Open an issue with the bug report form. Report security problems privately, as described in
  [SECURITY.md](SECURITY.md).
- **Never commit game or mod files**: `data.win`, `.xdelta` patches, decompiled code, sprites, audio, or built output.
  The `.gitignore` blocks the common ones.
- Express changes as code and rules that the patcher applies to the user's local files, not as copies of modified
  game or mod code.
- Say which chapter, game version and mod version a change targets.
- Update the docs your change affects in the same pull request (README, `docs/DESIGN.md`, the ini example, script
  help), and list the wiki pages that need an update in its description. The rules for AI agents and reviewers,
  this one included, are in [CLAUDE.md](CLAUDE.md).
- Before opening a pull request, run `bash tools/verify.sh`: unit tests, the `drnet.dll` build and the lockstep test,
  all without game files. The lockstep test needs Wine on Linux; without it `verify.sh` skips that test and says so.
  `bash tools/setup_env.sh` installs what it needs (Python 3.11+, mingw-w64, Wine).
- Test with two copies of the game on one PC before testing over a network.

## Credits

- **Local Multiplayer mod** by Yomsman, based on the multiplayer mods by Across_the_games and -S-.
- **DELTARUNE** by Toby Fox.

This project is not affiliated with Toby Fox or the authors of the Local Multiplayer mod.
<<<END FILE>>>

<<<FILE tools/agent_client.py>>>
#!/usr/bin/env python3
"""Agent API client for DeltaruneOnline (untested draft).

The game opens a TCP listener on 127.0.0.1 when `agent=<port>` is set in dronline.ini.
Messages are JSON objects, one per line. This client sends `state`, `input` and `clear`
commands and prints the state returned by a `done` reply.

Usage:
  python3 tools/agent_client.py state --port 8765
  python3 tools/agent_client.py do "20 right; tap confirm 2 15" --port 8765

The `do` command accepts the same text syntax as tools/input_script.py, but only the seven
action buttons (down right up left confirm cancel menu) are allowed.
"""
import argparse
import json
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.input_script import BUTTONS, compile_text, button_names, ScriptError  # noqa: E402


class AgentError(RuntimeError):
    pass


def encode_line(obj):
    return json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n"


def decode_line(line):
    return json.loads(line.decode("utf-8"))


def text_to_steps(text):
    """Compile input_script.py text and group consecutive equal frames into steps."""
    frames = compile_text(text)
    steps = []
    i = 0
    while i < len(frames):
        bits = frames[i]
        if bits & ~0x7F:
            raise ValueError("agent input may only use the seven action buttons")
        names = button_names(bits)
        buttons = [] if names == "-" else names.split("+")
        j = i + 1
        while j < len(frames) and frames[j] == bits:
            j += 1
        steps.append({"buttons": buttons, "frames": j - i})
        i = j
    return steps


class AgentClient:
    def __init__(self, host="127.0.0.1", port=8765, timeout=10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self._file = None
        self._next_id = 0

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._file = self.sock.makefile("rb")
        return self

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        self._file = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()
        return False

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    def _send_obj(self, obj):
        self.sock.sendall(encode_line(obj))

    def _read_obj(self):
        while True:
            line = self._file.readline()
            if not line:
                raise EOFError("agent closed the connection")
            if line.strip():
                return decode_line(line)

    def _wait_for(self, wait_type, rid):
        while True:
            msg = self._read_obj()
            if msg.get("type") == "error":
                raise AgentError(msg.get("error", "agent error"))
            if msg.get("type") == wait_type and msg.get("id") == rid:
                return msg

    def state(self):
        rid = self._new_id()
        self._send_obj({"cmd": "state", "id": rid})
        return self._wait_for("state", rid)["state"]

    def clear(self):
        rid = self._new_id()
        self._send_obj({"cmd": "clear", "id": rid})
        self._wait_for("cleared", rid)
        return None

    def input_text(self, text):
        return self.input_steps(text_to_steps(text))

    def input_steps(self, steps):
        rid = self._new_id()
        self._send_obj({"cmd": "input", "id": rid, "steps": steps})
        self._wait_for("queued", rid)
        done = self._wait_for("done", rid)
        return done["state"]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--timeout", type=float, default=10.0)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("state", help="read and print the game state")
    p_do = sub.add_parser("do", help="send input steps and print the state at done")
    p_do.add_argument("steps")
    args = ap.parse_args(argv)
    with AgentClient(args.host, args.port, args.timeout) as client:
        if args.command == "state":
            print(json.dumps(client.state(), indent=2))
        elif args.command == "do":
            try:
                print(json.dumps(client.input_text(args.steps), indent=2))
            except (ValueError, ScriptError) as e:
                ap.exit(2, "error: %s\n" % e)
        else:
            ap.error("unknown command %s" % args.command)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
<<<END FILE>>>

<<<FILE tests/test_agent_client.py>>>
"""Unit tests for tools/agent_client.py (no game, no network)."""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.agent_client import encode_line, decode_line, text_to_steps  # noqa: E402


class StepConversionTests(unittest.TestCase):
    def test_single_hold(self):
        self.assertEqual(text_to_steps("20 right"), [{"buttons": ["right"], "frames": 20}])

    def test_no_buttons(self):
        self.assertEqual(text_to_steps("wait 5"), [{"buttons": [], "frames": 5}])

    def test_group_consecutive(self):
        self.assertEqual(
            text_to_steps("20 right; 5 right"),
            [{"buttons": ["right"], "frames": 25}],
        )

    def test_tap(self):
        self.assertEqual(
            text_to_steps("tap confirm 2 15"),
            [
                {"buttons": ["confirm"], "frames": 1},
                {"buttons": [], "frames": 15},
                {"buttons": ["confirm"], "frames": 1},
                {"buttons": [], "frames": 15},
            ],
        )

    def test_rejects_non_agent_buttons(self):
        with self.assertRaises(ValueError):
            text_to_steps("20 teleport")


class JsonLineFramingTests(unittest.TestCase):
    def test_roundtrip(self):
        obj = {"cmd": "state", "id": 7}
        self.assertEqual(encode_line(obj), b'{"cmd":"state","id":7}\n')
        self.assertEqual(decode_line(encode_line(obj)), obj)

    def test_escaped_text(self):
        obj = {"type": "error", "error": 'a"b\\c\n'}
        decoded = decode_line(encode_line(obj))
        self.assertEqual(decoded, obj)


if __name__ == "__main__":
    unittest.main()
<<<END FILE>>>

