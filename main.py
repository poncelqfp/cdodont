import random
import os
import re
import string
import smtplib
import json
import time
import threading
import queue
import logging
import signal
import sys
from datetime import datetime
from email.mime.text import MIMEText
from email.utils import formataddr

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------
# MAPPINGS
# ---------------------------
CHARSET_MAP = {
    1: "utf-8", 2: "iso-8859-1", 3: "iso-8859-2", 4: "iso-8859-3",
    5: "iso-8859-4", 6: "iso-8859-5", 7: "iso-8859-6", 8: "iso-8859-7",
    9: "iso-8859-8", 10: "iso-8859-9", 11: "iso-8859-10",
    12: "windows-1250", 13: "windows-1251", 14: "windows-1252",
    15: "windows-1253", 16: "windows-1254", 17: "windows-1255",
    18: "windows-1256", 19: "windows-1257", 20: "windows-1258",
    21: "koi8-r", 22: "koi8-u", 23: "gb2312", 24: "gbk", 25: "big5",
    26: "euc-jp", 27: "euc-kr"
}
TRANSFER_MAP = {1: "base64", 2: "quoted-printable", 3: "binary", 4: "8bit", 5: "7bit"}
PRIORITY_MAP = {1: ("5", "Low"), 2: ("3", "Normal"), 3: ("1", "High")}
DEFAULT_CHARSET = "utf-8"
DEFAULT_TRANSFER = "quoted-printable"
DEFAULT_PRIORITY = ("3", "Normal")
DEFAULT_RANDOM_LENGTH = 6
MAX_RANDOM_LENGTH = 9

# ---------------------------
# ENCODING HANDLER
# ---------------------------
class EncodingHandler:
    def __init__(self, config):
        self.config = config
        self.char_counter = 0
        self.trans_counter = 0

        char_cfg = config.get("Encoding", {}).get("Character_Encoding", {})
        char_mode = char_cfg.get("Mode", 1)
        char_options = char_cfg.get("Options", [])
        valid_char = [opt for opt in char_options if opt in CHARSET_MAP]
        if not valid_char:
            self.current_charset = DEFAULT_CHARSET
        elif char_mode == 1:
            self.current_charset = CHARSET_MAP[valid_char[0]]
        else:
            self.current_charset = CHARSET_MAP[random.choice(valid_char)]

        trans_cfg = config.get("Encoding", {}).get("Transfer_Encoding", {})
        trans_mode = trans_cfg.get("Mode", 1)
        trans_options = trans_cfg.get("Options", [])
        valid_trans = [opt for opt in trans_options if opt in TRANSFER_MAP]
        if not valid_trans:
            self.current_transfer = DEFAULT_TRANSFER
        elif trans_mode == 1:
            self.current_transfer = TRANSFER_MAP[valid_trans[0]]
        else:
            self.current_transfer = TRANSFER_MAP[random.choice(valid_trans)]

    def _validate_options(self, options, mapping):
        return [opt for opt in options if opt in mapping]

    def _get_rotating_value(self, cfg, mapping, counter_attr, current_attr, default):
        try:
            mode = cfg.get("Mode", 1)
            if mode not in (1, 2):
                return default
            options = self._validate_options(cfg.get("Options", []), mapping)
            rotate = max(cfg.get("Rotate_Every", 1), 1)
            if not options:
                return default
            if mode == 1:
                return mapping[options[0]]
            counter = getattr(self, counter_attr)
            current = getattr(self, current_attr)
            if counter != 0 and counter % rotate == 0:
                current = mapping[random.choice(options)]
                setattr(self, current_attr, current)
            setattr(self, counter_attr, counter + 1)
            return current
        except Exception:
            return default

    def get_charset(self):
        cfg = self.config.get("Encoding", {}).get("Character_Encoding", {})
        return self._get_rotating_value(cfg, CHARSET_MAP, "char_counter", "current_charset", DEFAULT_CHARSET)

    def get_transfer_encoding(self):
        cfg = self.config.get("Encoding", {}).get("Transfer_Encoding", {})
        return self._get_rotating_value(cfg, TRANSFER_MAP, "trans_counter", "current_transfer", DEFAULT_TRANSFER)

    def get_priority_headers(self):
        try:
            value = self.config.get("Sender_Setup", {}).get("Priority", 2)
            return PRIORITY_MAP.get(value, DEFAULT_PRIORITY)
        except Exception:
            return DEFAULT_PRIORITY

    def build_headers(self):
        charset = self.get_charset()
        transfer = self.get_transfer_encoding()
        priority_num, priority_text = self.get_priority_headers()
        return {
            "Content-Type": f"text/html; charset={charset}",
            "Content-Transfer-Encoding": transfer,
            "X-Priority": priority_num,
            "X-MSMail-Priority": priority_text,
            "MIME-Version": "1.0"
        }

# ---------------------------
# LETTER HANDLER
# ---------------------------
class LetterHandler:
    def __init__(self, config):
        self.config = config
        self.counter = 0
        self.current_letter = None
        self.letter_pool = []
        self.shuffled_letters = []
        self.shuffle_index = 0
        self._initialize()

    def _initialize(self):
        cfg = self.config.get("Letters", {})
        self.mode = cfg.get("Mode", 1)
        self.path = cfg.get("Path", "letters/")
        self.selected_letter = cfg.get("Selected_Letter")
        self.rotate = max(cfg.get("Rotate_Every", 1), 1)
        self.file_types = cfg.get("File_Types", [".html"])
        if self.mode in (2, 3):
            self.letter_pool = self._load_letters()
        if self.mode == 1:
            self.current_letter = self.selected_letter
        elif self.mode == 2:
            self.current_letter = random.choice(self.letter_pool) if self.letter_pool else self.selected_letter
        elif self.mode == 3:
            self._reshuffle()

    def _load_letters(self):
        try:
            if not os.path.isdir(self.path):
                logger.warning(f"Letters directory '{self.path}' does not exist.")
                return []
            files = os.listdir(self.path)
            return [f for f in files if any(f.lower().endswith(ext) for ext in self.file_types)]
        except Exception as e:
            logger.error(f"Error loading letters from {self.path}: {e}")
            return []

    def _reshuffle(self):
        if not self.letter_pool:
            self.current_letter = self.selected_letter
            return
        self.shuffled_letters = self.letter_pool[:]
        random.shuffle(self.shuffled_letters)
        self.shuffle_index = 0
        self.current_letter = self.shuffled_letters[0]

    def get_letter(self):
        try:
            if self.mode == 1:
                return self.current_letter
            if not self.letter_pool:
                return self.selected_letter
            if self.mode == 2:
                if self.counter != 0 and self.counter % self.rotate == 0:
                    self.current_letter = random.choice(self.letter_pool)
                self.counter += 1
                return self.current_letter
            if self.mode == 3:
                if self.counter != 0 and self.counter % self.rotate == 0:
                    self.shuffle_index += 1
                    if self.shuffle_index >= len(self.shuffled_letters):
                        self._reshuffle()
                    else:
                        self.current_letter = self.shuffled_letters[self.shuffle_index]
                self.counter += 1
                return self.current_letter
            return self.selected_letter
        except Exception:
            return self.selected_letter

    def get_letter_path(self):
        letter = self.get_letter()
        if not letter:
            return None
        return os.path.join(self.path, letter)

# ---------------------------
# TEMPLATE RENDERER
# ---------------------------
class TemplateRenderer:
    def __init__(self):
        self.random_patterns = {
            "LCHAR": string.ascii_lowercase + string.digits,
            "UCHAR": string.ascii_uppercase + string.digits,
            "CHAR": string.ascii_letters + string.digits,
            "NUM": string.digits,
            "TEXT": string.ascii_letters,
            "LTEXT": string.ascii_lowercase,
            "UTEXT": string.ascii_uppercase
        }
        self.random_names = [
            "James", "John", "Robert", "Michael", "William",
            "David", "Richard", "Joseph", "Thomas", "Charles",
            "Daniel", "Matthew", "Anthony", "Mark", "Donald",
            "Steven", "Paul", "Andrew", "Joshua", "Kenneth"
        ]
        self.placeholder_cache = {}

    def reset_cache(self):
        self.placeholder_cache = {}

    def _generate_random(self, charset, length):
        return ''.join(random.choice(charset) for _ in range(length))

    def _get_email_parts(self, email):
        try:
            user, domain = email.split('@', 1)
            namedomain = domain.split('.')[0]
            return {"email": email, "user": user, "domain": domain, "namedomain": namedomain}
        except Exception:
            return {"email": "", "user": "", "domain": "", "namedomain": ""}

    def _replace_basic_placeholders(self, content, recipient_email, smtp_email):
        recipient = self._get_email_parts(recipient_email)
        smtp = self._get_email_parts(smtp_email)
        if "##NAME##" not in self.placeholder_cache:
            self.placeholder_cache["##NAME##"] = random.choice(self.random_names)
        replacements = {
            "##DATE##": datetime.now().strftime("%Y-%m-%d"),
            "##TIME##": datetime.now().strftime("%H:%M:%S"),
            "##NAME##": self.placeholder_cache["##NAME##"],
            "##EMAIL##": recipient["email"],
            "##USER##": recipient["user"],
            "##EMAILDOMAIN##": recipient["domain"],
            "##NAMEDOMAIN##": recipient["namedomain"],
            "##SMTPEMAIL##": smtp["email"],
            "##SMTPUSER##": smtp["user"],
            "##SMTPEMAILDOMAIN##": smtp["domain"],
            "##SMTPNAMEDOMAIN##": smtp["namedomain"]
        }
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        return content

    def _replace_cached_patterns(self, content):
        pattern = r'##([A-Z]+)(\d*)##'
        matches = re.findall(pattern, content)
        for placeholder_type, number in matches:
            if placeholder_type not in self.random_patterns:
                continue
            full_placeholder = f"##{placeholder_type}{number}##"
            if full_placeholder in self.placeholder_cache:
                generated = self.placeholder_cache[full_placeholder]
            else:
                length = int(number) if number else DEFAULT_RANDOM_LENGTH
                length = min(max(length, 1), MAX_RANDOM_LENGTH)
                generated = self._generate_random(self.random_patterns[placeholder_type], length)
                self.placeholder_cache[full_placeholder] = generated
            content = content.replace(full_placeholder, generated)
        return content

    def _replace_dynamic_patterns(self, content):
        # FIX: Each occurrence gets a brand new random value
        def replacer(match):
            placeholder_type = match.group(1)
            number = match.group(2)
            if placeholder_type not in self.random_patterns:
                return match.group(0)  # return unchanged
            length = int(number) if number else DEFAULT_RANDOM_LENGTH
            length = min(max(length, 1), MAX_RANDOM_LENGTH)
            return self._generate_random(self.random_patterns[placeholder_type], length)
        pattern = r'##RAND:([A-Z]+)(\d*)##'
        return re.sub(pattern, replacer, content)

    def load_template(self, path):
        if not path:
            return ""
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as file:
                return file.read()
        except Exception as e:
            logger.error(f"Failed to load template {path}: {e}")
            return ""

    def render_string(self, content, recipient_email="", smtp_email=""):
        if not content:
            return ""
        content = self._replace_basic_placeholders(content, recipient_email, smtp_email)
        content = self._replace_dynamic_patterns(content)
        content = self._replace_cached_patterns(content)
        return content

    def render(self, template_path, recipient_email="", smtp_email=""):
        content = self.load_template(template_path)
        return self.render_string(content, recipient_email, smtp_email)

# ---------------------------
# MESSAGE PROFILE HANDLER
# ---------------------------
class MessageProfileHandler:
    def __init__(self, config, renderer):
        self.config = config
        self.renderer = renderer
        self.subject_counter = 0
        self.from_counter = 0
        self.reply_counter = 0
        self.subject_current = None
        self.from_current = None
        self.reply_current = None
        self.subject_shuffled = []
        self.subject_shuffle_idx = 0
        self.from_shuffled = []
        self.from_shuffle_idx = 0
        self.reply_shuffled = []
        self.reply_shuffle_idx = 0
        self._initialize()

    def _initialize(self):
        message_cfg = self.config.get("Message", {})
        self._init_field(message_cfg.get("Subjects", {}), "subject_current", "subject_shuffled", "subject_shuffle_idx")
        self._init_field(message_cfg.get("From_Names", {}), "from_current", "from_shuffled", "from_shuffle_idx")
        self._init_field(message_cfg.get("Reply_To", {}), "reply_current", "reply_shuffled", "reply_shuffle_idx")

    def _init_field(self, cfg, current_attr, shuffled_attr, shuffle_idx_attr):
        mode = cfg.get("Mode", 1)
        options = cfg.get("Options", [])
        if not options:
            setattr(self, current_attr, "")
            return
        if mode == 1:
            setattr(self, current_attr, options[0])
        elif mode == 2:
            setattr(self, current_attr, random.choice(options))
        elif mode == 3:
            shuffled = options[:]
            random.shuffle(shuffled)
            setattr(self, shuffled_attr, shuffled)
            setattr(self, shuffle_idx_attr, 0)
            setattr(self, current_attr, shuffled[0])
        else:
            setattr(self, current_attr, options[0])

    def _reshuffle(self, options, shuffled_attr, shuffle_idx_attr, current_attr):
        shuffled = options[:]
        random.shuffle(shuffled)
        setattr(self, shuffled_attr, shuffled)
        setattr(self, shuffle_idx_attr, 0)
        setattr(self, current_attr, shuffled[0])

    def _rotate_value(self, cfg, counter_attr, current_attr, shuffled_attr=None, shuffle_idx_attr=None):
        mode = cfg.get("Mode", 1)
        options = cfg.get("Options", [])
        rotate = max(cfg.get("Rotate_Every", 1), 1)
        if not options:
            return ""
        if mode == 1:
            return options[0]
        counter = getattr(self, counter_attr)
        current = getattr(self, current_attr)
        if counter != 0 and counter % rotate == 0:
            if mode == 2:
                current = random.choice(options)
                setattr(self, current_attr, current)
            elif mode == 3:
                shuffled = getattr(self, shuffled_attr)
                idx = getattr(self, shuffle_idx_attr)
                idx += 1
                if idx >= len(shuffled):
                    self._reshuffle(options, shuffled_attr, shuffle_idx_attr, current_attr)
                else:
                    setattr(self, shuffle_idx_attr, idx)
                    setattr(self, current_attr, shuffled[idx])
                current = getattr(self, current_attr)
        setattr(self, counter_attr, counter + 1)
        return current

    def get_subject(self):
        return self._rotate_value(self.config.get("Message", {}).get("Subjects", {}),
                                  "subject_counter", "subject_current",
                                  "subject_shuffled", "subject_shuffle_idx")

    def get_from_name(self):
        return self._rotate_value(self.config.get("Message", {}).get("From_Names", {}),
                                  "from_counter", "from_current",
                                  "from_shuffled", "from_shuffle_idx")

    def get_reply_to(self):
        cfg = self.config.get("Message", {}).get("Reply_To", {})
        if not cfg.get("Enabled", False):
            return ""
        return self._rotate_value(cfg, "reply_counter", "reply_current",
                                  "reply_shuffled", "reply_shuffle_idx")

    def get_custom_headers(self, recipient_email="", smtp_email=""):
        cfg = self.config.get("Message", {}).get("Custom_Headers", {})
        if not cfg.get("Enabled", False):
            return {}
        headers = {}
        for item in cfg.get("Headers", []):
            key = item.get("Key", "").strip()
            value = item.get("Value", "")
            if not key:
                continue
            headers[key] = self.renderer.render_string(value, recipient_email, smtp_email)
        return headers

    def build(self, recipient_email="", smtp_email=""):
        subject = self.renderer.render_string(self.get_subject(), recipient_email, smtp_email)
        from_name = self.renderer.render_string(self.get_from_name(), recipient_email, smtp_email)
        reply_to = self.renderer.render_string(self.get_reply_to(), recipient_email, smtp_email)
        custom_headers = self.get_custom_headers(recipient_email, smtp_email)
        return {"subject": subject, "from_name": from_name, "reply_to": reply_to, "custom_headers": custom_headers}

# ---------------------------
# COMBINED CONTROLLER
# ---------------------------
class EmailContentBuilder:
    def __init__(self, config):
        self.config = config
        self.encoding = EncodingHandler(config)
        self.letters = LetterHandler(config)
        self.renderer = TemplateRenderer()
        self.message_profiles = MessageProfileHandler(config, self.renderer)

    def build(self, recipient_email="", smtp_email=""):
        self.renderer.reset_cache()
        headers = self.encoding.build_headers()
        letter_path = self.letters.get_letter_path()
        rendered_body = self.renderer.render(template_path=letter_path, recipient_email=recipient_email, smtp_email=smtp_email)
        profile = self.message_profiles.build(recipient_email, smtp_email)
        headers.update(profile["custom_headers"])
        if profile["reply_to"]:
            headers["Reply-To"] = profile["reply_to"]
        return {
            "headers": headers,
            "letter_path": letter_path,
            "body": rendered_body,
            "subject": profile["subject"],
            "from_name": profile["from_name"]
        }

# ---------------------------
# SMTP WORKER & MANAGER
# ---------------------------
def load_json_lines(path):
    entries = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entries.append(json.loads(line))
    except FileNotFoundError:
        logger.error(f"SMTP file not found: {path}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {path}: {e}")
        raise
    return entries

class SMTPConfig:
    def __init__(self, raw, global_defaults):
        self.host = raw["host"]
        self.port = int(raw["port"])
        self.username = raw["username"]
        self.password = raw["password"]
        self.security = raw.get("security", "tls").lower()
        self.from_email = raw.get("from_email", "")
        self.raw_from_name = raw.get("from_name", "")
        self.raw_reply_to = raw.get("reply_to", "")
        self.raw_return_path = raw.get("return_path", "")
        self.enabled = raw.get("enabled", True)
        self.max_per_connection = raw.get("max_per_connection") or global_defaults["max_per_conn"]
        self.delay_between = raw.get("delay_between_emails") or global_defaults["delay"]
        self.max_failures = raw.get("max_failures") or global_defaults["max_failures"]
        self.renderer = None

    def set_renderer(self, renderer):
        self.renderer = renderer

    def create_connection(self):
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                if self.security == "ssl":
                    conn = smtplib.SMTP_SSL(self.host, self.port, timeout=30)
                else:
                    conn = smtplib.SMTP(self.host, self.port, timeout=30)
                    conn.ehlo()
                    if self.security == "tls":
                        # Correct STARTTLS detection
                        if conn.has_extn("starttls"):
                            conn.starttls()
                            conn.ehlo()
                        else:
                            raise Exception("STARTTLS not supported by server")
                conn.login(self.username, self.password)
                return conn
            except Exception as e:
                logger.error(f"Connection attempt {attempt+1}/{max_attempts} failed for {self.host}: {e}")
                if attempt < max_attempts - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise

    def compose_from(self, recipient_email="", smtp_email=""):
        if not self.renderer:
            return self.from_email
        name = self.renderer.render_string(self.raw_from_name, recipient_email, self.from_email)
        if name:
            return formataddr((name, self.from_email))
        return self.from_email

    def get_reply_to(self, recipient_email="", smtp_email=""):
        if not self.raw_reply_to or not self.renderer:
            return ""
        return self.renderer.render_string(self.raw_reply_to, recipient_email, self.from_email)

    def get_return_path(self, recipient_email="", smtp_email=""):
        if not self.raw_return_path or not self.renderer:
            return ""
        return self.renderer.render_string(self.raw_return_path, recipient_email, self.from_email)

class SMTPWorker(threading.Thread):
    def __init__(self, smtp_config, builder, recipient_queue, dead_letters, dead_letters_lock, stop_event):
        super().__init__(daemon=True)
        self.smtp = smtp_config
        self.builder = builder
        self.queue = recipient_queue
        self.dead_letters = dead_letters
        self.dead_letters_lock = dead_letters_lock
        self.stop_event = stop_event
        self.sent_count = 0
        self.failure_count = 0
        self.conn = None

    def run(self):
        self.smtp.set_renderer(TemplateRenderer())
        try:
            self.conn = self.smtp.create_connection()
            logger.info(f"Connected to {self.smtp.host} as {self.smtp.from_email}")
        except Exception as e:
            logger.error(f"Could not connect to {self.smtp.host}: {e}")
            return

        try:
            while not self.stop_event.is_set():
                try:
                    recipient = self.queue.get(timeout=1)
                except queue.Empty:
                    # No recipient available; if queue is empty and no more items will be added, exit
                    if self.queue.empty() and not self.stop_event.is_set():
                        # But we must be careful: another worker might have taken the last item.
                        # To avoid race, we simply continue waiting until stop_event is set.
                        continue
                    continue

                email = recipient.strip()
                # Always mark task as done, even on failure
                try:
                    if not email:
                        continue
                    msg = self._build_message(email)
                    self._send_message(msg, email)
                except Exception as e:
                    logger.error(f"Send failed for {email} via {self.smtp.host}: {e}")
                    self._mark_dead(email)
                    self.failure_count += 1
                    if self.failure_count >= self.smtp.max_failures:
                        logger.error(f"Disabling SMTP {self.smtp.host} after {self.failure_count} consecutive failures.")
                        break
                    self._reconnect()
                else:
                    self.sent_count += 1
                    self.failure_count = 0
                    if self.smtp.delay_between > 0:
                        time.sleep(self.smtp.delay_between)
                    if self.sent_count >= self.smtp.max_per_connection:
                        self._reconnect()
                finally:
                    self.queue.task_done()   # Always called

        finally:
            if self.conn:
                try:
                    self.conn.quit()
                except:
                    pass
            logger.info(f"Worker {self.smtp.host} done. Sent: {self.sent_count}, Failures: {self.failure_count}")

    def _build_message(self, recipient):
        built = self.builder.build(recipient_email=recipient, smtp_email=self.smtp.from_email)
        charset = built["headers"].get("Content-Type", "text/html; charset=utf-8")
        if "charset=" in charset:
            charset = charset.split("charset=")[-1].split(";")[0].strip()
        else:
            charset = "utf-8"
        msg = MIMEText(built["body"], "html", charset)
        msg["From"] = self.smtp.compose_from(recipient, self.smtp.from_email)
        msg["To"] = recipient
        msg["Subject"] = built["subject"]
        for hdr, val in built["headers"].items():
            if hdr.lower() not in ("content-type", "content-transfer-encoding", "mime-version"):
                msg[hdr] = val
        reply = self.smtp.get_reply_to(recipient, self.smtp.from_email)
        if reply:
            msg["Reply-To"] = reply
        elif built.get("reply_to"):
            msg["Reply-To"] = built["reply_to"]
        ret_path = self.smtp.get_return_path(recipient, self.smtp.from_email)
        if ret_path:
            msg["Return-Path"] = ret_path
        return msg

    def _send_message(self, msg, recipient):
        self.conn.sendmail(self.smtp.from_email, [recipient], msg.as_string())

    def _mark_dead(self, email):
        logger.warning(f"Marking as dead: {email}")
        with self.dead_letters_lock:
            self.dead_letters.append(email)

    def _reconnect(self):
        try:
            self.conn.quit()
        except:
            pass
        delay = self.builder.config.get("SMTP", {}).get("Reconnect_Delay", 2)
        for attempt in range(3):
            try:
                logger.info(f"Reconnecting {self.smtp.host} (attempt {attempt+1})...")
                time.sleep(delay * (2 ** attempt))
                self.conn = self.smtp.create_connection()
                self.sent_count = 0
                logger.info(f"Reconnected to {self.smtp.host}")
                return
            except Exception as e:
                logger.error(f"Reconnect attempt {attempt+1} failed: {e}")
        raise Exception(f"Failed to reconnect to {self.smtp.host} after 3 attempts")

class SMTPManager:
    def __init__(self, config, builder_factory):
        self.config = config
        self.builder_factory = builder_factory
        smtp_path = config.get("SMTP", {}).get("Path", "smtps/smtp.json")
        self.global_defaults = {
            "max_per_conn": config.get("SMTP", {}).get("Default_Max_Per_Connection", 15),
            "delay": config.get("SMTP", {}).get("Default_Delay_Between_Emails", 10),
            "max_failures": config.get("SMTP", {}).get("Default_Max_Failures", 3),
        }
        skip_disabled = config.get("SMTP", {}).get("Skip_Disabled_SMTPs", True)
        raw_smtps = load_json_lines(smtp_path)
        self.smtp_templates = []
        for raw in raw_smtps:
            if skip_disabled and not raw.get("enabled", True):
                continue
            cfg = SMTPConfig(raw, self.global_defaults)
            self.smtp_templates.append(cfg)
        if not self.smtp_templates:
            raise RuntimeError("No active SMTP servers found.")
        self.recipient_queue = queue.Queue()
        self.dead_letters = []
        self.dead_letters_lock = threading.Lock()
        self.stop_event = threading.Event()

    def load_recipients(self, path="recipients.txt"):
        # FIX: Stream line by line, no massive list
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.recipient_queue.put(line)
            logger.info(f"Loaded recipients from {path} into queue (size {self.recipient_queue.qsize()})")
        except FileNotFoundError:
            logger.error(f"Recipients file not found: {path}")
            raise

    def run(self, recipient_file="recipients.txt"):
        self.load_recipients(recipient_file)

        workers = []
        for smtp_template in self.smtp_templates:
            # Clone SMTP config
            smtp_cfg = SMTPConfig(
                {
                    "host": smtp_template.host,
                    "port": smtp_template.port,
                    "username": smtp_template.username,
                    "password": smtp_template.password,
                    "security": smtp_template.security,
                    "from_email": smtp_template.from_email,
                    "from_name": smtp_template.raw_from_name,
                    "reply_to": smtp_template.raw_reply_to,
                    "return_path": smtp_template.raw_return_path,
                    "enabled": True,
                    "max_per_connection": smtp_template.max_per_connection,
                    "delay_between_emails": smtp_template.delay_between,
                    "max_failures": smtp_template.max_failures,
                },
                self.global_defaults
            )
            builder = self.builder_factory()
            worker = SMTPWorker(smtp_cfg, builder, self.recipient_queue, self.dead_letters, self.dead_letters_lock, self.stop_event)
            worker.start()
            workers.append(worker)

        def signal_handler(sig, frame):
            logger.info("Received interrupt, stopping workers...")
            self.stop_event.set()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)

        # FIX: Simple dead worker monitoring without redundancy
        while True:
            alive = [w for w in workers if w.is_alive()]
            if not alive:
                logger.error("All workers have died. Exiting.")
                break
            time.sleep(1)

        for w in workers:
            w.join(timeout=5)

        if self.dead_letters:
            unique = list(set(self.dead_letters))
            with open("dead.txt", "w", encoding="utf-8") as f:
                for addr in unique:
                    f.write(addr + "\n")
            logger.info(f"Dead letters saved to dead.txt ({len(unique)} unique).")
        else:
            logger.info("No dead letters.")
        logger.info("All SMTP workers finished.")

# ------------------------------------------------------------
if __name__ == "__main__":
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    def builder_factory():
        return EmailContentBuilder(config)

    manager = SMTPManager(config, builder_factory)
    manager.run("recipients.txt")