import java.io.*;
import java.net.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

public class Receptor {

    private static final int PORTA_RECEPTOR = 9002;
    private static final int PORTA_EMISSOR = 9003;
    private static final String IP = "127.0.0.1";
    private static final int MAX_DATA_SIZE = 50;

    public static void main(String[] args) {
        DatagramSocket socket = null;
        ByteArrayOutputStream mensagem = new ByteArrayOutputStream();
        int expectedSeq = 0;

        try {
            socket = new DatagramSocket(PORTA_RECEPTOR);
            System.out.println("[Receptor] Aguardando pacotes em " + IP + ":" + PORTA_RECEPTOR + "...");

            byte[] buffer = new byte[1024];
            while (true) {
                DatagramPacket pacote = new DatagramPacket(buffer, buffer.length);
                socket.receive(pacote);
                byte[] dados = Arrays.copyOf(pacote.getData(), pacote.getLength());

                if (dados.length < 4) {
                    System.out.println("[Receptor] Pacote muito pequeno! Ignorando...");
                    continue;
                }

                ByteBuffer bb = ByteBuffer.wrap(dados);
                bb.order(ByteOrder.BIG_ENDIAN);

                int seqNum = bb.getShort() & 0xFFFF;
                int checksumRecebido = bb.getShort() & 0xFFFF;
                byte[] conteudo = Arrays.copyOfRange(dados, 4, dados.length);

                byte[] checksumData = new byte[2 + conteudo.length];
                checksumData[0] = (byte) ((seqNum >> 8) & 0xFF);
                checksumData[1] = (byte) (seqNum & 0xFF);
                System.arraycopy(conteudo, 0, checksumData, 2, conteudo.length);
                int checksumCalculado = calcularChecksum(checksumData);

                if (checksumRecebido != checksumCalculado) {
                    System.out.println("[Receptor] Pacote corrompido! Ignorando...");
                    enviarAck(socket, expectedSeq - 1);
                    continue;
                }

                if (seqNum == expectedSeq) {
                    mensagem.write(conteudo);
                    System.out.println("[Receptor] Pacote " + seqNum + " aceito.");
                    expectedSeq++;
                } else {
                    System.out.println("[Receptor] Fora de ordem (esperado=" + expectedSeq + ", recebido=" + seqNum + ")");
                }

                enviarAck(socket, expectedSeq - 1);

                if (conteudo.length < MAX_DATA_SIZE) {
                    System.out.println("\n[Receptor] Fim da transmissão detectado.");
                    break;
                }
            }

            System.out.println("\n===== MENSAGEM COMPLETA =====");
            System.out.println(new String(mensagem.toByteArray(), StandardCharsets.UTF_8));
            System.out.println("=============================");

        } catch (Exception e) {
            e.printStackTrace();
        } finally {
            if (socket != null) socket.close();
            System.out.println("[Receptor] Socket fechado.");
        }
    }

    private static int calcularChecksum(byte[] dados) {
        if (dados.length % 2 != 0)
            dados = Arrays.copyOf(dados, dados.length + 1);

        int soma = 0;
        for (int i = 0; i < dados.length; i += 2) {
            int palavra = ((dados[i] & 0xFF) << 8) | (dados[i + 1] & 0xFF);
            soma += palavra;
            soma = (soma & 0xFFFF) + (soma >> 16);
        }
        return (~soma) & 0xFFFF;
    }

    private static void enviarAck(DatagramSocket socket, int ackNum) throws IOException {
        String ackMsg = "{'ack_num':" + ackNum + "}";
        byte[] ackBytes = ackMsg.getBytes();
        DatagramPacket ackPacket = new DatagramPacket(
                ackBytes, ackBytes.length,
                InetAddress.getByName(IP), PORTA_EMISSOR
        );
        socket.send(ackPacket);
        System.out.println("[Receptor] Enviou ACK(" + ackNum + ")");
    }
}
