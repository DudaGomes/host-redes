import socket
import threading
import struct
import time

# --- Configurações ---
ROUTER_ADDR = ('127.0.0.1', 9002)
SENDER_ADDR = ('127.0.0.1', 9001)
WINDOW_SIZE = 5
TIMEOUT = 0.5

# --- Variáveis globais ---
base = 0
next_seq_num = 0
buffer_pacotes = {}
lock = threading.Lock()
timer = None
emissor_ativo = True

# --- Função 1: Checksum manual ---
def calcular_checksum(dados: bytes) -> int:
    if len(dados) % 2 != 0:
        dados += b'\0'

    soma = 0
    for i in range(0, len(dados), 2):
        palavra = (dados[i] << 8) + dados[i + 1]
        soma += palavra
        soma = (soma & 0xFFFF) + (soma >> 16)
    return ~soma & 0xFFFF

# --- Função 2: Criar pacote binário ---
def criar_pacote(seq_num, dados: bytes):
    header_sem_checksum = struct.pack("!H", seq_num) + dados
    checksum = calcular_checksum(header_sem_checksum)
    pacote = struct.pack("!HH", seq_num, checksum) + dados
    return pacote

# --- Timeout ---
def evento_timeout(sock):
    global timer
    if not emissor_ativo:
        return
    with lock:
        print(f"--- [Emissor] TIMEOUT! ---")
        print(f"[Emissor] Reenviando pacotes de {base} até {next_seq_num - 1}")
        for i in range(base, next_seq_num):
            if i in buffer_pacotes:
                sock.sendto(buffer_pacotes[i], ROUTER_ADDR)
                print(f"[Emissor] Pacote {i} reenviado.")
        if timer:
            timer.cancel()
        timer = threading.Timer(TIMEOUT, evento_timeout, args=(sock,))
        timer.start()

# --- Escutar ACKs ---
def escutar_acks(sock):
    global base, timer, emissor_ativo
    while emissor_ativo:
        try:
            dados_ack, _ = sock.recvfrom(1024)
            ack_str = dados_ack.decode(errors='ignore')
            if "ack_num" in ack_str:
                ack_num = int(''.join(c for c in ack_str if c.isdigit()))
                print(f"[Emissor] Recebeu ACK para: {ack_num}")
                with lock:
                    if ack_num + 1 > base:
                        base = ack_num + 1
                        if base == next_seq_num:
                            if timer:
                                timer.cancel()
                            timer = None
                            print("[Emissor] Timer parado.")
                        else:
                            if timer:
                                timer.cancel()
                            timer = threading.Timer(TIMEOUT, evento_timeout, args=(sock,))
                            timer.start()
                            print("[Emissor] Timer reiniciado.")
        except Exception:
            break

# --- Programa principal ---
if __name__ == "__main__":
    MAX_DATA_SIZE = 50
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(SENDER_ADDR)
        print(f"[Emissor] Socket criado e escutando ACKs em {SENDER_ADDR}")
    except OSError as e:
        print(f"[Emissor] Erro: porta {SENDER_ADDR[1]} já em uso. {e}")
        exit()

    texto_usuario = input("Digite a mensagem para enviar e pressione Enter: ")
    MEU_TEXTO = texto_usuario.encode('utf-8')

    print(f"[Emissor] Mensagem a ser enviada: {MEU_TEXTO}")
    print(f"[Emissor] Tamanho: {len(MEU_TEXTO)} bytes")

    emissor_ativo = True
    thread_acks = threading.Thread(target=escutar_acks, args=(sock,), daemon=True)
    thread_acks.start()

    data_pointer = 0

    try:
        while data_pointer < len(MEU_TEXTO) or base < next_seq_num:
            if next_seq_num < base + WINDOW_SIZE and data_pointer < len(MEU_TEXTO):
                with lock:
                    chunk = MEU_TEXTO[data_pointer:data_pointer + MAX_DATA_SIZE]
                    data_pointer += len(chunk)
                    pacote = criar_pacote(next_seq_num, chunk)
                    buffer_pacotes[next_seq_num] = pacote
                    sock.sendto(pacote, ROUTER_ADDR)
                    print(f"[Emissor] Pacote {next_seq_num} enviado.")
                    if base == next_seq_num:
                        if timer:
                            timer.cancel()
                        timer = threading.Timer(TIMEOUT, evento_timeout, args=(sock,))
                        timer.start()
                    next_seq_num += 1
            else:
                time.sleep(0.01)

            if data_pointer >= len(MEU_TEXTO) and base == next_seq_num:
                break

    except KeyboardInterrupt:
        print("\n[Emissor] Encerrando por usuário.")
    finally:
        print("[Emissor] Envio concluído. Encerrando.")
        if timer:
            timer.cancel()
        emissor_ativo = False
        sock.close()
        thread_acks.join(timeout=1.0)
        print("[Emissor] Socket e threads fechados.")
