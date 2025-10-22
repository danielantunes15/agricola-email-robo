import imaplib
import email
import os
import pdfplumber
import re  # Para regex e limpeza de nomes de arquivos

# Configurações de e-mail
EMAIL = "daniel.antunes@bahiaetanol.com.br"
SENHA = "Maria2020+-"  # Use uma senha de aplicativo
IMAP_SERVER = "imap.gmail.com"

# Criar pasta para salvar as notas fiscais
save_directory = r"C:\Users\daniel.ribeiro\Desktop\PYTHON EMAIL\notas_fiscais"
os.makedirs(save_directory, exist_ok=True)

# Conectar ao servidor IMAP
mail = imaplib.IMAP4_SSL(IMAP_SERVER)
mail.login(EMAIL, SENHA)
mail.select("inbox")

# Definir período para a busca
data_inicio = "01-Apr-2024"
data_fim = "14-Mar-2025"
search_criteria = f'(SINCE {data_inicio} BEFORE {data_fim})'
status, messages = mail.search(None, search_criteria)

# Função para limpar nomes de arquivos e remover caracteres inválidos
def sanitize_filename(filename):
    filename = filename.replace("\r", "").replace("\n", "").strip()  # Remove quebras de linha e espaços extras
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)  # Substitui caracteres inválidos no Windows
    return filename

# Lista de CNPJs para buscar (diferentes formatos)
CNPJ_BASE = "53.518.733/0001-89"
CNPJ_VARIATIONS = [
    CNPJ_BASE,
    CNPJ_BASE.replace(".", "").replace("/", "").replace("-", ""),  # Ex: 53518733000189
    CNPJ_BASE.replace(".", "").replace("/", ""),  # Ex: 535187330001-89
    CNPJ_BASE.replace(".", ""),  # Ex: 53 518 733/0001-89
]

# Expressão regular para capturar diferentes formatos do CNPJ
CNPJ_REGEX = re.compile(r"53[.\s]?518[.\s]?733[./\s]?0001[-/\s]?89")

# Função para verificar se o CNPJ está dentro do anexo PDF
def verifica_cnpj_no_anexo(filepath):
    try:
        with pdfplumber.open(filepath) as pdf:
            texto_extraido = "\n".join(page.extract_text() or "" for page in pdf.pages)
            if any(cnpj in texto_extraido for cnpj in CNPJ_VARIATIONS) or CNPJ_REGEX.search(texto_extraido):
                return True
    except Exception as e:
        print(f"⚠ Erro ao processar PDF {filepath}: {e}")
    return False

# Processar e-mails encontrados
if messages[0]:
    for num in messages[0].split():
        status, data = mail.fetch(num, "(RFC822)")

        # Verifica se a mensagem é válida
        if not data or data[0] is None:
            continue

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        # Verificar anexos
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                filename = part.get_filename()
                
                if filename and filename.endswith(".pdf"):
                    filename = sanitize_filename(filename)  # Limpa nome do arquivo
                    filepath = os.path.join(save_directory, filename)

                    # Salvar o anexo temporariamente
                    try:
                        with open(filepath, "wb") as f:
                            f.write(part.get_payload(decode=True))

                        # Verificar se o CNPJ está dentro do anexo
                        if verifica_cnpj_no_anexo(filepath):
                            print(f"✔ Nota fiscal salva: {filename}")
                        else:
                            os.remove(filepath)  # Remove o arquivo se não for relevante
                            print(f"❌ '{filename}' removido (não contém o CNPJ buscado)")
                    except OSError as e:
                        print(f"❌ Erro ao salvar o anexo '{filename}': {e}")
else:
    print("📭 Nenhum e-mail encontrado no período especificado.")

# Encerrar a conexão
mail.logout()
