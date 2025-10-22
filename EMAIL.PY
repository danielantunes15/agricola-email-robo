import tkinter as tk
from tkinter import messagebox, scrolledtext, filedialog, ttk
from threading import Thread
import os
import re
from datetime import date, timedelta
import imaplib
import email 
import pdfplumber
# Importação necessária para o seletor de datas
from tkcalendar import DateEntry 

# --- CONFIGURAÇÕES GLOBAIS ---
IMAP_SERVER = "imap.gmail.com"
DEFAULT_SAVE_DIRECTORY = os.path.join(os.path.expanduser('~'), "Desktop", "ARQUIVOS_ROBO_EMAIL")
LAST_EMAIL_FILE = "last_email.txt"
# -----------------------------

# --- FUNÇÕES DE LÓGICA (IMAP/PDF) ---

def get_search_regex(search_type, search_term):
    """Cria a expressão regular baseada no tipo de busca."""
    if search_type in ('CNPJ', 'CPF'):
        clean_term = re.sub(r'[.\-/\s]', '', search_term)
        if len(clean_term) < 5:
            return None
        return re.compile(re.escape(clean_term)) 
    elif search_type == 'NOME':
        return re.compile(re.escape(search_term), re.IGNORECASE)
    return None

def sanitize_filename(filename):
    """Função para limpar nomes de arquivos e remover caracteres inválidos."""
    filename = filename.replace("\r", "").replace("\n", "").strip()
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    return filename

def verifica_termo_no_anexo(filepath, search_regex):
    """Verifica se o termo (CNPJ/CPF/Nome) está dentro do anexo PDF."""
    try:
        with pdfplumber.open(filepath) as pdf:
            texto_extraido = "\n".join(page.extract_text() or "" for page in pdf.pages)
            
            if search_regex.pattern not in ('NOME',):
                texto_para_busca = re.sub(r'\s', '', texto_extraido)
            else:
                texto_para_busca = texto_extraido

            if search_regex.search(texto_para_busca):
                return True
            
    except Exception as e:
        return f"⚠ Erro ao processar PDF: {e}"
    return False

def processar_busca(mail, search_type, search_term, save_directory, data_inicio_dt, data_fim_dt, log_callback, progress_callback):
    """Realiza a busca no e-mail, filtrando e salvando, com feedback de progresso."""
    
    search_regex = get_search_regex(search_type, search_term)
    if not search_regex:
        log_callback("❌ Tipo de busca ou termo inválido.")
        progress_callback(0) # Zera a barra de progresso
        return

    # Formatação das datas para o critério de busca IMAP
    data_inicio_imap = data_inicio_dt.strftime("%d-%b-%Y")
    data_fim_imap = data_fim_dt.strftime("%d-%b-%Y")
    
    # Adiciona +1 dia à data final para garantir que ela seja INCLUÍDA na busca (BEFORE é exclusivo)
    data_fim_search = (data_fim_dt + timedelta(days=1)).strftime("%d-%b-%Y")
    
    search_criteria = f'(SINCE {data_inicio_imap} BEFORE {data_fim_search})'
    
    log_callback(f"\n⚙️ Iniciando busca na 'inbox' (Período: {data_inicio_imap} a {data_fim_imap})...")
    log_callback(f"⚙️ Termo de busca ({search_type}): '{search_term}'")
    
    try:
        status, messages = mail.search(None, search_criteria)
    except Exception as e:
        log_callback(f"❌ Erro ao executar a busca IMAP: {e}")
        progress_callback(0)
        return

    message_ids = messages[0].split()
    total_emails = len(message_ids)
    
    if total_emails == 0:
        log_callback("📭 Nenhum e-mail encontrado no período especificado.")
        progress_callback(100) # Completa a barra se não houver emails
        return

    os.makedirs(save_directory, exist_ok=True)
    notas_salvas = 0

    # Lógica de progresso
    progress_per_email = 100 / total_emails
    current_progress = 0

    for i, num in enumerate(message_ids):
        # 1. Atualiza a barra de progresso no início de cada e-mail
        current_progress += progress_per_email
        progress_callback(current_progress)

        status, data = mail.fetch(num, "(RFC822)")

        if not data or data[0] is None:
            continue

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        # 2. Verifica anexos (mesma lógica)
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                filename = part.get_filename()
                
                if filename and filename.lower().endswith(".pdf"):
                    filename = sanitize_filename(filename)
                    temp_filepath = os.path.join(save_directory, f"temp_{num.decode('utf-8')}_{filename}")

                    try:
                        with open(temp_filepath, "wb") as f:
                            f.write(part.get_payload(decode=True))
                        
                        resultado_verificacao = verifica_termo_no_anexo(temp_filepath, search_regex)
                        
                        if resultado_verificacao is True:
                            final_filepath = os.path.join(save_directory, filename)
                            
                            if os.path.exists(final_filepath):
                                base, ext = os.path.splitext(filename)
                                final_filepath = os.path.join(save_directory, f"{base}_DUPLICADO{ext}")
                                
                            os.rename(temp_filepath, final_filepath)
                            log_callback(f"  ✔ Arquivo salvo: {filename}")
                            notas_salvas += 1
                        elif isinstance(resultado_verificacao, str):
                            log_callback(resultado_verificacao)
                            os.remove(temp_filepath)
                        else:
                            os.remove(temp_filepath)
                            
                    except OSError as e:
                        log_callback(f"  ❌ Erro ao salvar o anexo '{filename}': {e}")
    
    # Garante que a barra chegue a 100% no final
    progress_callback(100)
    log_callback("-" * 40)
    log_callback(f"✅ Busca concluída. {notas_salvas} anexos relevantes salvos em: {save_directory}")
    log_callback("-" * 40)

# --------------------

class EmailRoboGUI:
    def __init__(self, master):
        self.master = master
        master.title("🤖 Robô de Busca de Documentos (IMAP/PDF)")
        master.geometry("600x450")
        
        # Variáveis de estado
        self.mail = None
        self.save_directory = DEFAULT_SAVE_DIRECTORY
        
        # Lógica de persistência do último e-mail
        self.last_email = self.load_last_email()
        
        self.create_login_widgets()

    # --- Funções de Persistência ---

    def load_last_email(self):
        """Carrega o último e-mail usado a partir do arquivo."""
        if os.path.exists(LAST_EMAIL_FILE):
            try:
                with open(LAST_EMAIL_FILE, 'r') as f:
                    return f.read().strip()
            except Exception:
                return ""
        return ""

    def save_last_email(self, email_user):
        """Salva o e-mail em um arquivo após login bem-sucedido."""
        try:
            with open(LAST_EMAIL_FILE, 'w') as f:
                f.write(email_user)
        except Exception as e:
            print(f"Erro ao salvar e-mail: {e}")

    # --- Login Widgets ---

    def create_login_widgets(self):
        """Cria os widgets para a tela de login."""
        for widget in self.master.winfo_children():
            widget.destroy()

        self.login_frame = tk.Frame(self.master, padx=20, pady=20)
        self.login_frame.pack(pady=50)

        tk.Label(self.login_frame, text="Login IMAP", font=('Arial', 16, 'bold')).grid(row=0, columnspan=2, pady=10)
        
        tk.Label(self.login_frame, text="E-mail:").grid(row=1, column=0, sticky="w", pady=5)
        self.email_entry = tk.Entry(self.login_frame, width=35)
        self.email_entry.grid(row=1, column=1, pady=5)
        self.email_entry.insert(0, self.last_email)
        
        tk.Label(self.login_frame, text="Senha de App:").grid(row=2, column=0, sticky="w", pady=5)
        self.password_entry = tk.Entry(self.login_frame, show="*", width=35)
        self.password_entry.grid(row=2, column=1, pady=5)

        tk.Button(self.login_frame, text="Logar", command=self.start_login_thread, width=25, bg='#4CAF50', fg='white').grid(row=3, columnspan=2, pady=15)
        
        self.login_status = tk.Label(self.login_frame, text="")
        self.login_status.grid(row=4, columnspan=2)
        
    def start_login_thread(self):
        """Inicia o login em uma thread separada para não travar a GUI."""
        self.login_status.config(text="Tentando conectar...", fg="blue")
        login_button = self.login_frame.winfo_children()[6]
        login_button.config(state=tk.DISABLED)
        
        email_user = self.email_entry.get().strip()
        senha_app = self.password_entry.get()
        
        Thread(target=self.perform_login, args=(email_user, senha_app, login_button)).start()

    def perform_login(self, email_user, senha_app, login_button):
        """Tenta a conexão IMAP."""
        error_message = None
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            mail.login(email_user, senha_app)
            mail.select("inbox")
            self.mail = mail
            
            self.master.after(0, lambda: self.login_successful(email_user)) 
        except imaplib.IMAP4.error as e:
            error_message = str(e)
            self.master.after(0, lambda: self.login_failed(error_message, login_button))
        except Exception as e:
            error_message = f"Erro Inesperado: {e}"
            self.master.after(0, lambda: self.login_failed(error_message, login_button))
            
    def login_successful(self, email_user):
        self.save_last_email(email_user)
        self.login_status.config(text=f"🎉 Logado com sucesso em {email_user}! 🎉", fg="green")
        messagebox.showinfo("Sucesso", "Login realizado! Iniciando Menu de Busca.")
        self.create_main_menu_widgets()

    def login_failed(self, error_message, login_button):
        self.login_status.config(text=f"❌ Erro ao logar.", fg="red")
        login_button.config(state=tk.NORMAL)
        messagebox.showerror("Erro de Login", f"Não foi possível conectar:\n{error_message}")

    # --- Menu Principal Widgets ---

    def select_save_directory(self):
        """Abre a caixa de diálogo para seleção de pasta."""
        new_dir = filedialog.askdirectory(initialdir=self.save_directory)
        if new_dir:
            self.save_directory = new_dir
            self.dir_entry.config(state=tk.NORMAL)
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, self.save_directory)
            self.dir_entry.config(state=tk.DISABLED)

    def create_main_menu_widgets(self):
        """Cria os widgets para o menu de busca."""
        
        for widget in self.master.winfo_children():
            widget.destroy()
        self.master.title("Robô de Busca - Menu Principal")
        self.master.geometry("900x700") 

        # --- Frame de Configuração ---
        config_frame = tk.LabelFrame(self.master, text="Configuração da Busca", padx=15, pady=15)
        config_frame.pack(padx=10, pady=10, fill="x")

        # Layout com grid
        config_frame.grid_columnconfigure(1, weight=1)

        # 1. Tipo de Busca (Linha 0)
        tk.Label(config_frame, text="Buscar por:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.search_type = tk.StringVar(self.master, "CNPJ")
        tk.OptionMenu(config_frame, self.search_type, "CNPJ", "CPF", "NOME").grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        # 2. Termo de Busca (Linha 1)
        tk.Label(config_frame, text="Termo:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.term_entry = tk.Entry(config_frame, width=50)
        self.term_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

        # --- SELEÇÃO DE PERÍODO (Data De/Até) ---
        
        # 3. Data Inicial (Linha 2, Coluna 0/1)
        tk.Label(config_frame, text="Data Início:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        # Usa DateEntry do tkcalendar para o calendário drop-down
        self.date_inicio_entry = DateEntry(config_frame, width=12, background='darkblue', foreground='white', borderwidth=2, date_pattern='dd/MM/yyyy')
        self.date_inicio_entry.set_date(date.today() - timedelta(days=90)) # Data padrão: 90 dias atrás
        self.date_inicio_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")
        
        # 4. Data Final (Linha 3, Coluna 0/1)
        tk.Label(config_frame, text="Data Fim:").grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.date_fim_entry = DateEntry(config_frame, width=12, background='darkblue', foreground='white', borderwidth=2, date_pattern='dd/MM/yyyy')
        self.date_fim_entry.set_date(date.today()) # Data padrão: hoje
        self.date_fim_entry.grid(row=3, column=1, padx=5, pady=5, sticky="w")
        
        # 5. Pasta de Destino (Linha 4)
        tk.Label(config_frame, text="Salvar em:").grid(row=4, column=0, padx=5, pady=5, sticky="w")
        self.dir_entry = tk.Entry(config_frame)
        self.dir_entry.insert(0, self.save_directory)
        self.dir_entry.config(state=tk.DISABLED) 
        self.dir_entry.grid(row=4, column=1, padx=5, pady=5, sticky="ew")
        
        # 6. Botão de Seleção de Pasta
        tk.Button(config_frame, text="...", command=self.select_save_directory, width=3).grid(row=4, column=2, padx=5, sticky="w")
        
        # 7. Botão de Busca (Linha 0/1, Coluna 2)
        self.search_button = tk.Button(config_frame, text="🔍 Iniciar Busca", command=self.start_search_thread, bg='#007BFF', fg='white', font=('Arial', 10, 'bold'))
        self.search_button.grid(row=0, column=2, rowspan=4, padx=10, sticky="nsew")

        # --- Barra de Progresso ---
        tk.Label(self.master, text="Progresso da Operação:").pack(pady=(10, 2))
        self.progress_var = tk.DoubleVar()
        self.progressbar = ttk.Progressbar(self.master, orient="horizontal", length=880, mode="determinate", variable=self.progress_var)
        self.progressbar.pack(padx=10)
        
        # --- Log Frame ---
        tk.Label(self.master, text="Log da Operação:", font=('Arial', 12, 'bold')).pack(pady=(10, 5))
        self.log_text = scrolledtext.ScrolledText(self.master, width=90, height=25, wrap=tk.WORD, font=('Courier New', 10))
        self.log_text.pack(padx=10, pady=5, fill="both", expand=True)

        # --- Botão Sair/Logout ---
        tk.Button(self.master, text="Sair e Deslogar", command=self.logout_and_exit, bg='#DC3545', fg='white').pack(pady=10)

    # --- Lógica de Busca e Progresso ---

    def log_message(self, message):
        """Insere mensagens no widget de log de forma segura na thread da GUI."""
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)

    def update_progress(self, value):
        """Atualiza a barra de progresso na thread principal da GUI."""
        self.master.after(0, lambda: self.progress_var.set(value))

    def start_search_thread(self):
        """Inicia a busca em uma thread separada para manter a GUI responsiva."""
        if not self.mail:
            messagebox.showerror("Erro", "Você não está logado.")
            return

        search_term = self.term_entry.get().strip()
        search_type = self.search_type.get()
        
        # Obtém as datas como objetos datetime.date
        try:
            data_inicio = self.date_inicio_entry.get_date()
            data_fim = self.date_fim_entry.get_date()
        except ValueError:
             messagebox.showerror("Erro de Data", "Formato de data inválido. Use DD/MM/AAAA.")
             return

        if not search_term:
            messagebox.showwarning("Aviso", "O campo 'Termo' não pode estar vazio.")
            return
        
        if data_inicio > data_fim:
            messagebox.showwarning("Aviso", "A data de início não pode ser posterior à data final.")
            return

        self.log_text.delete('1.0', tk.END)
        self.update_progress(0) # Zera a barra de progresso
        
        self.search_button.config(state=tk.DISABLED, text="Buscando...")

        # Executa a busca em uma thread
        Thread(target=self.perform_search, 
               args=(search_type, search_term, data_inicio, data_fim)).start()

    def perform_search(self, search_type, search_term, data_inicio, data_fim):
        """Função que executa a busca real e atualiza o estado da GUI."""
        try:
            processar_busca(
                self.mail, 
                search_type, 
                search_term, 
                self.save_directory, 
                data_inicio,
                data_fim,
                self.log_message, 
                self.update_progress
            )
        except Exception as e:
            self.log_message(f"❌ ERRO GRAVE DURANTE A BUSCA: {e}")
        finally:
            self.master.after(0, lambda: self.search_button.config(state=tk.NORMAL, text="🔍 Iniciar Busca"))

    def logout_and_exit(self):
        """Desloga e fecha a aplicação."""
        if self.mail:
            try:
                self.mail.logout()
                messagebox.showinfo("Sair", "Deslogado com sucesso.")
            except:
                pass
        self.master.quit()


if __name__ == "__main__":
    os.makedirs(DEFAULT_SAVE_DIRECTORY, exist_ok=True)
    
    root = tk.Tk()
    app = EmailRoboGUI(root)
    
    def on_closing():
        if app.mail:
            try:
                app.mail.logout()
            except:
                pass
        root.destroy()
        
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    root.mainloop()