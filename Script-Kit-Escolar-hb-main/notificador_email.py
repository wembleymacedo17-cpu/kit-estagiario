import smtplib
import mimetypes
from email.message import EmailMessage
from io import BytesIO
from dotenv import load_dotenv
import os

load_dotenv()

SMTP_SERVER = os.getenv("SERVIDOR_BREVO")
SMTP_PORT = os.getenv("PORTA_BREVO")
LOGIN_SMTP = os.getenv("LOGIN_SMTP")
SENHA_KEY = os.getenv("SENHA_KEY")
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE")


class NotificadorEmail:
    def __init__(self, smtp_server: str, smtp_port, login_smtp: str, senha: str):
        self.smtp_server = smtp_server
        self.smtp_port = int(smtp_port)
        self.login_smtp = login_smtp
        self.senha = senha

    def disparar(self, remetente: str, destinatarios, assunto: str, corpo: str,
                 anexo, nome_anexo: str) -> bool:
        """
        Monta e envia o e-mail com um anexo via SMTP (Brevo).
        'anexo' pode ser bytes ou um BytesIO (ex: buffer de QR Code gerado em memória).
        Retorna True/False indicando sucesso do envio.
        """
        if isinstance(destinatarios, str):
            destinatarios = [destinatarios]

        msg = EmailMessage()
        msg['Subject'] = assunto
        msg['From'] = remetente
        msg['To'] = ", ".join(destinatarios)
        msg.set_content(corpo)

        if isinstance(anexo, BytesIO):
            anexo.seek(0)
            conteudo_anexo = anexo.read()
            anexo.seek(0)
        else:
            conteudo_anexo = anexo

        tipo_mime, _ = mimetypes.guess_type(nome_anexo)
        tipo_mime = tipo_mime or 'application/octet-stream'
        tipo_principal, sub_tipo = tipo_mime.split('/')

        msg.add_attachment(conteudo_anexo, maintype=tipo_principal, subtype=sub_tipo, filename=nome_anexo)

        print(f"DEBUG -> From: {repr(remetente)} | To: {repr(destinatarios)} | Subject: {repr(assunto)}")

        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            print("DEBUG -> Conexão TCP aberta")

            server.ehlo()
            print("DEBUG -> EHLO ok")

            server.starttls()
            print("DEBUG -> STARTTLS ok")

            server.ehlo()
            print("DEBUG -> EHLO pós-TLS ok")

            server.login(self.login_smtp, self.senha)
            print("DEBUG -> LOGIN ok")

            server.send_message(msg)
            print("DEBUG -> SEND_MESSAGE ok")

            server.quit()
            print(f"✅ E-mail enviado para {len(destinatarios)} destinatário(s).")
            return True
        except smtplib.SMTPAuthenticationError as e:
            print(f"❌ Erro de autenticação retornado pelo Brevo: {e}")
            return False
        except Exception as e:
            print(f"❌ Erro ao enviar e-mail [{type(e).__name__}]: {e}")
            return False