# router.py
import argparse
import random
import socket
import struct
import threading
import time
from collections import deque

# --------------------------
# Utilidades de embaralhamento/corrupção
# --------------------------
def scramble_payload(payload: bytes, mode: str = "shuffle") -> bytes:
    if not payload:
        return payload
    if mode == "shuffle":
        lst = list(payload)
        random.shuffle(lst)
        return bytes(lst)
    elif mode == "xor":
        key = random.randrange(1, 256)
        return bytes([b ^ key for b in payload])
    elif mode == "bitflip":
        # inverte 1 bit aleatório de até 3 posições
        out = bytearray(payload)
        flips = random.randint(1, 3)
        for _ in range(flips):
            i = random.randrange(len(out))
            bit = 1 << random.randrange(8)
            out[i] ^= bit
        return bytes(out)
    else:
        return payload

def maybe(delay_mean: float) -> None:
    """Atraso aleatório (exponencial simples)"""
    if delay_mean > 0:
        t = random.expovariate(1.0 / delay_mean)
        time.sleep(t)

# --------------------------
# Encaminhamento com bagunça
# --------------------------
class UDPRouter:
    def __init__(
        self,
        router_host: str, router_port: int,
        sender_host: str, sender_port: int,
        receiver_host: str, receiver_port: int,
        p_corrupt_fwd: float, p_drop_fwd: float, p_dup_fwd: float,
        p_reorder_fwd: float, delay_mean_fwd: float, scramble_mode_fwd: str,
        p_drop_back: float, p_dup_back: float, delay_mean_back: float,
        reorder_window: int
    ):
        self.router_addr = (router_host, router_port)
        self.sender_addr = (sender_host, sender_port)     # para enviar ACKs de volta
        self.receiver_addr = (receiver_host, receiver_port)

        # Sockets
        self.sock_fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_back = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Liga o roteador na porta que o emissor envia
        self.sock_fwd.bind(self.router_addr)
        # Não é estritamente necessário dar bind no sock_back; vamos usá-lo para enviar/receber de qualquer porta
        self.sock_back.bind(("0.0.0.0", receiver_port))  # recebe ACKs vindos do receptor

        # Probabilidades/comportamentos (forward = emissor->receptor)
        self.p_corrupt_fwd = p_corrupt_fwd
        self.p_drop_fwd = p_drop_fwd
        self.p_dup_fwd = p_dup_fwd
        self.p_reorder_fwd = p_reorder_fwd
        self.delay_mean_fwd = delay_mean_fwd
        self.scramble_mode_fwd = scramble_mode_fwd

        # (back = receptor->emissor)
        self.p_drop_back = p_drop_back
        self.p_dup_back = p_dup_back
        self.delay_mean_back = delay_mean_back

        # Reordenação
        self.reorder_window = max(0, reorder_window)
        self.buffer_reorder = deque()
        self.lock = threading.Lock()

        self.running = True

    def stop(self):
        self.running = False
        try:
            self.sock_fwd.close()
        except:
            pass
        try:
            self.sock_back.close()
        except:
            pass

    # ----------- Threads -----------
    def thread_forward(self):
        # Emissor -> (Router) -> Receptor
        print(f"[Router] Escutando do emissor em {self.router_addr}, encaminhando para {self.receiver_addr}")
        while self.running:
            try:
                data, addr = self.sock_fwd.recvfrom(65535)
            except OSError:
                break

            # data = [ seq(2 bytes) | checksum(2 bytes) | payload ]
            if len(data) >= 4:
                seq, chksum = struct.unpack("!HH", data[:4])
                payload = data[4:]
            else:
                # pacote demasiado curto; encaminha como está (ou descarta)
                seq, chksum, payload = None, None, b""

            # Drop?
            if random.random() < self.p_drop_fwd:
                print(f"[Router→] DROP pacote seq={seq}")
                continue

            # Corrupção (só no payload)
            if random.random() < self.p_corrupt_fwd and len(data) >= 4:
                payload_scr = scramble_payload(payload, self.scramble_mode_fwd)
                data_to_send = data[:4] + payload_scr
                print(f"[Router→] CORRUPT seq={seq} modo={self.scramble_mode_fwd} (len={len(payload)})")
            else:
                data_to_send = data

            # Atraso
            maybe(self.delay_mean_fwd)

            # Reordenação: enfileira e decide soltar fora de ordem
            send_now = True
            with self.lock:
                if self.reorder_window > 0 and random.random() < self.p_reorder_fwd:
                    self.buffer_reorder.append(data_to_send)
                    print(f"[Router→] HOLD seq={seq} para reordenar (buffer={len(self.buffer_reorder)})")
                    send_now = False

                # Se o buffer acumulou, com chance libera um antigo
                if self.buffer_reorder and (len(self.buffer_reorder) >= self.reorder_window or random.random() < 0.5):
                    pkt = self.buffer_reorder.popleft()
                    self.sock_fwd.sendto(pkt, self.receiver_addr)
                    print(f"[Router→] REORDER envio de pacote antigo (buffer={len(self.buffer_reorder)})")

            # Envia o atual (se não ficou retido)
            if send_now:
                self.sock_fwd.sendto(data_to_send, self.receiver_addr)
                if seq is not None:
                    print(f"[Router→] SEND seq={seq}")

                # Duplicação
                if random.random() < self.p_dup_fwd:
                    time.sleep(0.01 + random.random() * 0.05)
                    self.sock_fwd.sendto(data_to_send, self.receiver_addr)
                    print(f"[Router→] DUP seq={seq}")

    def thread_backward(self):
        # Receptor -> (Router) -> Emissor (ACKs)
        print(f"[Router] Escutando ACKs do receptor em 0.0.0.0:{self.receiver_addr[1]}, encaminhando ao emissor em {self.sender_addr}")
        while self.running:
            try:
                data, addr = self.sock_back.recvfrom(65535)
            except OSError:
                break

            # Drop ACK?
            if random.random() < self.p_drop_back:
                print("[Router←] DROP ACK")
                continue

            # Atraso ACK
            maybe(self.delay_mean_back)

            # Repassa
            self.sock_back.sendto(data, self.sender_addr)
            try:
                msg = data.decode(errors="ignore")
            except:
                msg = str(len(data)) + " bytes"
            print(f"[Router←] ACK → emissor ({msg.strip()[:60]})")

            # Dup ACK
            if random.random() < self.p_dup_back:
                time.sleep(0.01 + random.random() * 0.05)
                self.sock_back.sendto(data, self.sender_addr)
                print("[Router←] DUP ACK")

    def run(self):
        tf = threading.Thread(target=self.thread_forward, daemon=True)
        tb = threading.Thread(target=self.thread_backward, daemon=True)
        tf.start()
        tb.start()
        try:
            while self.running:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            tf.join(timeout=1.0)
            tb.join(timeout=1.0)
            print("[Router] Encerrado.")

# --------------------------
# CLI
# --------------------------
def main():
    parser = argparse.ArgumentParser(description="Roteador UDP com embaralhamento/corrupção/reordenação/atraso/duplicação.")
    # Endereços
    parser.add_argument("--router-host", default="127.0.0.1")
    parser.add_argument("--router-port", type=int, default=9002, help="Porta onde o emissor envia (roteador escuta).")
    parser.add_argument("--sender-host", default="127.0.0.1")
    parser.add_argument("--sender-port", type=int, default=9001, help="Porta do emissor para encaminhar ACKs.")
    parser.add_argument("--receiver-host", default="127.0.0.1")
    parser.add_argument("--receiver-port", type=int, default=9003, help="Porta do receptor em Java.")

    # Emissor -> Receptor
    parser.add_argument("--p-corrupt", type=float, default=0.20, help="Probabilidade de corromper payload.")
    parser.add_argument("--p-drop", type=float, default=0.05, help="Probabilidade de descartar pacote.")
    parser.add_argument("--p-dup", type=float, default=0.05, help="Probabilidade de duplicar pacote.")
    parser.add_argument("--p-reorder", type=float, default=0.20, help="Probabilidade de reter pacote para reordenação.")
    parser.add_argument("--reorder-window", type=int, default=3, help="Tamanho do buffer de reordenação.")
    parser.add_argument("--delay-mean", type=float, default=0.02, help="Atraso médio (s) por pacote (distribuição exponencial).")
    parser.add_argument("--scramble-mode", choices=["shuffle", "xor", "bitflip"], default="shuffle", help="Modo de embaralhar/corromper payload.")

    # Receptor -> Emissor (ACKs)
    parser.add_argument("--p-drop-ack", type=float, default=0.02, help="Probabilidade de descartar ACK.")
    parser.add_argument("--p-dup-ack", type=float, default=0.03, help="Probabilidade de duplicar ACK.")
    parser.add_argument("--delay-mean-ack", type=float, default=0.01, help="Atraso médio (s) de ACK.")

    args = parser.parse_args()

    r = UDPRouter(
        router_host=args.router_host, router_port=args.router_port,
        sender_host=args.sender_host, sender_port=args.sender_port,
        receiver_host=args.receiver_host, receiver_port=args.receiver_port,
        p_corrupt_fwd=args.p_corrupt, p_drop_fwd=args.p_drop, p_dup_fwd=args.p_dup,
        p_reorder_fwd=args.p_reorder, delay_mean_fwd=args.delay_mean, scramble_mode_fwd=args.scramble_mode,
        p_drop_back=args.p_drop_ack, p_dup_back=args.p_dup_ack, delay_mean_back=args.delay-mean-ack if hasattr(args, "delay-mean-ack") else args.delay_mean_ack,
        reorder_window=args.reorder_window
    )
    r.run()

if __name__ == "__main__":
    main()
