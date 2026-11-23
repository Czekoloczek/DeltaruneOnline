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
 *                                                                    (the host welcomes clients once all are in)
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
 * Agent API (a program on this PC reads the game state and plays; "Agent API" further down, docs/agent-api.md):
 *   drnet_agent_start(port)                                       -> 1 listening on 127.0.0.1:port (TCP), 0 error
 *                                                                    (drnet.log says why)
 *   drnet_agent_frame()                                           -> first thing in a game frame: runs the agent's
 *                                                                    commands; 1 = its replies need the game state,
 *                                                                    so call drnet_agent_state now, 0 = nothing more
 *   drnet_agent_state(json)                                       -> 1 (the game state for those replies)
 *   drnet_agent_bits()                                            -> once per input frame: the agent's action bits
 *                                                                    for it (0 when it has queued none)
 *   drnet_agent_stop()                                            -> 1 (closes the connection and the port: before
 *                                                                    a chapter change, for the next game process)
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
static int32_t sample_frame = -1;           /* frame in which the sample of the last drnet_exchange is applied */
static int sample_put = 1;                  /* frames that sample went into: 0 = held back, over 1 = repeated (the delay changed) */
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
    /* a client repeats HELLO until it is welcomed: log the first one from each address */
    if (h->type == T_WELCOME || (h->type == T_HELLO && h->player >= 1 && h->player <= MAXP &&
        (!have_peer[h->player] || peer[h->player].sin_addr.s_addr != from->sin_addr.s_addr || peer[h->player].sin_port != from->sin_port)))
        logm("got %s from %s:%d player %d", h->type == T_HELLO ? "HELLO" : "WELCOME", inet_ntoa(from->sin_addr), ntohs(from->sin_port), h->player);
    if (h->proto != PROTO) {
        static int warned;
        if (!warned++) { snprintf(status, sizeof(status), "other player runs a different DeltaruneOnline version"); logm("%s (protocol %d, mine %d)", status, h->proto, PROTO); }
        return;
    }
    if (h->type == T_HELLO && is_host && h->player >= 2 && h->player <= players) {
        hdr_t w; int q, all = 1;
        peer[h->player] = *from; have_peer[h->player] = 1;
        /* WELCOME only once every player is in: a welcomed client starts playing, and with 3 players its first
           exchange would wait for a host that still waits for the third, and give up after 10 s. Clients repeat
           HELLO every 200 ms until they are welcomed, so each one gets its WELCOME then. */
        for (q = 2; q <= players; q++) if (!have_peer[q]) all = 0;
        if (all) { fill_hdr(&w, T_WELCOME, 1); send_to(from, &w, sizeof(w)); }
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
    if (players < 2 || players > MAXP || me < 1 || me > players || delay > 30) { snprintf(status, sizeof(status), "bad settings"); logm("bad settings"); WSACleanup(); return 0; }
    if (is_host && me != 1) { snprintf(status, sizeof(status), "host must be player 1"); logm("%s", status); WSACleanup(); return 0; }
    /* DELTARUNE's chapter change (game_change) starts a new game process that inherits open handles, and the old
       process exits without Clean Up. An inherited socket keeps the port bound inside the new process, so hosting
       again fails until that process ends (Phase 1 test, 2026-11-23). So the socket is created non-inheritable;
       SetHandleInformation alone can fail with layered Winsock providers (antivirus, VPN), and that is logged.
       WSA_FLAG_OVERLAPPED keeps it the same kind of socket that socket() makes. */
    sock = WSASocketW(AF_INET, SOCK_DGRAM, IPPROTO_UDP, NULL, 0, WSA_FLAG_OVERLAPPED | WSA_FLAG_NO_HANDLE_INHERIT);
    if (sock == INVALID_SOCKET) {               /* Windows older than 7 SP1 does not know the flag */
        logm("WSASocketW failed (WSA %d), using socket()", WSAGetLastError());
        sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (sock == INVALID_SOCKET) { int r = fail("socket"); WSACleanup(); return r; }
        if (!SetHandleInformation((HANDLE)sock, HANDLE_FLAG_INHERIT, 0))
            logm("SetHandleInformation failed (%lu): a chapter change may keep port %g busy", GetLastError(), port);
    }
    ioctlsocket(sock, FIONBIO, &nb);
    memset(&a, 0, sizeof(a)); a.sin_family = AF_INET;
    a.sin_port = htons(is_host ? (u_short)port : 0); a.sin_addr.s_addr = INADDR_ANY;
    if (bind(sock, (struct sockaddr *)&a, sizeof(a)) != 0) { int r = fail("bind"); closesocket(sock); sock = INVALID_SOCKET; WSACleanup(); return r; }
    for (p = 0; p <= MAXP; p++) {
        have_peer[p] = 0; gone[p] = 0; bye_last[p] = -1; srtt[p] = rttvar[p] = 0; rx_ts[p] = 0;
        for (i = 0; i < RING; i++) frame_of[p][i] = -1;
        for (i = 0; i < HRING; i++) hash_frame[p][i] = -1;
    }
    if (!is_host) {
        memset(&peer[1], 0, sizeof(peer[1])); peer[1].sin_family = AF_INET;
        peer[1].sin_port = htons((u_short)port);
        if (inet_pton(AF_INET, host_ip, &peer[1].sin_addr) != 1) { snprintf(status, sizeof(status), "bad host address %s", host_ip); logm("%s", status); closesocket(sock); sock = INVALID_SOCKET; WSACleanup(); return 0; }
        have_peer[1] = 1;
    }
    connected = 0; next_target = 0; held_bits = 0; held_aux = next_aux = 0; last_frame = sample_frame = -1; sample_put = 1; desync_at = -1; qcount = 0;
    srand((unsigned)now_us());
    running = 1;
    thr = CreateThread(NULL, 0, net_thread, NULL, 0, NULL);
    if (!thr) { int r = fail("CreateThread"); running = 0; closesocket(sock); sock = INVALID_SOCKET; WSACleanup(); return r; }
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
    int first = 1; int32_t from = next_target;
    held_bits |= bits & ~LAG_BIT;
    if (next_aux && !held_aux) held_aux = next_aux;
    next_aux = 0;
    while (next_target <= f + delay) {
        put_input(me, next_target, (uint16_t)(held_bits | (next_target > f ? LAG_BIT : 0)), first ? held_aux : 0);
        next_target++;
        first = 0;
    }
    if (!first) { held_bits = 0; held_aux = 0; }
    /* held back (the delay just shrank): it goes out merged into the next sample, in frame next_target */
    sample_frame = first ? next_target : next_target - 1;
    sample_put = (int)(next_target - from);
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
    if (!cs_ready) return ms;
    lock();
    if (is_host) { for (q = 2; q <= players; q++) if (srtt[q] > 0 && srtt[q] > ms) ms = srtt[q]; }   /* 0 = not measured */
    else if (srtt[1] > 0) ms = srtt[1];
    unlock();
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

/* ---------------------------------------------------------------------------------------------------------------
 * Agent API: an AI agent plays from data instead of screenshots. Protocol and state: docs/agent-api.md.
 *
 * A TCP listener on 127.0.0.1 only, apart from the lockstep socket, speaking JSON lines with one agent at a time.
 * Its thread accepts the connection, reads lines and turns them into commands; it never touches the game.
 * The game thread does the rest through three calls from scr_dronline.gml (and drnet_agent_stop before a chapter
 * change, which closes the connection and the port for the game process that follows):
 *
 *   drnet_agent_frame()   first thing in a game frame. Runs the waiting commands in the order they came: `input`
 *                         joins the input queue ("queued"), `clear` empties it ("cleared"). A `state` command and
 *                         an input that is done need the game state, which only the GML can build: then nothing
 *                         runs yet and the call returns 1.
 *   drnet_agent_state(s)  called right after a 1, with the state as JSON: answers those and runs the rest.
 *   drnet_agent_bits()    once per input frame, where the local player's input is put together: the buttons of
 *                         the next queued frame. They go the way of the keyboard's, so online they pass through
 *                         drnet_exchange like any other input.
 *
 * "done" answers an input once the game has applied its last frame. Offline that is the frame it was taken in.
 * Online it is `sample_frame`, the frame assign() gave that sample, up to `delay` frames later. The answer goes
 * out at the start of the first frame after it, so its state shows what the input did.
 *
 * Everything below is guarded by agent_cs. Only the agent thread closes its sockets.
 * ------------------------------------------------------------------------------------------------------------- */

#define AGENT_MAX_LINE 65536        /* bytes in one command line; a longer one is answered with an error */
#define AGENT_MAX_STEPS 60          /* steps in one input command */
#define AGENT_MAX_FRAMES 3600       /* frames in one step (2 minutes) */
#define AGENT_MAX_INPUTS 10         /* input commands in the queue */
#define AGENT_MAX_CMDS 32           /* commands waiting for the next game frame */
#define AGENT_MAX_FINS 64           /* inputs waiting for their last frame to be applied (the delay is at most 30) */
#define AGENT_MAX_ID 96             /* an id as JSON text */
#define AGENT_MAX_DEPTH 32          /* nesting of a JSON value the reader skips */
#define AGENT_OUT_MAX (4 << 20)     /* bytes of replies waiting to be sent: more, and the agent has stopped reading */
#define AGENT_MAX_BYE 4             /* connections that are being closed politely at one time */

enum { A_STATE = 1, A_INPUT = 2, A_CLEAR = 3, A_ERROR = 4 };

typedef struct { uint16_t bits, frames; } agent_step;
typedef struct {
    int type;
    char id[AGENT_MAX_ID + 1];      /* as JSON text, ASCII only: null, a number, or a string in quotes */
    const char *err;                /* A_ERROR: a line that was refused, and why */
    int nsteps;
    agent_step steps[AGENT_MAX_STEPS];
} agent_cmd;
typedef struct { char id[AGENT_MAX_ID + 1]; int32_t frame; int known; } agent_fin;

static SOCKET agent_listen = INVALID_SOCKET, agent_conn = INVALID_SOCKET;
static CRITICAL_SECTION agent_cs;
static int agent_cs_ready;
static HANDLE agent_thr;
static int agent_quit;                      /* drnet_agent_stop asks the agent thread to close everything and end */
static int agent_busy_logged;               /* the port was busy: logged once, the game tries again every second */
static int agent_first;                     /* this connection has not sent a line of valid JSON yet */
static const char *agent_drop;              /* why the agent thread should close the connection, NULL = keep it */
static char agent_line[AGENT_MAX_LINE + 1]; /* the line being received */
static int agent_line_len, agent_skip;      /* skip: the line was too long, drop the rest of it */
static agent_cmd agent_cmds[AGENT_MAX_CMDS];        /* waiting for the next game frame, oldest first */
static int agent_ncmds;
static agent_cmd agent_inputs[AGENT_MAX_INPUTS];    /* the input queue, [0] is being played */
static int agent_ninputs, agent_step_i, agent_step_used;
static agent_fin agent_fins[AGENT_MAX_FINS];        /* played to the end, "done" not sent yet */
static int agent_nfins;
static unsigned agent_last_bits;            /* what drnet_agent_bits returned last */
static char *agent_out;                     /* replies not sent yet: agent_out[agent_out_pos .. agent_out_len) */
static int agent_out_pos, agent_out_len, agent_out_cap;
static SOCKET agent_bye[AGENT_MAX_BYE];     /* connections that were told to go, see agent_hangup */
static DWORD agent_bye_tick[AGENT_MAX_BYE];
static int agent_nbye;
static int agent_notes;

static void agent_lock(void) { EnterCriticalSection(&agent_cs); }
static void agent_unlock(void) { LeaveCriticalSection(&agent_cs); }

/* To drnet.log, but not without end: a client that reconnects in a loop would fill the file. */
static void agent_note(const char *text)
{
    if (agent_notes < 200) logm("agent: %s", text);
    else if (agent_notes == 200) logm("agent: (no more agent notes in this log)");
    agent_notes++;
}

/* A new connection, or none: nothing of the last one is left. */
static void agent_reset(void)
{
    agent_first = 1; agent_drop = NULL;
    agent_line_len = agent_skip = 0;
    agent_ncmds = agent_ninputs = agent_nfins = 0;
    agent_step_i = agent_step_used = 0;
    agent_last_bits = 0;
    agent_out_pos = agent_out_len = 0;
}

/* ---- replies ---- */

/* Sends what the socket takes now. The agent thread sends the rest once the socket can take more, so a slow
 * reader never makes the game wait. */
static void agent_flush(void)
{
    while (agent_out_pos < agent_out_len && agent_conn != INVALID_SOCKET && !agent_drop) {
        int want = agent_out_len - agent_out_pos;
        int n = send(agent_conn, agent_out + agent_out_pos, want > 65536 ? 65536 : want, 0);
        if (n > 0) agent_out_pos += n;
        else { if (WSAGetLastError() != WSAEWOULDBLOCK) agent_drop = "connection error"; break; }
    }
    if (agent_out_pos >= agent_out_len) agent_out_pos = agent_out_len = 0;
}

/* Adds bytes to the replies waiting to be sent, whatever their size. 1: added. */
static int agent_put(const char *s, int n)
{
    if (agent_conn == INVALID_SOCKET || agent_drop) return 0;
    if (n > AGENT_OUT_MAX - (agent_out_len - agent_out_pos)) { agent_drop = "it does not read its replies"; return 0; }
    if (agent_out_pos > 0 && agent_out_len + n > agent_out_cap) {      /* the room in front, before any more is taken */
        memmove(agent_out, agent_out + agent_out_pos, agent_out_len - agent_out_pos);
        agent_out_len -= agent_out_pos; agent_out_pos = 0;
    }
    if (agent_out_len + n > agent_out_cap) {
        int cap = agent_out_cap ? agent_out_cap : 4096;
        char *b;
        while (cap < agent_out_len + n) cap *= 2;
        b = (char *)realloc(agent_out, cap);
        if (!b) { agent_drop = "out of memory"; return 0; }
        agent_out = b; agent_out_cap = cap;
    }
    memcpy(agent_out + agent_out_len, s, n); agent_out_len += n;
    return 1;
}

static void agent_puts(const char *s) { agent_put(s, (int)strlen(s)); }

/* {"type":"<type>","id":<id> and whatever `more` adds, as one line. `more` is ASCII without quotes to escape. */
static void agent_reply(const char *type, const char *id, const char *more)
{
    agent_puts("{\"type\":\""); agent_puts(type); agent_puts("\",\"id\":"); agent_puts(id);
    agent_puts(more); agent_put("}\n", 2);
}

static void agent_error(const char *id, const char *text)
{
    agent_puts("{\"type\":\"error\",\"id\":"); agent_puts(id);
    agent_puts(",\"error\":\""); agent_puts(text); agent_put("\"}\n", 3);
}

/* A reply that carries the game state: "state" and "done". */
static void agent_reply_state(const char *type, const char *id, const char *state)
{
    int i, n = (int)strlen(state);
    agent_puts("{\"type\":\""); agent_puts(type); agent_puts("\",\"id\":"); agent_puts(id); agent_puts(",\"state\":");
    if (agent_put(state, n)) {              /* one reply is one line, whatever the GML built */
        char *s = agent_out + agent_out_len - n;
        for (i = 0; i < n; i++) if ((unsigned char)s[i] < 0x20) s[i] = ' ';
    }
    agent_put("}\n", 2);
}

/* ---- a small JSON reader for the command lines ---- */

static void aj_ws(const char **pp)
{
    const char *p = *pp;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    *pp = p;
}

static int aj_hex4(const char *p, unsigned *out)
{
    unsigned v = 0; int i;
    for (i = 0; i < 4; i++) {
        char ch = p[i];
        v <<= 4;
        if (ch >= '0' && ch <= '9') v |= (unsigned)(ch - '0');
        else if (ch >= 'a' && ch <= 'f') v |= (unsigned)(ch - 'a' + 10);
        else if (ch >= 'A' && ch <= 'F') v |= (unsigned)(ch - 'A' + 10);
        else return 0;                      /* also the end of the line */
    }
    *out = v;
    return 1;
}

/* One character of a string into `out`. mode 1: as UTF-8. mode 2: as JSON text in plain ASCII (the rest as
 * \uXXXX), which is safe to send back in a reply. *fit goes to 0 when `out` is full, and nothing more is added. */
static void aj_emit(int mode, unsigned cp, char *out, int cap, int *n, int *fit)
{
    char t[16]; int len;
    if (!mode || !*fit) return;
    if (mode == 2) {
        if (cp == '"' || cp == '\\') { t[0] = '\\'; t[1] = (char)cp; len = 2; }
        else if (cp >= 0x20 && cp < 0x7F) { t[0] = (char)cp; len = 1; }
        else if (cp < 0x10000) len = sprintf(t, "\\u%04x", cp);
        else len = sprintf(t, "\\u%04x\\u%04x", 0xD800 + ((cp - 0x10000) >> 10), 0xDC00 + ((cp - 0x10000) & 0x3FF));
    } else if (cp == 0) { t[0] = (char)0xC0; t[1] = (char)0x80; len = 2; }      /* never ends the C string early */
    else if (cp < 0x80) { t[0] = (char)cp; len = 1; }
    else if (cp < 0x800) { t[0] = (char)(0xC0 | (cp >> 6)); t[1] = (char)(0x80 | (cp & 0x3F)); len = 2; }
    else if (cp < 0x10000) {
        t[0] = (char)(0xE0 | (cp >> 12)); t[1] = (char)(0x80 | ((cp >> 6) & 0x3F)); t[2] = (char)(0x80 | (cp & 0x3F)); len = 3;
    } else {
        t[0] = (char)(0xF0 | (cp >> 18)); t[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
        t[2] = (char)(0x80 | ((cp >> 6) & 0x3F)); t[3] = (char)(0x80 | (cp & 0x3F)); len = 4;
    }
    if (*n + len + mode > cap) { *fit = 0; return; }       /* room for the end too: 0, or quote and 0 */
    memcpy(out + *n, t, len); *n += len;
}

/* Reads the JSON string at *pp. mode 0 only checks it, 1 and 2 also write it to `out` (see aj_emit).
 * 0: not a valid JSON string. *fit tells whether `out` was large enough. */
static int aj_string(const char **pp, int mode, char *out, int cap, int *fit)
{
    const char *p = *pp; int n = 0, ok = 1;
    if (*p != '"') return 0;
    p++;
    if (mode == 2) out[n++] = '"';
    for (;;) {
        unsigned cp; unsigned char ch = (unsigned char)*p;
        if (ch == '"') { p++; break; }
        if (ch < 0x20) return 0;            /* a control character, or the line ends inside the string */
        if (ch == '\\') {
            p++;
            switch (*p) {
                case '"': cp = '"'; break;
                case '\\': cp = '\\'; break;
                case '/': cp = '/'; break;
                case 'b': cp = 8; break;
                case 'f': cp = 12; break;
                case 'n': cp = 10; break;
                case 'r': cp = 13; break;
                case 't': cp = 9; break;
                case 'u':
                    if (!aj_hex4(p + 1, &cp)) return 0;
                    p += 4;
                    if (cp >= 0xDC00 && cp <= 0xDFFF) return 0;
                    if (cp >= 0xD800 && cp <= 0xDBFF) {     /* the first half of a pair: the second must follow */
                        unsigned lo;
                        if (p[1] != '\\' || p[2] != 'u' || !aj_hex4(p + 3, &lo) || lo < 0xDC00 || lo > 0xDFFF) return 0;
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        p += 6;
                    }
                    break;
                default: return 0;
            }
            p++;
        } else if (ch < 0x80) {
            cp = ch; p++;
        } else {                            /* UTF-8, checked: the id goes back out and must be valid text */
            int more; unsigned low;
            if ((ch & 0xE0) == 0xC0) { cp = ch & 0x1F; more = 1; low = 0x80; }
            else if ((ch & 0xF0) == 0xE0) { cp = ch & 0x0F; more = 2; low = 0x800; }
            else if ((ch & 0xF8) == 0xF0) { cp = ch & 0x07; more = 3; low = 0x10000; }
            else return 0;
            for (p++; more > 0; more--, p++) {
                if (((unsigned char)*p & 0xC0) != 0x80) return 0;
                cp = (cp << 6) | ((unsigned char)*p & 0x3F);
            }
            if (cp < low || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return 0;
        }
        aj_emit(mode, cp, out, cap, &n, &ok);
    }
    if (mode == 2) out[n++] = '"';
    if (mode) out[n] = 0;
    if (fit) *fit = ok;
    *pp = p;
    return 1;
}

/* Reads the JSON number at *pp; its text goes to `out` when it fits. 0: not a valid JSON number. */
static int aj_number(const char **pp, char *out, int cap, int *fit)
{
    const char *p = *pp, *s = p; int n;
    if (*p == '-') p++;
    if (*p == '0') p++;
    else if (*p >= '1' && *p <= '9') { while (*p >= '0' && *p <= '9') p++; }
    else return 0;
    if (*p == '.') {
        p++;
        if (*p < '0' || *p > '9') return 0;
        while (*p >= '0' && *p <= '9') p++;
    }
    if (*p == 'e' || *p == 'E') {
        p++;
        if (*p == '+' || *p == '-') p++;
        if (*p < '0' || *p > '9') return 0;
        while (*p >= '0' && *p <= '9') p++;
    }
    n = (int)(p - s);
    if (fit) *fit = n < cap;
    if (out && n < cap) { memcpy(out, s, n); out[n] = 0; }
    *pp = p;
    return 1;
}

/* The value of a JSON number's text when it is a whole number from 1 to `max` (also written as 30.0 or 3e1),
 * 0 otherwise. Done by hand: strtod reads "1.5" as 1 where the system's decimal mark is a comma. */
static int aj_whole(const char *s, int max)
{
    double v = 0; int scale = 0, e = 0, neg = 0;
    if (*s == '-') return 0;
    for (; *s >= '0' && *s <= '9'; s++) { if (v < 1e15) v = v * 10 + (*s - '0'); else scale++; }
    if (*s == '.') for (s++; *s >= '0' && *s <= '9'; s++) {
        if (v < 1e15) { v = v * 10 + (*s - '0'); scale--; }
        else if (*s != '0') return 0;       /* a digit beyond what v holds: not a whole number in range */
    }
    if (*s == 'e' || *s == 'E') {
        s++;
        if (*s == '+' || *s == '-') neg = *s++ == '-';
        for (; *s >= '0' && *s <= '9'; s++) if (e < 1000) e = e * 10 + (*s - '0');
        scale += neg ? -e : e;
    }
    for (; scale > 0 && v <= max; scale--) v *= 10;
    for (; scale < 0 && v > 0; scale++) { if (v != floor(v)) return 0; v /= 10; }
    if (v < 1 || v > max || v != floor(v)) return 0;
    return (int)v;
}

/* Skips any JSON value. 0: not valid JSON, or nested deeper than AGENT_MAX_DEPTH. */
static int aj_skip(const char **pp, int depth)
{
    const char *p = *pp;
    aj_ws(&p);
    if (*p == '"') {
        if (!aj_string(&p, 0, NULL, 0, NULL)) return 0;
    } else if (*p == '{' || *p == '[') {
        int obj = *p == '{'; char end = obj ? '}' : ']';
        if (depth >= AGENT_MAX_DEPTH) return 0;
        p++; aj_ws(&p);
        if (*p == end) p++;
        else for (;;) {
            if (obj) {
                aj_ws(&p);
                if (!aj_string(&p, 0, NULL, 0, NULL)) return 0;
                aj_ws(&p);
                if (*p != ':') return 0;
                p++;
            }
            if (!aj_skip(&p, depth + 1)) return 0;
            aj_ws(&p);
            if (*p == ',') { p++; continue; }
            if (*p == end) { p++; break; }
            return 0;
        }
    }
    else if (!strncmp(p, "true", 4)) p += 4;
    else if (!strncmp(p, "false", 5)) p += 5;
    else if (!strncmp(p, "null", 4)) p += 4;
    else if (!aj_number(&p, NULL, 0, NULL)) return 0;
    *pp = p;
    return 1;
}

/* The first thing wrong with a command stays; reading goes on, so that the reply still carries the command's id. */
#define AJ_WRONG(text) do { if (!*err) *err = (text); } while (0)

/* Action bits 0 to 6, the layout of dronline_local_bits in scr_dronline.gml. */
static const char *const agent_buttons[7] = { "down", "right", "up", "left", "confirm", "cancel", "menu" };

/* "buttons": ["right", "cancel"] -> action bits. 0: not valid JSON. */
static int aj_buttons(const char **pp, unsigned *bits, const char **err)
{
    const char *p = *pp;
    *bits = 0;
    if (*p != '[') { AJ_WRONG("buttons must be a list of button names"); return aj_skip(pp, 3); }
    p++; aj_ws(&p);
    if (*p == ']') p++;
    else for (;;) {
        aj_ws(&p);
        if (*p != '"') {
            AJ_WRONG("buttons must be a list of button names");
            if (!aj_skip(&p, 4)) return 0;
        } else {
            char name[16]; int fit, b;
            if (!aj_string(&p, 1, name, sizeof(name), &fit)) return 0;
            for (b = 0; b < 7; b++) if (fit && !strcmp(name, agent_buttons[b])) break;
            if (b < 7) *bits |= 1u << b;
            else AJ_WRONG("unknown button: use down, right, up, left, confirm, cancel or menu");
        }
        aj_ws(&p);
        if (*p == ',') { p++; continue; }
        if (*p == ']') { p++; break; }
        return 0;
    }
    *pp = p;
    return 1;
}

/* "steps": [{"buttons":["right"],"frames":30}, ...] into c. 0: not valid JSON. */
static int aj_steps(const char **pp, agent_cmd *c, const char **err)
{
    const char *p = *pp; int count = 0;
    c->nsteps = 0;
    if (*p != '[') { AJ_WRONG("steps must be a list"); return aj_skip(pp, 1); }
    p++; aj_ws(&p);
    if (*p == ']') p++;
    else for (;;) {
        unsigned bits = 0; int frames = 0, has_buttons = 0, has_frames = 0;
        aj_ws(&p);
        if (*p != '{') {
            AJ_WRONG("every step must be an object with buttons and frames");
            if (!aj_skip(&p, 2)) return 0;
        } else {
            p++; aj_ws(&p);
            if (*p == '}') p++;
            else for (;;) {
                char key[16]; int fit;
                aj_ws(&p);
                if (!aj_string(&p, 1, key, sizeof(key), &fit)) return 0;
                aj_ws(&p);
                if (*p != ':') return 0;
                p++; aj_ws(&p);
                if (fit && !strcmp(key, "buttons")) {
                    has_buttons = 1;
                    if (!aj_buttons(&p, &bits, err)) return 0;
                } else if (fit && !strcmp(key, "frames")) {
                    char num[40];
                    has_frames = 1; frames = 0;
                    if (*p == '-' || (*p >= '0' && *p <= '9')) {
                        if (!aj_number(&p, num, sizeof(num), &fit)) return 0;
                        if (fit) frames = aj_whole(num, AGENT_MAX_FRAMES);
                    } else if (!aj_skip(&p, 3)) return 0;
                    if (!frames) AJ_WRONG("frames must be a whole number from 1 to 3600");
                } else if (!aj_skip(&p, 3)) return 0;
                aj_ws(&p);
                if (*p == ',') { p++; continue; }
                if (*p == '}') { p++; break; }
                return 0;
            }
            if (!has_buttons || !has_frames) AJ_WRONG("every step needs buttons and frames");
        }
        if (count < AGENT_MAX_STEPS) { c->steps[count].bits = (uint16_t)bits; c->steps[count].frames = (uint16_t)frames; }
        count++;
        aj_ws(&p);
        if (*p == ',') { p++; continue; }
        if (*p == ']') { p++; break; }
        return 0;
    }
    if (count < 1 || count > AGENT_MAX_STEPS) AJ_WRONG("steps must hold 1 to 60 steps");
    else c->nsteps = count;
    *pp = p;
    return 1;
}

/* Reads one command line into c. 0: a command. 1: a JSON object that is not a usable command; *err says why and
 * c->id is its id, if it had one. 2: not a JSON object at all. */
static int agent_parse(const char *s, agent_cmd *c, const char **err)
{
    const char *p = s; char name[16]; int has_cmd = 0, has_steps = 0, fit;
    c->type = 0; c->nsteps = 0; c->err = NULL; strcpy(c->id, "null");
    *err = NULL; name[0] = 0;
    aj_ws(&p);
    if (*p != '{') goto bad;
    p++; aj_ws(&p);
    if (*p == '}') p++;
    else for (;;) {
        char key[16];
        aj_ws(&p);
        if (!aj_string(&p, 1, key, sizeof(key), &fit)) goto bad;
        aj_ws(&p);
        if (*p != ':') goto bad;
        p++; aj_ws(&p);
        if (fit && !strcmp(key, "cmd")) {
            has_cmd = 1; name[0] = 0;
            if (*p == '"') {
                if (!aj_string(&p, 1, name, sizeof(name), &fit)) goto bad;
                if (!fit) name[0] = 0;
            } else if (!aj_skip(&p, 1)) goto bad;
        } else if (fit && !strcmp(key, "id")) {
            strcpy(c->id, "null");
            if (*p == '"' || *p == '-' || (*p >= '0' && *p <= '9')) {
                if (!(*p == '"' ? aj_string(&p, 2, c->id, sizeof(c->id), &fit) : aj_number(&p, c->id, sizeof(c->id), &fit))) goto bad;
                if (!fit) { strcpy(c->id, "null"); AJ_WRONG("id is too long"); }
            } else if (!strncmp(p, "null", 4)) p += 4;
            else {
                AJ_WRONG("id must be a string or a number");
                if (!aj_skip(&p, 1)) goto bad;
            }
        } else if (fit && !strcmp(key, "steps")) {
            has_steps = 1;
            if (!aj_steps(&p, c, err)) goto bad;
        } else if (!aj_skip(&p, 1)) goto bad;
        aj_ws(&p);
        if (*p == ',') { p++; continue; }
        if (*p == '}') { p++; break; }
        goto bad;
    }
    aj_ws(&p);
    if (*p) goto bad;                       /* something follows the object */
    if (!has_cmd) AJ_WRONG("no cmd: use state, input or clear");
    else if (!strcmp(name, "state")) c->type = A_STATE;
    else if (!strcmp(name, "clear")) c->type = A_CLEAR;
    else if (!strcmp(name, "input")) { c->type = A_INPUT; if (!has_steps) AJ_WRONG("an input command needs steps"); }
    else AJ_WRONG("unknown cmd: use state, input or clear");
    return *err ? 1 : 0;
bad:
    strcpy(c->id, "null");
    *err = "not a JSON object on one line";
    return 2;
}

/* ---- the agent thread: connections and lines ---- */

/* A command, or a refused line (c->err), joins the ones waiting for the next game frame. The game thread answers
 * them in the order they came, so the replies of a connection keep the order of its lines. Only when more wait
 * than the game keeps is the surplus refused here and now. */
static void agent_queue(const agent_cmd *c)
{
    if (agent_ncmds >= AGENT_MAX_CMDS) agent_error(c->id, "too many commands are waiting for the game");
    else agent_cmds[agent_ncmds++] = *c;
}

/* agent_line holds a complete line. */
static void agent_handle_line(void)
{
    agent_cmd c; const char *err, *p = agent_line; int r;
    aj_ws(&p);
    if (!*p) return;                        /* an empty line */
    r = agent_parse(agent_line, &c, &err);
    if (r == 2 && agent_first) {
        /* A web page can make a browser send an HTTP request to this port, with command lines in its body. The
           request starts with a line that is not JSON, and the page cannot change that: such a connection ends here. */
        agent_error(c.id, err); agent_flush();
        agent_drop = "its first line was not JSON";
        return;
    }
    if (r != 2) agent_first = 0;
    if (r) { c.type = A_ERROR; c.err = err; }
    agent_queue(&c);
}

static void agent_feed(const char *buf, int n)
{
    int i;
    for (i = 0; i < n && !agent_drop; i++) {
        char ch = buf[i];
        if (ch == '\n') {
            if (!agent_skip) {
                if (agent_line_len && agent_line[agent_line_len - 1] == '\r') agent_line_len--;
                agent_line[agent_line_len] = 0;
                agent_handle_line();
            }
            agent_line_len = agent_skip = 0;
        } else if (agent_skip) {
            /* the rest of a line that was too long */
        } else if (agent_line_len >= AGENT_MAX_LINE) {
            static const char longer[] = "line longer than 64 KB";
            agent_skip = 1;
            if (agent_first) { agent_error("null", longer); agent_flush(); agent_drop = "its first line was not JSON"; }
            else {
                agent_cmd c;
                c.type = A_ERROR; c.err = longer; c.nsteps = 0; strcpy(c.id, "null");
                agent_queue(&c);
            }
        } else {
            agent_line[agent_line_len++] = ch ? ch : 1;     /* a 0 byte is not JSON; as 1 the reader refuses it */
        }
    }
}

/* Ends a connection so that the last line sent to it still arrives. Closing a socket at once, with something of
 * the peer's unread, resets the connection, and the reset can overtake that line ("another agent is connected").
 * So only this side is shut here; the agent thread closes the socket once the peer has hung up too, or after half
 * a second. */
static void agent_hangup(SOCKET s)
{
    shutdown(s, SD_SEND);
    if (agent_nbye >= AGENT_MAX_BYE) { closesocket(s); return; }
    agent_bye[agent_nbye] = s; agent_bye_tick[agent_nbye] = GetTickCount();
    agent_nbye++;
}

/* Every round of the agent thread: reads away what the connections in agent_bye still send and closes them. */
static void agent_bye_poll(void)
{
    char buf[1024]; int i, k, n = 0;
    for (i = 0; i < agent_nbye; i++) {
        int open = GetTickCount() - agent_bye_tick[i] < 500;
        for (k = 0; open && k < 64; k++) {
            int r = recv(agent_bye[i], buf, sizeof(buf), 0);
            if (r > 0) continue;
            if (r == 0 || WSAGetLastError() != WSAEWOULDBLOCK) open = 0;    /* the peer hung up, or an error */
            break;
        }
        if (open) { agent_bye[n] = agent_bye[i]; agent_bye_tick[n] = agent_bye_tick[i]; n++; }
        else closesocket(agent_bye[i]);
    }
    agent_nbye = n;
}

static void agent_close(const char *why)
{
    char note[160];
    if (agent_conn == INVALID_SOCKET) return;
    agent_hangup(agent_conn);
    agent_conn = INVALID_SOCKET;
    agent_reset();                          /* its queued input stops with it */
    snprintf(note, sizeof(note), "disconnected (%s)", why);
    agent_note(note);
}

static void agent_accept(void)
{
    static const char busy[] = "{\"type\":\"error\",\"id\":null,\"error\":\"another agent is connected\"}\n";
    u_long nb = 1; BOOL yes = TRUE;
    SOCKET s = accept(agent_listen, NULL, NULL);
    if (s == INVALID_SOCKET) return;
    ioctlsocket(s, FIONBIO, &nb);
    SetHandleInformation((HANDLE)s, HANDLE_FLAG_INHERIT, 0);    /* not for the game process a chapter change starts */
    if (agent_conn != INVALID_SOCKET) {
        send(s, busy, (int)sizeof(busy) - 1, 0);
        agent_hangup(s);
        agent_note("refused a second connection");
        return;
    }
    setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char *)&yes, sizeof(yes));   /* replies are short: send at once */
    agent_reset();
    agent_conn = s;
    agent_note("connected");
}

static DWORD WINAPI agent_thread(LPVOID unused)
{
    char buf[4096];
    (void)unused;
    for (;;) {
        fd_set rs, ws; struct timeval tv; SOCKET c; int n, i;
        agent_lock();
        if (agent_quit) {
            /* Closed hard, with a reset: nothing of the connection may stay behind. Windows does not let the next
               game process listen on this port (SO_EXCLUSIVEADDRUSE) while a connection accepted here still lives,
               and a politely closed one lives until the agent closes its side too. */
            struct linger hard; hard.l_onoff = 1; hard.l_linger = 0;
            if (agent_conn != INVALID_SOCKET) {
                setsockopt(agent_conn, SOL_SOCKET, SO_LINGER, (const char *)&hard, sizeof(hard));
                closesocket(agent_conn); agent_conn = INVALID_SOCKET;
            }
            for (i = 0; i < agent_nbye; i++) {
                setsockopt(agent_bye[i], SOL_SOCKET, SO_LINGER, (const char *)&hard, sizeof(hard));
                closesocket(agent_bye[i]);
            }
            agent_nbye = 0;
            closesocket(agent_listen); agent_listen = INVALID_SOCKET;
            agent_reset();
            agent_quit = 0;
            agent_unlock();
            return 0;
        }
        if (agent_drop) agent_close(agent_drop);
        c = agent_conn;
        FD_ZERO(&rs); FD_ZERO(&ws);
        FD_SET(agent_listen, &rs);
        if (c != INVALID_SOCKET) {
            FD_SET(c, &rs);
            if (agent_out_pos < agent_out_len) FD_SET(c, &ws);
        }
        agent_unlock();
        tv.tv_sec = 0; tv.tv_usec = 20000;
        n = select(0, &rs, &ws, NULL, &tv);
        if (n < 0) Sleep(20);
        agent_lock();
        agent_bye_poll();
        if (n <= 0) { agent_unlock(); continue; }
        if (c != INVALID_SOCKET) {
            if (FD_ISSET(c, &ws)) agent_flush();
            /* The open connection first: when it has just closed, the next one is welcome in the same round.
               At most 256 KB per round, so the game thread never waits long for the lock. */
            for (i = 0; FD_ISSET(c, &rs) && i < 64 && !agent_drop; i++) {
                n = recv(c, buf, sizeof(buf), 0);
                if (n > 0) agent_feed(buf, n);
                else if (n == 0) { agent_close("closed by the agent"); break; }
                else { if (WSAGetLastError() != WSAEWOULDBLOCK) agent_close("connection error"); break; }
            }
            agent_flush();
            if (agent_drop) agent_close(agent_drop);
        }
        if (FD_ISSET(agent_listen, &rs)) agent_accept();
        agent_unlock();
    }
    return 0;
}

/* ---- the game thread ---- */

/* Answers the inputs that are done and runs the waiting commands in the order they came. `state` is the game
 * state as JSON; without it everything stops at the first reply that needs it. Once per game frame. */
static void agent_run(const char *state)
{
    char more[64]; int i, k, n = 0;
    if (state) {
        for (i = 0; i < agent_nfins; i++) {
            if (agent_fins[i].known && agent_fins[i].frame <= last_frame) agent_reply_state("done", agent_fins[i].id, state);
            else agent_fins[n++] = agent_fins[i];
        }
        agent_nfins = n;
    }
    for (i = 0; i < agent_ncmds; i++) {
        agent_cmd *c = &agent_cmds[i];
        if (c->type == A_STATE) {
            if (!state) break;
            agent_reply_state("state", c->id, state);
        } else if (c->type == A_ERROR) {
            agent_error(c->id, c->err);
        } else if (c->type == A_CLEAR) {    /* nothing sent before it gets a "done" any more */
            agent_ninputs = agent_nfins = 0;
            agent_step_i = agent_step_used = 0;
            agent_last_bits = 0;
            agent_reply("cleared", c->id, "");
        } else if (agent_ninputs >= AGENT_MAX_INPUTS) {
            agent_error(c->id, "10 input commands are queued already");
        } else {
            long total = 0;
            for (k = 0; k < c->nsteps; k++) total += c->steps[k].frames;
            agent_inputs[agent_ninputs++] = *c;
            snprintf(more, sizeof(more), ",\"frames\":%ld", total);
            agent_reply("queued", c->id, more);
        }
    }
    if (i > 0) {
        agent_ncmds -= i;
        memmove(agent_cmds, agent_cmds + i, agent_ncmds * sizeof(agent_cmd));
    }
    agent_flush();
}

EXPORT double drnet_agent_start(double port)
{
    WSADATA w; struct sockaddr_in a; u_long nb = 1; BOOL yes = TRUE;
    if (agent_listen != INVALID_SOCKET) return 1;
    if (!(port >= 1 && port <= 65535)) { logm("agent: %g is not a port (1 to 65535)", port); return 0; }
    if (!agent_cs_ready) { InitializeCriticalSection(&agent_cs); agent_cs_ready = 1; }
    if (WSAStartup(MAKEWORD(2, 2), &w) != 0) { logm("agent: WSAStartup failed"); return 0; }
    /* Not inheritable, like the lockstep socket: the game process a chapter change starts listens on this port again. */
    agent_listen = WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_OVERLAPPED | WSA_FLAG_NO_HANDLE_INHERIT);
    if (agent_listen == INVALID_SOCKET) {
        logm("agent: WSASocketW failed (WSA %d), using socket()", WSAGetLastError());
        agent_listen = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (agent_listen == INVALID_SOCKET) { logm("agent: socket failed (WSA %d)", WSAGetLastError()); return 0; }
        if (!SetHandleInformation((HANDLE)agent_listen, HANDLE_FLAG_INHERIT, 0))
            logm("agent: SetHandleInformation failed (%lu): a chapter change may keep port %d busy", GetLastError(), (int)port);
    }
    /* No other program may bind the same port beside this socket and take the agent's connections. */
    setsockopt(agent_listen, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char *)&yes, sizeof(yes));
    ioctlsocket(agent_listen, FIONBIO, &nb);
    memset(&a, 0, sizeof(a)); a.sin_family = AF_INET;
    a.sin_port = htons((u_short)port);
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);     /* this computer only, never the network */
    if (bind(agent_listen, (struct sockaddr *)&a, sizeof(a)) != 0 || listen(agent_listen, 4) != 0) {
        if (!agent_busy_logged) logm("agent: cannot listen on 127.0.0.1:%d (WSA %d)", (int)port, WSAGetLastError());
        agent_busy_logged = 1;
        closesocket(agent_listen); agent_listen = INVALID_SOCKET;
        WSACleanup();
        return 0;
    }
    agent_busy_logged = 0;
    agent_reset();
    agent_thr = CreateThread(NULL, 0, agent_thread, NULL, 0, NULL);
    if (!agent_thr) {
        logm("agent: CreateThread failed (%lu)", GetLastError());
        closesocket(agent_listen); agent_listen = INVALID_SOCKET;
        WSACleanup();
        return 0;
    }
    logm("agent: listening on 127.0.0.1:%d", (int)port);
    return 1;
}

/* Before a chapter change: the game process that follows listens on the same port. */
EXPORT double drnet_agent_stop(void)
{
    if (agent_listen == INVALID_SOCKET) return 1;
    agent_lock(); agent_quit = 1; agent_unlock();
    if (agent_thr) {                        /* it looks at agent_quit at least every 20 ms */
        WaitForSingleObject(agent_thr, 2000);
        CloseHandle(agent_thr); agent_thr = NULL;
    }
    logm("agent: stopped");
    return 1;
}

EXPORT double drnet_agent_frame(void)
{
    int i, need = 0, online = sock != INVALID_SOCKET && connected;
    if (agent_listen == INVALID_SOCKET) return 0;
    agent_lock();
    for (i = 0; i < agent_nfins; i++) {
        /* Its last frame went out with the previous frame's input. Offline the game applied it there and then
           (-1: any frame is later); online the lockstep gave it the frame it is applied in. A sample the lockstep
           held back (sample_put 0) has no frame yet: it goes out with the next one, perhaps into several frames,
           and only then is the last of them known. */
        if (!agent_fins[i].known && (!online || sample_put > 0)) {
            agent_fins[i].frame = online ? sample_frame : -1; agent_fins[i].known = 1;
        }
        if (agent_fins[i].known && agent_fins[i].frame <= last_frame) need = 1;
    }
    for (i = 0; i < agent_ncmds && !need; i++) if (agent_cmds[i].type == A_STATE) need = 1;
    if (!need) agent_run(NULL);
    agent_unlock();
    return need;
}

EXPORT double drnet_agent_state(const char *json)
{
    if (agent_listen == INVALID_SOCKET) return 0;
    agent_lock();
    agent_run(json && *json ? json : "null");
    agent_unlock();
    return 1;
}

/* Online with delay=auto the lockstep does not always give a sample exactly one frame (assign): when the delay
 * shrinks it holds the sample back and sends it merged with the next one, and when the delay grows it repeats the
 * sample in the frames that opened up. For a keyboard that is fine. A queued input would lose a frame in the first
 * case (two taps one frame apart became one press) and gain frames in the second. So after a held sample the same
 * buttons are given once more instead of the next frame's, and after a repeated one that many frames of the step
 * being played are left out, as long as one frame of it stays. Presses are never lost or merged; a step can still
 * last longer, by as many frames as the delay grew (usually one), when it grows during the step's last frames. */
EXPORT double drnet_agent_bits(void)
{
    unsigned bits = 0; int skip, online = sock != INVALID_SOCKET && connected;
    if (agent_listen == INVALID_SOCKET) return 0;
    agent_lock();
    if (online && sample_put == 0) {
        bits = agent_last_bits;
    } else if (agent_ninputs > 0) {
        agent_cmd *c = &agent_inputs[0];
        /* agent_step_used > 0: the repeated sample was a frame of this very step */
        for (skip = online ? sample_put - 1 : 0; skip > 0 && agent_step_used > 0 && agent_step_used < c->steps[agent_step_i].frames - 1; skip--)
            agent_step_used++;
        bits = c->steps[agent_step_i].bits;
        if (++agent_step_used >= c->steps[agent_step_i].frames) {
            agent_step_used = 0;
            if (++agent_step_i >= c->nsteps) {      /* its last frame: "done" follows when the game has applied it */
                agent_step_i = 0;
                if (agent_nfins < AGENT_MAX_FINS) {
                    strcpy(agent_fins[agent_nfins].id, c->id);
                    agent_fins[agent_nfins].frame = 0; agent_fins[agent_nfins].known = 0;
                    agent_nfins++;
                }
                agent_ninputs--;
                memmove(agent_inputs, agent_inputs + 1, agent_ninputs * sizeof(agent_cmd));
            }
        }
    }
    agent_last_bits = bits;
    agent_unlock();
    return bits;
}
