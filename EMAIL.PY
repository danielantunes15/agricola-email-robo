import tkinter as tk
from tkinter import messagebox, scrolledtext, filedialog, ttk
from threading import Thread
import os
import re
from datetime import date, timedelta
import imaplib
import email 
import pdfplumber
from tkcalendar import DateEntry 
from PIL import Image, ImageTk 

# --- CONFIGURAÇÕES GLOBAIS ---
IMAP_SERVER = "imap.gmail.com"
DEFAULT_SAVE_DIRECTORY = os.path.join(os.path.expanduser('~'), "Desktop", "ARQUIVOS_ROBO_EMAIL")
LAST_EMAIL_FILE = "last_email.txt"
# Regex para aceitar zero ou mais separadores (ponto, traço, barra, espaço)
SEPARATOR_REGEX = r'[\.\-/\s]*'
# -----------------------------

# --- TEMA E CORES MODERNAS ---
COLORS = {
    'primary': '#2C3E50',
    'secondary': '#3498DB',
    'success': '#27AE60',
    'danger': '#E74C3C',
    'warning': '#F39C12',
    'info': '#17A2B8',
    'light': '#ECF0F1',
    'dark': '#2C3E50',
    'background': '#F8F9FA',
    'text': '#2C3E50',
    'accent': '#9B59B6'
}

# Estilo moderno
FONT_PRIMARY = ('Segoe UI', 10)
FONT_BOLD = ('Segoe UI', 10, 'bold')
FONT_TITLE = ('Segoe UI', 14, 'bold')
FONT_HEADER = ('Segoe UI', 12, 'bold')
# -----------------------------

# --- FUNÇÕES DE LÓGICA (IMAP/PDF) ---

def get_search_regex(search_type, search_term):
    """
    Cria a expressão regular flexível (mais assertiva) baseada no tipo de busca.
    """
    # Remove caracteres de formatação da entrada do usuário, deixando apenas dígitos
    clean_term = re.sub(r'[.\-/\s]', '', search_term)

    if search_type == 'CNPJ':
        if len(clean_term) != 14:
            return None
        # Divide o CNPJ em partes: 2.3.3.4.2
        parts = [clean_term[0:2], clean_term[2:5], clean_term[5:8], clean_term[8:12], clean_term[12:14]]
        # Constrói a regex permitindo separadores flexíveis entre as partes numéricas
        pattern = SEPARATOR_REGEX.join(map(re.escape, parts))
        return re.compile(pattern)
        
    elif search_type == 'CPF':
        if len(clean_term) != 11:
            return None
        # Divide o CPF em partes: 3.3.3.2
        parts = [clean_term[0:3], clean_term[3:6], clean_term[6:9], clean_term[9:11]]
        pattern = SEPARATOR_REGEX.join(map(re.escape, parts))
        return re.compile(pattern)

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
            
            # Remove quebras de linha e espaços para tornar a busca de números mais robusta contra OCR
            texto_para_busca_limpa = re.sub(r'[\s\n\r]', '', texto_extraido)

            # Para nomes, busca no texto original para preservar a separação de palavras
            if search_regex.flags & re.IGNORECASE: 
                 if search_regex.search(texto_extraido):
                    return True
            
            # Para CNPJ/CPF, busca no texto limpo usando a regex flexível
            elif search_regex.search(texto_para_busca_limpa):
                return True
            
    except Exception as e:
        return f"⚠ Erro ao processar PDF: {e}"
    return False

def processar_busca(mail, search_type, search_term, save_directory, data_inicio_dt, data_fim_dt, log_callback, progress_callback):
    """Função central de busca e filtragem, agora com criação de subpasta."""
    
    search_regex = get_search_regex(search_type, search_term)
    if not search_regex:
        log_callback("❌ Tipo de busca, ou termo CNPJ/CPF incompleto ou inválido.")
        progress_callback(0)
        return

    data_inicio_imap = data_inicio_dt.strftime("%d-%b-%Y")
    data_fim_search = (data_fim_dt + timedelta(days=1)).strftime("%d-%b-%Y") # Adiciona +1 dia (IMAP BEFORE é exclusivo)
    
    # NOVO: Cria a subpasta com o nome do termo de busca sanitizado
    sanitized_term = sanitize_filename(search_term)
    final_save_directory = os.path.join(save_directory, sanitized_term) # Define o novo diretório
    
    search_criteria = f'(SINCE {data_inicio_imap} BEFORE {data_fim_search})'
    
    log_callback(f"\n⚙️ Iniciando busca na 'inbox' (Período: {data_inicio_imap} a {data_fim_dt.strftime('%d-%b-%Y')})...")
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
        progress_callback(100)
        return

    # ATUALIZADO: Cria o diretório final, que inclui a subpasta do termo de busca
    os.makedirs(final_save_directory, exist_ok=True) 
    notas_salvas = 0
    progress_per_email = 100 / total_emails
    current_progress = 0

    for i, num in enumerate(message_ids):
        current_progress += progress_per_email
        progress_callback(current_progress)

        status, data = mail.fetch(num, "(RFC822)")
        if not data or data[0] is None: continue

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                filename = part.get_filename()
                
                if filename and filename.lower().endswith(".pdf"):
                    filename = sanitize_filename(filename)
                    # ATUALIZADO: Salva temporariamente dentro do novo subdiretório
                    temp_filepath = os.path.join(final_save_directory, f"temp_{num.decode('utf-8')}_{filename}")

                    try:
                        with open(temp_filepath, "wb") as f:
                            f.write(part.get_payload(decode=True))
                        
                        resultado_verificacao = verifica_termo_no_anexo(temp_filepath, search_regex)
                        
                        if resultado_verificacao is True:
                            # ATUALIZADO: Define o caminho final dentro do novo subdiretório
                            final_filepath = os.path.join(final_save_directory, filename)
                            
                            if os.path.exists(final_filepath):
                                base, ext = os.path.splitext(filename)
                                # ATUALIZADO: Define o caminho duplicado dentro do novo subdiretório
                                final_filepath = os.path.join(final_save_directory, f"{base}_DUPLICADO{ext}")
                                
                            os.rename(temp_filepath, final_filepath)
                            log_callback(f"  ✔ Arquivo salvo: {filename}")
                            notas_salvas += 1
                        elif isinstance(resultado_verificacao, str):
                            log_callback(f"  ❌ Falha na leitura do PDF '{filename}': {resultado_verificacao}")
                            os.remove(temp_filepath)
                        else:
                            os.remove(temp_filepath)
                            
                    except OSError as e:
                        log_callback(f"  ❌ Erro ao salvar o anexo '{filename}': {e}")
    
    progress_callback(100)
    log_callback("-" * 40)
    # ATUALIZADO: Mostra o novo caminho completo no log
    log_callback(f"✅ Busca concluída. {notas_salvas} anexos relevantes salvos em: {final_save_directory}")
    log_callback("-" * 40)

# --------------------

class ModernButton(tk.Button):
    """Botão com estilo moderno"""
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.config(
            font=FONT_BOLD,
            relief='flat',
            bd=0,
            padx=20,
            pady=8,
            cursor='hand2'
        )

class EmailRoboGUI:
    def __init__(self, master):
        self.master = master
        master.title("🤖 Robô de Busca de Documentos - Daniel Antunes Tecnologia")
        master.geometry("750x600")
        master.configure(bg=COLORS['background'])
        
        # Configurar estilo para widgets ttk
        self.setup_styles()
        
        self.mail = None
        self.save_directory = DEFAULT_SAVE_DIRECTORY
        self.last_email = self._load_last_email()
        
        # --- Configurações do GIF ---
        self.gif_frames = []
        self.gif_delay = 0
        self.gif_index = 0
        self.gif_running = False
        self.gif_label = None
        # Caminho do GIF fornecido pelo usuário (usando r"" para tratar as barras)
        self.gif_path = r"C:\Users\Daniel\OneDrive\Área de Trabalho\aplicativo_email_agricola\jobson.gif"
        # ---------------------------
        
        self.create_login_widgets()

    def setup_styles(self):
        """Configura estilos modernos para os widgets"""
        style = ttk.Style()
        
        # Configurar estilo da progressbar
        style.theme_use('clam')
        style.configure("Modern.Horizontal.TProgressbar",
                       troughcolor=COLORS['light'],
                       background=COLORS['secondary'],
                       bordercolor=COLORS['light'],
                       lightcolor=COLORS['secondary'],
                       darkcolor=COLORS['secondary'])
        
        # Configurar estilo dos combobox
        style.configure('Modern.TCombobox',
                       fieldbackground=COLORS['light'],
                       background=COLORS['light'],
                       bordercolor=COLORS['secondary'],
                       focuscolor=COLORS['secondary'])

    def _load_last_email(self):
        if os.path.exists(LAST_EMAIL_FILE):
            try:
                with open(LAST_EMAIL_FILE, 'r') as f:
                    content = f.read().strip()
                    # Remove o prefixo de citação se houver
                    if content.startswith(''):
                        return content.replace('', '')
                    return content
            except Exception:
                return ""
        return ""

    def _save_last_email(self, email_user):
        try:
            with open(LAST_EMAIL_FILE, 'w') as f:
                f.write(email_user)
        except Exception as e:
            print(f"Erro ao salvar e-mail: {e}")

    # --- Métodos de Animação do GIF ---
    def load_gif(self):
        """Carrega todos os frames do GIF, o tempo de delay, e REDIMENSIONA."""
        try:
            image = Image.open(self.gif_path)
            self.gif_frames = []
            
            # TAMANHO MÉDIO DO GIF: 100x100 pixels para melhor visibilidade
            GIF_SIZE = (100, 100)
            
            # Tenta iterar sobre os frames
            try:
                while True:
                    # Redimensiona o frame antes de converter para PhotoImage
                    # Image.Resampling.LANCZOS é usado para melhor qualidade no redimensionamento
                    resized_frame = image.resize(GIF_SIZE, Image.Resampling.LANCZOS)
                    self.gif_frames.append(ImageTk.PhotoImage(resized_frame))
                    image.seek(len(self.gif_frames))
            except EOFError:
                pass # Fim da animação
                
            # Define o delay (usando o valor do próprio GIF, se disponível, ou um padrão)
            self.gif_delay = image.info.get('duration', 100)
            return True

        except FileNotFoundError:
            self.log_message(f"❌ Erro de GIF: Arquivo não encontrado em {self.gif_path}")
            return False
        except Exception as e:
            self.log_message(f"❌ Erro de GIF: Erro ao carregar o GIF: {e}")
            return False

    def animate_gif(self):
        """Atualiza o frame do GIF e agenda o próximo."""
        if not self.gif_running or not self.gif_frames:
            return

        # Garante que o label foi criado
        if self.gif_label:
            # Pega o próximo frame
            frame = self.gif_frames[self.gif_index]
            self.gif_label.configure(image=frame)
            self.gif_label.image = frame # Manter referência
            
            self.gif_index = (self.gif_index + 1) % len(self.gif_frames)
            
            # Agenda a próxima atualização
            self.master.after(self.gif_delay, self.animate_gif)
            
    def start_gif_animation(self):
        """Prepara e inicia a animação."""
        if not self.gif_frames:
             if not self.load_gif():
                 return
                 
        self.gif_index = 0
        self.gif_running = True
        self.gif_label.pack(side='right', padx=10, pady=5) # MOSTRA o GIF no canto superior direito do log
        self.animate_gif()
        
    def stop_gif_animation(self):
        """Para a animação e esconde o GIF."""
        self.gif_running = False
        if self.gif_label:
            self.gif_label.pack_forget() # ESCONDE o GIF
    # ----------------------------------------

    def create_login_widgets(self):
        for widget in self.master.winfo_children():
            widget.destroy()

        # Frame principal do login
        main_frame = tk.Frame(self.master, bg=COLORS['background'], padx=40, pady=40)
        main_frame.pack(expand=True, fill='both')

        # Header com logo/marca
        header_frame = tk.Frame(main_frame, bg=COLORS['background'])
        header_frame.pack(pady=(0, 30))

        tk.Label(header_frame, text="🤖", font=('Arial', 24), bg=COLORS['background']).pack()
        tk.Label(header_frame, text="Daniel Antunes", font=FONT_TITLE, fg=COLORS['primary'], bg=COLORS['background']).pack()
        tk.Label(header_frame, text="Tecnologia", font=('Segoe UI', 12), fg=COLORS['secondary'], bg=COLORS['background']).pack()

        # Card de login
        login_card = tk.Frame(main_frame, bg='white', relief='flat', bd=1, highlightbackground=COLORS['light'], highlightthickness=1)
        login_card.pack(padx=20, pady=10, fill='x')

        tk.Label(login_card, text="Login IMAP", font=FONT_HEADER, fg=COLORS['primary'], bg='white').pack(pady=(20, 10))

        # Formulário
        form_frame = tk.Frame(login_card, bg='white', padx=30, pady=15)
        form_frame.pack(fill='x')

        tk.Label(form_frame, text="E-mail:", font=FONT_BOLD, fg=COLORS['text'], bg='white', anchor='w').pack(fill='x', pady=(5, 2))
        self.email_entry = tk.Entry(form_frame, font=FONT_PRIMARY, relief='flat', bd=1, highlightbackground=COLORS['light'], highlightthickness=1, highlightcolor=COLORS['secondary'])
        self.email_entry.pack(fill='x', pady=(0, 10))
        self.email_entry.insert(0, self.last_email)
        
        tk.Label(form_frame, text="Senha de App:", font=FONT_BOLD, fg=COLORS['text'], bg='white', anchor='w').pack(fill='x', pady=(5, 2))
        self.password_entry = tk.Entry(form_frame, show="*", font=FONT_PRIMARY, relief='flat', bd=1, highlightbackground=COLORS['light'], highlightthickness=1, highlightcolor=COLORS['secondary'])
        self.password_entry.pack(fill='x', pady=(0, 15))

        # Botão de login
        self.login_button = ModernButton(form_frame, text="Entrar no Sistema", 
                                       command=self.start_login_thread, 
                                       bg=COLORS['secondary'], fg='white',
                                       activebackground=COLORS['primary'])
        self.login_button.pack(fill='x', pady=10)
        
        self.login_status = tk.Label(form_frame, text="", font=FONT_PRIMARY, bg='white')
        self.login_status.pack(pady=5)

        # Footer
        footer_frame = tk.Frame(main_frame, bg=COLORS['background'])
        footer_frame.pack(pady=(20, 0))
        tk.Label(footer_frame, text="© 2024 Daniel Antunes Tecnologia - Todos os direitos reservados", 
                font=('Segoe UI', 8), fg=COLORS['dark'], bg=COLORS['background']).pack()

    def start_login_thread(self):
        self.login_status.config(text="Conectando...", fg=COLORS['info'])
        self.login_button.config(state=tk.DISABLED, text="Conectando...")
        
        email_user = self.email_entry.get().strip()
        senha_app = self.password_entry.get()
        
        Thread(target=self.perform_login, args=(email_user, senha_app), daemon=True).start()

    def perform_login(self, email_user, senha_app):
        error_message = None
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            mail.login(email_user, senha_app)
            mail.select("inbox")
            self.mail = mail
            self.master.after(0, lambda: self.login_successful(email_user)) 
        except imaplib.IMAP4.error as e:
            error_message = str(e)
            self.master.after(0, lambda: self.login_failed(error_message))
        except Exception as e:
            error_message = f"Erro Inesperado: {e}"
            self.master.after(0, lambda: self.login_failed(error_message))
            
    def login_successful(self, email_user):
        self._save_last_email(email_user)
        self.login_status.config(text="✅ Conectado com sucesso!", fg=COLORS['success'])
        self.master.after(1000, lambda: self.create_main_menu_widgets())

    def login_failed(self, error_message):
        self.login_status.config(text="❌ Falha na conexão", fg=COLORS['danger'])
        self.login_button.config(state=tk.NORMAL, text="Entrar no Sistema")
        messagebox.showerror("Erro de Login", f"Não foi possível conectar:\n{error_message}")

    def select_save_directory(self):
        new_dir = filedialog.askdirectory(initialdir=self.save_directory)
        if new_dir:
            self.save_directory = new_dir
            self.dir_entry.config(state=tk.NORMAL)
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, self.save_directory)
            self.dir_entry.config(state=tk.DISABLED)

    def create_main_menu_widgets(self):
        for widget in self.master.winfo_children():
            widget.destroy()

        self.master.title("🤖 Robô de Busca - Daniel Antunes Tecnologia")
        self.master.geometry("900x750")

        # Header
        header_frame = tk.Frame(self.master, bg=COLORS['primary'], pady=10)
        header_frame.pack(fill='x', padx=10, pady=(10, 0))

        tk.Label(header_frame, text="🤖 Robô de Busca de Documentos", 
                font=FONT_TITLE, fg='white', bg=COLORS['primary']).pack(side='left', padx=20)
        
        tk.Label(header_frame, text="Daniel Antunes Tecnologia", 
                font=('Segoe UI', 10), fg=COLORS['light'], bg=COLORS['primary']).pack(side='right', padx=20)

        # Container principal
        main_container = tk.Frame(self.master, bg=COLORS['background'])
        main_container.pack(fill='both', expand=True, padx=10, pady=10)

        # Card de configuração
        config_card = tk.LabelFrame(main_container, text="🔧 Configuração da Busca", 
                                  font=FONT_HEADER, fg=COLORS['primary'], bg='white',
                                  relief='flat', bd=1, highlightbackground=COLORS['light'], highlightthickness=1)
        config_card.pack(fill='x', pady=(0, 10))
        config_card.grid_columnconfigure(1, weight=1)

        # Formulário de configuração
        form_padding = {'padx': 10, 'pady': 8, 'sticky': 'ew'}

        tk.Label(config_card, text="Buscar por:", font=FONT_BOLD, bg='white').grid(row=0, column=0, **form_padding)
        self.search_type = tk.StringVar(self.master, "CNPJ")
        search_menu = ttk.Combobox(config_card, textvariable=self.search_type, 
                                 values=["CNPJ", "CPF", "NOME"], state="readonly",
                                 font=FONT_PRIMARY, style='Modern.TCombobox')
        search_menu.grid(row=0, column=1, **form_padding)

        tk.Label(config_card, text="Termo:", font=FONT_BOLD, bg='white').grid(row=1, column=0, **form_padding)
        self.term_entry = tk.Entry(config_card, font=FONT_PRIMARY, relief='flat', bd=1,
                                 highlightbackground=COLORS['light'], highlightthickness=1,
                                 highlightcolor=COLORS['secondary'])
        self.term_entry.grid(row=1, column=1, **form_padding)

        tk.Label(config_card, text="Data Início:", font=FONT_BOLD, bg='white').grid(row=2, column=0, **form_padding)
        self.date_inicio_entry = DateEntry(config_card, width=12, background=COLORS['secondary'],
                                         foreground='white', borderwidth=1, date_pattern='dd/MM/yyyy',
                                         font=FONT_PRIMARY)
        self.date_inicio_entry.set_date(date.today() - timedelta(days=90))
        self.date_inicio_entry.grid(row=2, column=1, **form_padding)
        
        tk.Label(config_card, text="Data Fim:", font=FONT_BOLD, bg='white').grid(row=3, column=0, **form_padding)
        self.date_fim_entry = DateEntry(config_card, width=12, background=COLORS['secondary'],
                                      foreground='white', borderwidth=1, date_pattern='dd/MM/yyyy',
                                      font=FONT_PRIMARY)
        self.date_fim_entry.set_date(date.today())
        self.date_fim_entry.grid(row=3, column=1, **form_padding)
        
        tk.Label(config_card, text="Salvar em:", font=FONT_BOLD, bg='white').grid(row=4, column=0, **form_padding)
        dir_frame = tk.Frame(config_card, bg='white')
        dir_frame.grid(row=4, column=1, **form_padding)
        
        self.dir_entry = tk.Entry(dir_frame, font=FONT_PRIMARY, relief='flat', bd=1,
                                highlightbackground=COLORS['light'], highlightthickness=1)
        self.dir_entry.insert(0, self.save_directory)
        self.dir_entry.config(state=tk.DISABLED)
        self.dir_entry.pack(side='left', fill='x', expand=True)
        
        tk.Button(dir_frame, text="📁", command=self.select_save_directory, 
                 font=('Arial', 10), relief='flat', bg=COLORS['light'],
                 activebackground=COLORS['secondary']).pack(side='right', padx=(5, 0))
        
        # Botão de busca
        self.search_button = ModernButton(config_card, text="🔍 Iniciar Busca Avançada", 
                                        command=self.start_search_thread, 
                                        bg=COLORS['success'], fg='white',
                                        activebackground=COLORS['secondary'])
        self.search_button.grid(row=0, column=2, rowspan=5, padx=20, sticky='ns')

        # Área de progresso
        progress_frame = tk.Frame(main_container, bg=COLORS['background'])
        progress_frame.pack(fill='x', pady=(10, 5))

        tk.Label(progress_frame, text="Progresso da Operação:", font=FONT_BOLD, 
                fg=COLORS['text'], bg=COLORS['background']).pack(anchor='w')
        
        self.progress_var = tk.DoubleVar()
        self.progressbar = ttk.Progressbar(progress_frame, orient="horizontal", 
                                         length=880, mode="determinate", 
                                         variable=self.progress_var,
                                         style="Modern.Horizontal.TProgressbar")
        self.progressbar.pack(fill='x', pady=(5, 0))

        # Área de log
        log_frame = tk.Frame(main_container, bg=COLORS['background'])
        log_frame.pack(fill='both', expand=True, pady=(10, 0))

        # Novo frame para o cabeçalho do log e o GIF
        log_header_frame = tk.Frame(log_frame, bg=COLORS['background'])
        log_header_frame.pack(fill='x', pady=(0, 5))

        tk.Label(log_header_frame, text="📋 Log da Operação:", font=FONT_HEADER, 
                fg=COLORS['text'], bg=COLORS['background']).pack(side='left', anchor='w')
        
        # Label para o GIF (inicialmente não empacotado/invisível)
        self.gif_label = tk.Label(log_header_frame, bg=COLORS['background'])
        
        log_container = tk.Frame(log_frame, bg=COLORS['light'], relief='flat', bd=1,
                               highlightbackground=COLORS['light'], highlightthickness=1)
        log_container.pack(fill='both', expand=True, pady=(5, 0))

        self.log_text = scrolledtext.ScrolledText(log_container, width=90, height=20, 
                                                wrap=tk.WORD, font=('Consolas', 9),
                                                relief='flat', bg='#FEFEFE')
        self.log_text.pack(fill='both', expand=True, padx=1, pady=1)

        # Botão de sair
        button_frame = tk.Frame(main_container, bg=COLORS['background'])
        button_frame.pack(fill='x', pady=10)

        ModernButton(button_frame, text="🚪 Sair e Deslogar", 
                   command=self.logout_and_exit, 
                   bg=COLORS['danger'], fg='white',
                   activebackground=COLORS['warning']).pack(side='right')

        # Footer
        footer_frame = tk.Frame(self.master, bg=COLORS['light'], pady=8)
        footer_frame.pack(fill='x', side='bottom')

        tk.Label(footer_frame, text="© 2024 Daniel Antunes Tecnologia - Soluções em Automação e Tecnologia", 
                font=('Segoe UI', 8), fg=COLORS['dark'], bg=COLORS['light']).pack()

    def log_message(self, message):
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)

    def update_progress(self, value):
        self.master.after(0, lambda: self.progress_var.set(value))

    def start_search_thread(self):
        if not self.mail:
            messagebox.showerror("Erro", "Você não está logado.")
            return

        search_term = self.term_entry.get().strip()
        search_type = self.search_type.get()
        
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
        self.update_progress(0)
        
        self.search_button.config(state=tk.DISABLED, text="Buscando...")
        self.start_gif_animation() # CHAMA O GIF ANTES DE INICIAR A THREAD

        Thread(target=self.perform_search, 
               args=(search_type, search_term, data_inicio, data_fim), daemon=True).start()

    def perform_search(self, search_type, search_term, data_inicio, data_fim):
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
            # Garante que o botão e o GIF são atualizados na thread principal
            self.master.after(0, lambda: self.search_button.config(state=tk.NORMAL, text="🔍 Iniciar Busca Avançada"))
            self.master.after(0, self.stop_gif_animation) # CHAMA O GIF PARA PARAR

    def logout_and_exit(self):
        if self.mail:
            try:
                self.mail.logout()
            except:
                pass
        self.master.quit()


if __name__ == "__main__":
    os.makedirs(DEFAULT_SAVE_DIRECTORY, exist_ok=True)
    
    root = tk.Tk()
    
    # Centralizar a janela
    root.eval('tk::PlaceWindow . center')
    
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