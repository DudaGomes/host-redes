import argparse, random, socket, struct, threading, time
from collections import deque

def scramble_payload(payload: bytes, mode: str = "shuffle") -> bytes:
    if not payload: return payload
    if mode == "shuffle":
        lst = list(payload); random.shuffle(lst); return bytes(lst)
    elif mode == "xor":
        key = random.randrange(1, 256)
        return bytes([b ^ key for b in payload])
    elif mode == "bitflip":
        out = bytearray(payload)
        for _ in range(random.randint(1, 3)):
            i = random.randrange(len(out))
            bit = 1 << random.randrange(8)
            out[i] ^= bit
        return bytes(out)
    return payload

def maybe(delay_mean):
    if delay_mean > 0: time.sleep(random.expovariate(1.0 / delay_mean))


def parse_seq_list(spec: str):
    s = set()
    if not spec:
        return s
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            try:
                a_i = int(a); b_i = int(b)
            except Exception:
                continue
            if a_i <= b_i:
                s.update(range(a_i, b_i + 1))
        else:
            try:
                s.add(int(part))
            except Exception:
                continue
    return s

class UDPRouter:
    def __init__(self, router_host, router_port,
                 sender_host, sender_port,
                 receiver_host, receiver_port,
                 p_corrupt_fwd, p_drop_fwd, p_dup_fwd,
                 p_reorder_fwd, delay_mean_fwd, scramble_mode_fwd,
                 p_drop_back, p_dup_back, delay_mean_back,
                 reorder_window,
                 force_drop_seqs=None, force_corrupt_seqs=None, force_dup_seqs=None, force_reorder_seqs=None):
        self.router_addr = (router_host, router_port)
        self.sender_addr = (sender_host, sender_port)
        self.receiver_addr = (receiver_host, receiver_port)
        self.sock_fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_back = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_fwd.bind(self.router_addr)
        self.sock_back.bind(("0.0.0.0", 9003))
        self.p_corrupt_fwd = p_corrupt_fwd
        self.p_drop_fwd = p_drop_fwd
        self.p_dup_fwd = p_dup_fwd
        self.p_reorder_fwd = p_reorder_fwd
        self.delay_mean_fwd = delay_mean_fwd
        self.scramble_mode_fwd = scramble_mode_fwd
        self.p_drop_back = p_drop_back
        self.p_dup_back = p_dup_back
        self.delay_mean_back = delay_mean_back
        self.reorder_window = max(0, reorder_window)
        self.buffer_reorder = deque()
        self.lock = threading.Lock()
        self.running = True
        # forced sequences to deterministically apply errors
        self.force_drop_seqs = set(force_drop_seqs or [])
        self.force_corrupt_seqs = set(force_corrupt_seqs or [])
        self.force_dup_seqs = set(force_dup_seqs or [])
        self.force_reorder_seqs = set(force_reorder_seqs or [])

    # --- runtime control of forced rules ---
    def add_forced(self, typ: str, seqs):
        if typ == 'drop':
            self.force_drop_seqs.update(seqs)
        elif typ == 'corrupt':
            self.force_corrupt_seqs.update(seqs)
        elif typ == 'dup':
            self.force_dup_seqs.update(seqs)
        elif typ == 'reorder':
            self.force_reorder_seqs.update(seqs)

    def remove_forced(self, typ: str, seqs):
        target = None
        if typ == 'drop':
            target = self.force_drop_seqs
        elif typ == 'corrupt':
            target = self.force_corrupt_seqs
        elif typ == 'dup':
            target = self.force_dup_seqs
        elif typ == 'reorder':
            target = self.force_reorder_seqs
        if target is not None:
            for s in seqs:
                target.discard(s)

    def clear_forced(self, typ: str = None):
        if typ is None:
            self.force_drop_seqs.clear(); self.force_corrupt_seqs.clear(); self.force_dup_seqs.clear(); self.force_reorder_seqs.clear()
        elif typ == 'drop':
            self.force_drop_seqs.clear()
        elif typ == 'corrupt':
            self.force_corrupt_seqs.clear()
        elif typ == 'dup':
            self.force_dup_seqs.clear()
        elif typ == 'reorder':
            self.force_reorder_seqs.clear()

    def show_forced(self):
        print("Forced rules:")
        print(f"  drop: {sorted(self.force_drop_seqs)}")
        print(f"  corrupt: {sorted(self.force_corrupt_seqs)}")
        print(f"  dup: {sorted(self.force_dup_seqs)}")
        print(f"  reorder: {sorted(self.force_reorder_seqs)}")

    def stop(self):
        self.running = False
        self.sock_fwd.close()
        self.sock_back.close()

    def thread_forward(self):
        print(f"[Router] Escutando do emissor em {self.router_addr}, para {self.receiver_addr}")
        while self.running:
            try:
                data, _ = self.sock_fwd.recvfrom(65535)
            except OSError:
                break
            # extrair seq num (se disponível)
            seq = None
            if len(data) >= 2:
                try:
                    seq = struct.unpack("!H", data[:2])[0]
                except Exception:
                    seq = None

            # Forçar DROP por seq ou aleatório
            if seq is not None and seq in self.force_drop_seqs:
                print(f"[Router→] FORCED DROP pacote seq={seq}")
                continue
            if random.random() < self.p_drop_fwd:
                print("[Router→] DROP pacote")
                continue

            # Forçar CORRUPT por seq ou aleatório
            if seq is not None and seq in self.force_corrupt_seqs:
                data = data[:4] + scramble_payload(data[4:], self.scramble_mode_fwd)
                print(f"[Router→] FORCED CORRUPT seq={seq}")
            elif random.random() < self.p_corrupt_fwd:
                data = data[:4] + scramble_payload(data[4:], self.scramble_mode_fwd)
                print("[Router→] CORRUPT payload")

            maybe(self.delay_mean_fwd)

            with self.lock:
                # Forçar HOLD (reorder) por seq
                if seq is not None and seq in self.force_reorder_seqs:
                    self.buffer_reorder.append(data)
                    print(f"[Router→] FORCED HOLD seq={seq} para reordenar (buffer={len(self.buffer_reorder)})")
                    continue
                if self.reorder_window > 0 and random.random() < self.p_reorder_fwd:
                    self.buffer_reorder.append(data)
                    print("[Router→] HOLD pacote p/ reordenação")
                    continue
                if self.buffer_reorder and (len(self.buffer_reorder) >= self.reorder_window or random.random() < 0.5):
                    pkt = self.buffer_reorder.popleft()
                    self.sock_fwd.sendto(pkt, self.receiver_addr)
                    print("[Router→] REORDER envio de pacote antigo")

            self.sock_fwd.sendto(data, self.receiver_addr)
            # Duplicação forçada por seq
            if seq is not None and seq in self.force_dup_seqs:
                time.sleep(0.02)
                self.sock_fwd.sendto(data, self.receiver_addr)
                print(f"[Router→] FORCED DUP seq={seq}")
            elif random.random() < self.p_dup_fwd:
                time.sleep(0.02)
                self.sock_fwd.sendto(data, self.receiver_addr)
                print("[Router→] DUP pacote")

    def thread_backward(self):
        print(f"[Router] Escutando ACKs do receptor em {self.receiver_addr[1]}")
        while self.running:
            try: data, _ = self.sock_back.recvfrom(65535)
            except OSError: break
            if random.random() < self.p_drop_back:
                print("[Router←] DROP ACK"); continue
            maybe(self.delay_mean_back)
            self.sock_back.sendto(data, self.sender_addr)
            print(f"[Router←] ACK repassado: {data.decode(errors='ignore')}")
            if random.random() < self.p_dup_back:
                time.sleep(0.02)
                self.sock_back.sendto(data, self.sender_addr)
                print("[Router←] DUP ACK")

    def run(self):
        tf = threading.Thread(target=self.thread_forward, daemon=True)
        tb = threading.Thread(target=self.thread_backward, daemon=True)
        tf.start(); tb.start()
        try:
            while self.running: time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop(); tf.join(1.0); tb.join(1.0)
            print("[Router] Encerrado.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Roteador UDP com regras forçadas por seq")
    parser.add_argument("--router-host", default="127.0.0.1")
    parser.add_argument("--router-port", type=int, default=9000)
    parser.add_argument("--sender-host", default="127.0.0.1")
    parser.add_argument("--sender-port", type=int, default=9001)
    parser.add_argument("--receiver-host", default="127.0.0.1")
    parser.add_argument("--receiver-port", type=int, default=9002)
    parser.add_argument("--p-corrupt", type=float, default=0.05)
    parser.add_argument("--p-drop", type=float, default=0.05)
    parser.add_argument("--p-dup", type=float, default=0.05)
    parser.add_argument("--p-reorder", type=float, default=0.2)
    parser.add_argument("--reorder-window", type=int, default=3)
    parser.add_argument("--delay-mean", type=float, default=0.02)
    parser.add_argument("--scramble-mode", choices=["shuffle", "xor", "bitflip"], default="bitflip")
    parser.add_argument("--p-drop-ack", type=float, default=0.02)
    parser.add_argument("--p-dup-ack", type=float, default=0.03)
    parser.add_argument("--delay-mean-ack", type=float, default=0.01)
    parser.add_argument("--force-drop", type=str, default="", help="Seq numbers/ranges to FORCE drop, e.g. '2,5-7'")
    parser.add_argument("--force-corrupt", type=str, default="", help="Seq numbers/ranges to FORCE corrupt")
    parser.add_argument("--force-dup", type=str, default="", help="Seq numbers/ranges to FORCE duplicate")
    parser.add_argument("--force-reorder", type=str, default="", help="Seq numbers/ranges to FORCE reorder (hold)")
    parser.add_argument("--interactive-control", action="store_true", help="Start interactive control prompt to add/remove forced errors at runtime")

    args = parser.parse_args()

    forced_drop = parse_seq_list(args.force_drop)
    forced_corrupt = parse_seq_list(args.force_corrupt)
    forced_dup = parse_seq_list(args.force_dup)
    forced_reorder = parse_seq_list(args.force_reorder)

    router = UDPRouter(
        args.router_host, args.router_port,
        args.sender_host, args.sender_port,
        args.receiver_host, args.receiver_port,
        p_corrupt_fwd=args.p_corrupt, p_drop_fwd=args.p_drop, p_dup_fwd=args.p_dup,
        p_reorder_fwd=args.p_reorder, delay_mean_fwd=args.delay_mean, scramble_mode_fwd=args.scramble_mode,
        p_drop_back=args.p_drop_ack, p_dup_back=args.p_dup_ack, delay_mean_back=args.delay_mean_ack,
        reorder_window=args.reorder_window,
        force_drop_seqs=forced_drop, force_corrupt_seqs=forced_corrupt, force_dup_seqs=forced_dup, force_reorder_seqs=forced_reorder
    )

    # controle interativo em tempo de execução (opcional)
    if getattr(args, 'interactive_control', False):
        def control_loop(rtr: UDPRouter):
            print("Controle interativo: use 'add <type> <list>', 'remove <type> <list>', 'clear [type]', 'show', 'exit'")
            while True:
                try:
                    cmd = input('control> ').strip()
                except (EOFError, KeyboardInterrupt):
                    print('\nSaindo do controle interativo.')
                    break
                if not cmd:
                    continue
                parts = cmd.split(None, 2)
                action = parts[0].lower()
                if action == 'exit':
                    break
                if action == 'show':
                    rtr.show_forced(); continue
                if action in ('clear',):
                    if len(parts) == 1:
                        rtr.clear_forced(); print('All forced rules cleared');
                    else:
                        rtr.clear_forced(parts[1]); print(f'Cleared {parts[1]}')
                    continue
                if action in ('add', 'remove', 'set'):
                    if len(parts) < 3:
                        print('Usage: add|remove|set <type> <list>'); continue
                    typ = parts[1]
                    seqs = parse_seq_list(parts[2])
                    if action == 'add':
                        rtr.add_forced(typ, seqs); print(f'Added {sorted(seqs)} to {typ}')
                    elif action == 'remove':
                        rtr.remove_forced(typ, seqs); print(f'Removed {sorted(seqs)} from {typ}')
                    elif action == 'set':
                        rtr.clear_forced(typ); rtr.add_forced(typ, seqs); print(f'Set {typ} to {sorted(seqs)}')
                    continue
                print('Comando desconhecido')

        t_ctl = threading.Thread(target=control_loop, args=(router,), daemon=True)
        t_ctl.start()

    router.run()
