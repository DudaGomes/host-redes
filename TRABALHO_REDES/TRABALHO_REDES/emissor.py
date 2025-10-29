import socket
import threading
import struct
import time
import json

# --- CONFIGURAÇÕES ---
ROUTER_ADDR = ('127.0.0.1', 9000)   # IP do roteador (porta corrigida)
SENDER_ADDR = ('127.0.0.1', 9001)   # IP local
WINDOW_SIZE = 5 #tamanho da janela
TIMEOUT = 20 #tempo de timeout em segundos
MAX_DATA_SIZE = 50 #tamanho máximo dos dados em bytes

# --- VARIÁVEIS ---
base = 0 #menor número de sequência não reconhecido
next_seq_num = 0 #próximo número de sequência a ser usado
buffer_pacotes = {} #armazenar pacotes enviados mas não reconhecidos
lock = threading.Lock() #trava para sincronização de threads
timer = None # referencia ao timer para timeout
emissor_ativo = True #indica se o emissor está ativo

# --- CHECKSUM ---
def calcular_checksum(dados: bytes) -> int:
    if len(dados) % 2 != 0:
        dados += b'\0'
    soma = 0
    for i in range(0, len(dados), 2):
        palavra = (dados[i] << 8) + dados[i + 1]
        soma += palavra
        soma = (soma & 0xFFFF) + (soma >> 16)
    return ~soma & 0xFFFF

# --- CRIAR PACOTE ---
def criar_pacote(seq_num, dados: bytes):
    header_sem_checksum = struct.pack("!H", seq_num) + dados
    checksum = calcular_checksum(header_sem_checksum)
    return struct.pack("!HH", seq_num, checksum) + dados

# --- TIMEOUT ---
def evento_timeout(sock):
    global timer
    if not emissor_ativo:
        return
    with lock:
        print(f"\n[Emissor] TIMEOUT! Reenviando de {base} até {next_seq_num - 1}")
        for i in range(base, next_seq_num):
            if i in buffer_pacotes:
                sock.sendto(buffer_pacotes[i], ROUTER_ADDR)
                print(f"[Emissor] Reenviado pacote {i}.")
        if timer:
            timer.cancel()
        timer = threading.Timer(TIMEOUT, evento_timeout, args=(sock,))
        timer.start()

# --- RECEBER ACKs ---
def escutar_acks(sock):
    global base, timer, emissor_ativo
    while emissor_ativo:
        try:
            dados_ack, _ = sock.recvfrom(1024)
            s = dados_ack.decode('utf-8', errors='ignore').replace("'", '"').strip()
            try:
                ack = json.loads(s).get("ack_num")
            except:
                continue
            if ack is None:
                continue
            print(f"[Emissor] Recebeu ACK({ack})")

            with lock:
                if ack + 1 > base:
                    old_base = base
                    base = ack + 1
                    print(f"[Emissor] Base moveu de {old_base} para {base} (next_seq_num={next_seq_num})")
                    if base == next_seq_num:
                        if timer: timer.cancel()
                        timer = None
                        print("[Emissor] Timer parado - TODOS OS PACOTES CONFIRMADOS!")
                    else:
                        if timer: timer.cancel()
                        timer = threading.Timer(TIMEOUT, evento_timeout, args=(sock,))
                        timer.start()
                        print(f"[Emissor] Timer reiniciado. Aguardando ACKs de {base} até {next_seq_num-1}")
        except Exception:
            break

# --- PRINCIPAL ---
if __name__ == "__main__":
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(SENDER_ADDR)
    print(f"[Emissor] Escutando ACKs em {SENDER_ADDR}")

    texto = input("Digite a mensagem para enviar: ")
    dados = texto.encode('utf-8')

    emissor_ativo = True
    threading.Thread(target=escutar_acks, args=(sock,), daemon=True).start()

    data_ptr = 0
    try:
        while data_ptr < len(dados) or base < next_seq_num:
            if next_seq_num < base + WINDOW_SIZE and data_ptr < len(dados):
                with lock:
                    bloco = dados[data_ptr:data_ptr + MAX_DATA_SIZE]
                    data_ptr += len(bloco)
                    pacote = criar_pacote(next_seq_num, bloco)
                    buffer_pacotes[next_seq_num] = pacote
                    sock.sendto(pacote, ROUTER_ADDR)
                    print(f"[Emissor] Pacote {next_seq_num} enviado. (janela: {base} a {base+WINDOW_SIZE-1})")
                    if base == next_seq_num:
                        if timer: timer.cancel()
                        timer = threading.Timer(TIMEOUT, evento_timeout, args=(sock,))
                        timer.start()
                    next_seq_num += 1
            else:
                time.sleep(0.01)
        
        print(f"\n[Emissor] Todos os dados enviados. data_ptr={data_ptr}, len(dados)={len(dados)}")

        # Pacote final (FIN)
        with lock:
            pacote_fin = criar_pacote(next_seq_num, b'')
            buffer_pacotes[next_seq_num] = pacote_fin
            sock.sendto(pacote_fin, ROUTER_ADDR)
            print(f"[Emissor] FIN (seq {next_seq_num}) enviado.")
            if timer is None or base == next_seq_num:
                if timer: timer.cancel()
                timer = threading.Timer(TIMEOUT, evento_timeout, args=(sock,))
                timer.start()
                print("[Emissor] Timer iniciado para FIN")
            next_seq_num += 1

        print(f"[Emissor] Aguardando confirmação final. base={base}, next_seq_num={next_seq_num}")
        while base < next_seq_num:
            time.sleep(0.05)
        print(f"[Emissor] Confirmação final recebida! base={base}, next_seq_num={next_seq_num}")

    finally:
        if timer: timer.cancel()
        emissor_ativo = False
        sock.close()
        print("\n[Emissor] Envio concluído e socket fechado.")
