# main.py
# QvaClick Welcome Bot (Telegram) — Python
# Requiere: python-telegram-bot==20.6 y python-dotenv
#
# .env (en el mismo directorio):
# BOT_TOKEN=123456:ABC...
# ALLOWED_CHAT_IDS=-1001234567890,-100222333444   # (opcional) si se vacía, actúa en cualquier grupo
# WELCOME_TOPIC_ID=12                              # (opcional) si se omite/vacío, no usa topic
# SUPER_ADMIN_IDS=111111111,222222222              # (opcional) IDs de usuario con permisos forzados
# WELCOME_DELETE_SECONDS=0                         # (opcional) segundos para borrar la bienvenida (0=desactivado)

import os
import asyncio
import json
import time
from collections import defaultdict, deque
from typing import Dict, Tuple
from collections import defaultdict, deque
from html import escape
from pathlib import Path
from typing import Set, Optional, List, Deque, Dict, Tuple

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    BotCommand,
)
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# Package version
__version__ = "1.0.0"

# === Cargar variables desde .env ===
load_dotenv(Path(__file__).with_name(".env"))

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
RAW_ALLOWED = os.environ.get("ALLOWED_CHAT_IDS", "").strip()
RAW_TOPIC = os.environ.get("WELCOME_TOPIC_ID", "").strip()
RAW_SUPERADM = os.environ.get("SUPER_ADMIN_IDS", "").strip()
RAW_DELETE = os.environ.get("WELCOME_DELETE_SECONDS", "").strip()


def parse_ids(raw: str) -> Set[int]:
    """Acepta separadores coma, punto y coma o espacios. Ignora tokens vacíos o no numéricos."""
    ids: Set[int] = set()
    raw = raw.replace(";", ",").replace(" ", ",")
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        try:
            ids.add(int(t))
        except ValueError:
            pass
    return ids


ALLOWED_CHAT_IDS = parse_ids(RAW_ALLOWED)
SUPER_ADMIN_IDS = parse_ids(RAW_SUPERADM)

WELCOME_TOPIC_ID: Optional[int] = None
try:
    if RAW_TOPIC:
        WELCOME_TOPIC_ID = int(RAW_TOPIC)
except ValueError:
    WELCOME_TOPIC_ID = None  # si no es entero, se ignora

# Tiempo para borrar la bienvenida automáticamente (0 o vacío = desactivado)
WELCOME_DELETE_SECONDS: int = 0
try:
    if RAW_DELETE:
        WELCOME_DELETE_SECONDS = max(0, int(RAW_DELETE))
except ValueError:
    WELCOME_DELETE_SECONDS = 0

# === Estado para manejo de comandos en pasos ===
# Estructura: {(chat_id, user_id): "waiting_for_welcome" | "waiting_for_registration"}
waiting_for_message: dict = {}

# === Cache de mensajes para limpieza manual ===
# Clave: (chat_id, message_thread_id|None) -> deque de message_id
MessageKey = Tuple[int, Optional[int]]
message_cache: Dict[MessageKey, Deque[int]] = defaultdict(lambda: deque(maxlen=2000))
# Pinned por chat (IDs detectados por eventos)
pinned_by_chat: Dict[int, Set[int]] = defaultdict(set)

# === Mensaje de bienvenida por defecto (acortado, según solicitud) ===
DEFAULT_WELCOME = """
👋 ¡Bienvenid@ a QvaClick!

🚀 Comunidad para contratar y ofrecer servicios freelance. Impulsamos el trabajo remoto como una vía real de desarrollo personal y económico.

🔗 Empieza aquí: https://qvaclick.com
""".strip()

# === Mensaje de registro por defecto ===
DEFAULT_REGISTRATION = """
📝 <b>¿Aún no tienes cuenta en QvaClick?</b>
Por favor regístrate para participar según tu rol:

• <b>Freelancer</b>: ofrece tus servicios, crea tu portafolio y recibe propuestas.
• <b>Empleador</b>: publica proyectos, recibe propuestas y contrata con confianza.
""".strip()


# === Cache de mensajes por chat para limpieza manual ===
# Guardamos tuplas (message_id, user_id, is_command, is_service)
MESSAGE_CACHE: Dict[int, deque[Tuple[int, int | None, bool, bool]]] = defaultdict(lambda: deque(maxlen=1000))
SCHEDULED_AUTOCLEAN_CHATS: Set[int] = set()

# Tamaño por defecto de limpieza cuando es automática o si no se especifica
AUTO_CLEAN_DEFAULT_N = 200

# === Auto-clean por chat (horas) ===
def auto_clean_path_for_chat(chat_id: int) -> Path:
    return Path(__file__).with_name(f"auto_clean_{chat_id}.txt")


def load_auto_clean_hours(chat_id: int) -> int:
    p = auto_clean_path_for_chat(chat_id)
    if p.exists():
        try:
            val = int(p.read_text(encoding="utf-8").strip())
            return max(0, val)
        except Exception:
            return 0
    return 0


def save_auto_clean_hours(chat_id: int, hours: int) -> None:
    p = auto_clean_path_for_chat(chat_id)
    p.write_text(str(max(0, int(hours))) + "\n", encoding="utf-8")


def _cancel_auto_clean_jobs(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    try:
        if getattr(context, "job_queue", None):
            for j in context.job_queue.get_jobs_by_name(f"auto_clean_{chat_id}"):
                j.schedule_removal()
    except Exception:
        pass


def _schedule_auto_clean_if_configured(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    hours = load_auto_clean_hours(chat_id)
    _cancel_auto_clean_jobs(context, chat_id)
    if hours and hours > 0 and getattr(context, "job_queue", None):
        try:
            seconds = hours * 3600
            print(f"[DEBUG] Programando auto-clean cada {hours}h para chat {chat_id}")
            context.job_queue.run_repeating(
                auto_clean_job,
                interval=seconds,
                first=seconds,
                data={"chat_id": chat_id},
                name=f"auto_clean_{chat_id}",
            )
            SCHEDULED_AUTOCLEAN_CHATS.add(chat_id)
        except Exception as e:
            print(f"[DEBUG] No se pudo programar auto-clean: {e}")


def mention_html(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(name or "nuevo miembro")}</a>'


def chat_allowed(chat_id: int) -> bool:
    return (not ALLOWED_CHAT_IDS) or (chat_id in ALLOWED_CHAT_IDS)


def welcome_path_for_chat(chat_id: int) -> Path:
    return Path(__file__).with_name(f"welcome_{chat_id}.md")


def load_welcome_text(chat_id: int) -> str:
    p = welcome_path_for_chat(chat_id)
    if p.exists():
        try:
            return p.read_text(encoding="utf-8").strip() or DEFAULT_WELCOME
        except Exception:
            return DEFAULT_WELCOME
    return DEFAULT_WELCOME


def save_welcome_text(chat_id: int, text: str) -> None:
    p = welcome_path_for_chat(chat_id)
    p.write_text(text.strip() + "\n", encoding="utf-8")


def reset_welcome_text(chat_id: int) -> None:
    p = welcome_path_for_chat(chat_id)
    if p.exists():
        p.unlink()


def registration_path_for_chat(chat_id: int) -> Path:
    return Path(__file__).with_name(f"registration_{chat_id}.md")


def load_registration_text(chat_id: int) -> str:
    p = registration_path_for_chat(chat_id)
    if p.exists():
        try:
            return p.read_text(encoding="utf-8").strip() or DEFAULT_REGISTRATION
        except Exception:
            return DEFAULT_REGISTRATION
    return DEFAULT_REGISTRATION


def save_registration_text(chat_id: int, text: str) -> None:
    p = registration_path_for_chat(chat_id)
    p.write_text(text.strip() + "\n", encoding="utf-8")


def reset_registration_text(chat_id: int) -> None:
    p = registration_path_for_chat(chat_id)
    if p.exists():
        p.unlink()


def _cache_message(msg) -> None:
    try:
        chat_id = msg.chat_id
        thread_id = getattr(msg, "message_thread_id", None)
        key = (chat_id, thread_id)
        message_cache[key].append(msg.message_id)
        pinned = getattr(msg, "pinned_message", None)
        if pinned is not None and getattr(pinned, "message_id", None):
            pinned_by_chat[chat_id].add(pinned.message_id)
    except Exception:
        pass

def _record_bot_message(context: ContextTypes.DEFAULT_TYPE, sent_msg) -> None:
    try:
        chat_id = sent_msg.chat_id
        uid = context.bot.id if getattr(context, "bot", None) else None
        MESSAGE_CACHE[chat_id].append((sent_msg.message_id, uid, False, False))
    except Exception:
        pass


# === Configuración por chat: segundos de auto-borrado ===
def delete_seconds_path_for_chat(chat_id: int) -> Path:
    return Path(__file__).with_name(f"welcome_delete_{chat_id}.txt")


def load_delete_seconds_for_chat(chat_id: int) -> int:
    p = delete_seconds_path_for_chat(chat_id)
    if p.exists():
        try:
            val = int(p.read_text(encoding="utf-8").strip())
            return max(0, val)
        except Exception:
            return WELCOME_DELETE_SECONDS
    return WELCOME_DELETE_SECONDS


def save_delete_seconds_for_chat(chat_id: int, seconds: int) -> None:
    p = delete_seconds_path_for_chat(chat_id)
    p.write_text(str(max(0, int(seconds))) + "\n", encoding="utf-8")


def reset_delete_seconds_for_chat(chat_id: int) -> None:
    p = delete_seconds_path_for_chat(chat_id)
    if p.exists():
        p.unlink()


# === Persistencia de borrados programados (sobrevive reinicios) ===
def pending_deletes_path_for_chat(chat_id: int) -> Path:
    return Path(__file__).with_name(f"pending_deletes_{chat_id}.jsonl")


def _append_pending_delete(chat_id: int, record: dict) -> None:
    p = pending_deletes_path_for_chat(chat_id)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_pending_deletes(chat_id: int) -> List[dict]:
    p = pending_deletes_path_for_chat(chat_id)
    records: List[dict] = []
    if not p.exists():
        return records
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict):
                    records.append(rec)
            except Exception:
                continue
    except Exception:
        pass
    return records


def _remove_pending_delete(chat_id: int, message_id: int) -> None:
    p = pending_deletes_path_for_chat(chat_id)
    if not p.exists():
        return
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        new_lines: List[str] = []
        for line in lines:
            try:
                rec = json.loads(line)
                if isinstance(rec, dict) and rec.get("message_id") == message_id:
                    continue
            except Exception:
                pass
            new_lines.append(line)
        p.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
    except Exception:
        pass


def _schedule_delete_with_persistence(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    delay_seconds: int,
    thread_id: Optional[int] = None,
) -> None:
    delete_at = int(time.time()) + int(delay_seconds)
    rec = {
        "chat_id": chat_id,
        "message_id": message_id,
        "thread_id": thread_id,
        "delete_at": delete_at,
        "created_at": int(time.time()),
    }
    _append_pending_delete(chat_id, rec)

    def _queue_job():
        if getattr(context, "job_queue", None):
            context.job_queue.run_once(
                delete_welcome_job,
                when=delay_seconds,
                data={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "persist": True,
                },
                name=f"del_persist_{chat_id}_{message_id}",
            )
        else:
            async def _del_later(bot, cid, mid, delay):
                try:
                    await asyncio.sleep(delay)
                    await bot.delete_message(chat_id=cid, message_id=mid)
                except Exception:
                    pass
                finally:
                    _remove_pending_delete(cid, mid)
            if getattr(context, "application", None) and hasattr(context.application, "create_task"):
                context.application.create_task(_del_later(context.bot, chat_id, message_id, delay_seconds))
            else:
                asyncio.create_task(_del_later(context.bot, chat_id, message_id, delay_seconds))

    try:
        print(f"[DEBUG] [persist] Programando borrado en {delay_seconds}s para msg {message_id} en chat {chat_id}")
        _queue_job()
    except Exception as e:
        print(f"[DEBUG] [persist] No se pudo programar borrado: {e}")


async def is_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """
    Permite:
      - IDs en SUPER_ADMIN_IDS (override)
      - owner/creator
      - administrator
    Intenta primero get_chat_member (estado individual) y luego get_chat_administrators (lista).
    """
    print(f"[DEBUG] Verificando admin: user_id={user_id}, chat_id={chat_id}")
    print(f"[DEBUG] SUPER_ADMIN_IDS: {SUPER_ADMIN_IDS}")
    
    if user_id in SUPER_ADMIN_IDS:
        print(f"[DEBUG] Usuario {user_id} es super admin")
        return True
    
    # Método 1: get_chat_member
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        status = getattr(member, "status", "")
        print(f"[DEBUG] Status del usuario {user_id}: '{status}'")
        # Telegram usa 'creator' para el dueño del grupo
        if status in ("creator", "administrator"):
            print(f"[DEBUG] Usuario {user_id} es admin/creator por get_chat_member")
            return True
    except Exception as e:
        print(f"[DEBUG] Error en get_chat_member: {e}")
    
    # Método 2: get_chat_administrators (fallback)
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        print(f"[DEBUG] Lista de admins obtenida: {len(admins)} admins")
        for admin in admins:
            if admin.user.id == user_id:
                print(f"[DEBUG] Usuario {user_id} encontrado en lista de admins con status: {admin.status}")
                return True
    except Exception as e:
        print(f"[DEBUG] Error en get_chat_administrators: {e}")
    
    print(f"[DEBUG] Usuario {user_id} NO es admin")
    return False


def _thread_kwargs() -> dict:
    return {"message_thread_id": WELCOME_TOPIC_ID} if WELCOME_TOPIC_ID is not None else {}


async def send_welcome(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    mention_id: int,
    mention_name: str,
) -> None:
    text_template = load_welcome_text(chat_id)
    # Mostramos siempre la mención + el template (el template se escapa por seguridad)
    texto = f"{mention_html(mention_id, mention_name)}\n\n{escape(text_template)}"
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🌐 Ir a QvaClick", url="https://qvaclick.com")]]
    )
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=texto,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=kb,
        **_thread_kwargs(),
    )
    _record_bot_message(context, sent)

    # Programar borrado automático si está configurado
    seconds = load_delete_seconds_for_chat(chat_id)
    if seconds > 0:
        _schedule_delete_with_persistence(
            context,
            chat_id,
            sent.message_id,
            seconds,
            getattr(sent, "message_thread_id", None),
        )

async def delete_welcome_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data if hasattr(context, "job") and context.job else {}
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    thread_id = data.get("thread_id")
    if not chat_id or not message_id:
        return
    try:
        print(f"[DEBUG] Ejecutando borrado (job_queue) para msg {message_id} en chat {chat_id}")
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        print(f"[DEBUG] Borrado OK (job_queue) para msg {message_id} en chat {chat_id}")
    except Exception as e:
        # Puede fallar si ya fue borrado manualmente o faltan permisos
        print(f"[DEBUG] Error borrando bienvenida {chat_id}/{message_id}: {e}")
    finally:
        if data.get("persist"):
            _remove_pending_delete(chat_id, message_id)


async def send_registration_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> None:
    """
    Segundo mensaje tras la bienvenida: invita a registrarse con rol preseleccionado
    usando parámetros de URL (?qvc_role=freelancer|employer).
    """
    texto = load_registration_text(chat_id)
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👩‍💻 Soy Freelancer", url="https://www.qvaclick.com/register/?qvc_role=freelancer")],
            [InlineKeyboardButton("🏢 Soy Empleador", url="https://www.qvaclick.com/register/?qvc_role=employer")],
        ]
    )
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=texto,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=kb,
        **_thread_kwargs(),
    )
    _record_bot_message(context, sent)
    # Auto-borrado con la misma política del chat
    seconds = load_delete_seconds_for_chat(chat_id)
    if seconds > 0:
        try:
            if getattr(context, "job_queue", None):
                print(f"[DEBUG] Programando borrado (job_queue) en {seconds}s para REG msg {sent.message_id} en chat {chat_id}")
                context.job_queue.run_once(
                    delete_welcome_job,
                    when=seconds,
                    data={
                        "chat_id": chat_id,
                        "message_id": sent.message_id,
                        "thread_id": getattr(sent, "message_thread_id", None),
                    },
                    name=f"del_registration_{chat_id}_{sent.message_id}",
                )
            else:
                async def _del_later(bot, cid, mid, delay):
                    try:
                        await asyncio.sleep(delay)
                        print(f"[DEBUG] Ejecutando borrado (fallback) para REG msg {mid} en chat {cid}")
                        await bot.delete_message(chat_id=cid, message_id=mid)
                        print(f"[DEBUG] Borrado OK (fallback) para REG msg {mid} en chat {cid}")
                    except Exception as e:
                        print(f"[DEBUG] Error borrando (fallback REG) {cid}/{mid}: {e}")

                if getattr(context, "application", None) and hasattr(context.application, "create_task"):
                    print(f"[DEBUG] Programando borrado (asyncio) en {seconds}s para REG msg {sent.message_id} en chat {chat_id}")
                    context.application.create_task(_del_later(context.bot, chat_id, sent.message_id, seconds))
                else:
                    print(f"[DEBUG] Programando borrado (asyncio.create_task) en {seconds}s para REG msg {sent.message_id} en chat {chat_id}")
                    asyncio.create_task(_del_later(context.bot, chat_id, sent.message_id, seconds))
        except Exception as e:
            print(f"[DEBUG] No se pudo programar borrado de REG: {e}")


async def send_combined_welcome(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    mention_id: int,
    mention_name: str,
) -> None:
    """Envía un único mensaje combinando bienvenida + CTA de registro."""
    welcome_text = load_welcome_text(chat_id)
    registration_text = load_registration_text(chat_id)

    texto = (
        f"{mention_html(mention_id, mention_name)}\n\n"
        f"{escape(welcome_text)}\n\n"
        f"{registration_text}"
    )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌐 Ir a QvaClick", url="https://qvaclick.com")],
            [InlineKeyboardButton("👩‍💻 Soy Freelancer", url="https://www.qvaclick.com/register/?qvc_role=freelancer")],
            [InlineKeyboardButton("🏢 Soy Empleador", url="https://www.qvaclick.com/register/?qvc_role=employer")],
        ]
    )

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=texto,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=kb,
        **_thread_kwargs(),
    )
    _record_bot_message(context, sent)

    seconds = load_delete_seconds_for_chat(chat_id)
    if seconds > 0:
        _schedule_delete_with_persistence(
            context,
            chat_id,
            sent.message_id,
            seconds,
            getattr(sent, "message_thread_id", None),
        )


# === Comandos ===

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or not msg or (ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS):
        return
    help_text = (
        "🤖 <b>Comandos disponibles</b>\n"
        "/help — Ver esta ayuda\n"
        "/whoami — Ver tu user_id\n"
        "/debug_admin — Debug de permisos de admin\n"
        "/id — Mostrar chat_id y (si aplica) topic_id\n"
    "/test_welcome — Probar mensaje único combinado\n\n"
        "<b>📝 Mensajes de bienvenida:</b>\n"
        "/get_welcome — Ver texto de bienvenida actual\n"
        "/set_welcome — Cambiar bienvenida (admins/owner)\n"
        "/reset_welcome — Restaurar bienvenida por defecto\n\n"
        "<b>🧹 Auto-borrado de bienvenida:</b>\n"
        "/get_welcome_delete — Ver tiempo de auto-borrado\n"
        "/set_welcome_delete &lt;segundos|off&gt; — Cambiar auto-borrado en este chat\n"
        "/reset_welcome_delete — Volver al valor global (.env)\n\n"
        "<b>📋 Mensajes de registro:</b>\n"
        "/get_registration — Ver texto de registro actual\n"
        "/set_registration — Cambiar mensaje de registro (admins/owner)\n"
        "/reset_registration — Restaurar mensaje de registro por defecto\n\n"
        "/cancelar — Cancelar operación en curso\n\n"
        "<b>🧹 Limpieza del chat:</b>\n"
        "/clean_chat [N] — Borra los últimos N mensajes no fijados (admins)\n"
        "/set_auto_clean &lt;horas|off&gt; — Programa limpieza automática (admins)\n"
    )
    sent = await msg.reply_text(help_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    _record_bot_message(context, sent)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Devuelve tu user_id (útil para SUPER_ADMIN_IDS)."""
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or not msg:
        return
    u = msg.from_user
    await msg.reply_text(f"Tu user_id: {u.id}\nUsername: @{u.username or '-'}")


async def cmd_debug_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando de debug para verificar permisos de administrador."""
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or not msg:
        return
    
    user = msg.from_user
    if not user:
        return
        
    # Verificar si es admin
    is_admin_result = await is_admin(context, chat.id, user.id)
    
    # Información adicional
    info_text = (
        f"🔍 <b>Debug de permisos</b>\n"
        f"User ID: <code>{user.id}</code>\n"
        f"Username: @{user.username or 'N/A'}\n"
        f"Chat ID: <code>{chat.id}</code>\n"
        f"Chat Type: {chat.type}\n"
        f"Es admin: {'✅ SÍ' if is_admin_result else '❌ NO'}\n"
        f"En SUPER_ADMIN_IDS: {'✅ SÍ' if user.id in SUPER_ADMIN_IDS else '❌ NO'}\n\n"
        f"<i>Revisa los logs del bot para más detalles técnicos.</i>"
    )
    
    await msg.reply_text(info_text, parse_mode=ParseMode.HTML)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    if chat is None or msg is None or (ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS):
        return
    tid = getattr(msg, "message_thread_id", None)
    await msg.reply_text(f"chat_id: {chat.id}{', topic_id: ' + str(tid) if tid else ''}")


async def bienvenida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    print(f"[DEBUG] bienvenida() llamada - chat: {chat.id if chat else None}, msg: {msg.message_id if msg else None}")

    if chat is None or msg is None:
        print("[DEBUG] chat o msg es None, saliendo")
        return

    if ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS:
        print(f"[DEBUG] Chat {chat.id} no está en ALLOWED_CHAT_IDS: {ALLOWED_CHAT_IDS}")
        return

    _cache_message(msg)

    new_members = msg.new_chat_members or []
    print(f"[DEBUG] Nuevos miembros detectados: {len(new_members)}")
    for i, member in enumerate(new_members):
        print(
            f"[DEBUG] Miembro {i+1}: ID={member.id}, username={member.username}, "
            f"first_name={member.first_name}, is_bot={member.is_bot}"
        )

    try:
        # Borra el mensaje "X se unió" (requiere permiso de eliminar)
        try:
            await msg.delete()
            print("[DEBUG] Mensaje de unión borrado exitosamente")
        except Exception as e:
            print(f"[DEBUG] No se pudo borrar mensaje de unión: {e}")

        for m in new_members:
            if m.is_bot:
                print(f"[DEBUG] Saltando bot: {m.first_name} ({m.id})")
                continue
            nombre = " ".join(filter(None, [m.first_name, m.last_name])) or "nuevo miembro"
            print(f"[DEBUG] Enviando bienvenida a: {nombre} (ID: {m.id})")
            await send_combined_welcome(context, chat.id, m.id, nombre)
            print(f"[DEBUG] Bienvenida enviada exitosamente a {nombre}")

    except Exception as e:
        print(f"[ERROR] Error en bienvenida: {e}")
        import traceback
        traceback.print_exc()


async def test_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra un único mensaje combinando bienvenida + registro."""
    chat = update.effective_chat
    msg = update.effective_message
    if chat is None or msg is None or (ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS):
        return
    user = msg.from_user
    nombre = " ".join(filter(None, [user.first_name, user.last_name])) or "miembro"
    await send_combined_welcome(context, chat.id, user.id, nombre)

    # Se ha unificado la prueba en un único mensaje; test_registration/test_chat eliminados


async def get_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Devuelve el texto de bienvenida actual (sin la mención)."""
    chat = update.effective_chat
    msg = update.effective_message
    if chat is None or msg is None or (ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS):
        return

    text_template = load_welcome_text(chat.id)
    sent = await msg.reply_text(
        f"📝 Bienvenida actual (chat {chat.id}):\n\n{text_template}",
        disable_web_page_preview=True,
    )
    _record_bot_message(context, sent)


async def get_welcome_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el tiempo de auto-borrado efectivo y el global por defecto."""
    chat = update.effective_chat
    msg = update.effective_message
    if chat is None or msg is None or (ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS):
        return
    eff = load_delete_seconds_for_chat(chat.id)
    glob = WELCOME_DELETE_SECONDS
    await msg.reply_text(
        "⏱️ Auto-borrado de bienvenida\n"
        f"• Valor efectivo en este chat: {eff} s ({'desactivado' if eff == 0 else 'activo'})\n"
        f"• Valor global por .env: {glob} s\n\n"
        "Usa /set_welcome_delete <segundos|off> para cambiarlo en este chat,\n"
        "o /reset_welcome_delete para volver al valor global.",
    )


async def get_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Devuelve el texto de registro actual."""
    chat = update.effective_chat
    msg = update.effective_message
    if chat is None or msg is None or (ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS):
        return

    text_template = load_registration_text(chat.id)
    sent = await msg.reply_text(
        f"📝 Mensaje de registro actual (chat {chat.id}):\n\n{text_template}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    _record_bot_message(context, sent)


async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permite a un admin/owner (o SUPER_ADMIN_IDS) definir el texto de bienvenida (por grupo)."""
    chat = update.effective_chat
    msg = update.effective_message
    user = msg.from_user if msg else None
    if not chat or not msg or not user:
        return
    if ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS:
        return

    # Debe ejecutarse en el grupo (salvo superadmins)
    if chat.type == ChatType.PRIVATE and user.id not in SUPER_ADMIN_IDS:
        await msg.reply_text("ℹ️ Por favor ejecuta este comando dentro del grupo.")
        return

    if not await is_admin(context, chat.id, user.id):
        await msg.reply_text("🚫 Solo administradores/owner pueden cambiar la bienvenida.")
        return

    # Nuevo comportamiento: sistema de espera de mensaje
    chat_user_key = (chat.id, user.id)
    waiting_for_message[chat_user_key] = "waiting_for_welcome"
    
    sent = await msg.reply_text(
        "✏️ <b>Configuración de mensaje de bienvenida</b>\n\n"
        "📝 Por favor, envía ahora el mensaje que quieres usar como bienvenida.\n\n"
        "💡 <i>Puedes usar saltos de línea, emojis y formatear el texto como desees. "
        "El próximo mensaje que envíes será usado exactamente como lo escribas.</i>\n\n"
        "❌ Escribe /cancelar para cancelar esta operación.",
        parse_mode=ParseMode.HTML
    )
    _record_bot_message(context, sent)


async def set_welcome_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Define el tiempo de auto-borrado de bienvenida para ESTE chat."""
    chat = update.effective_chat
    msg = update.effective_message
    user = msg.from_user if msg else None
    if not chat or not msg or not user:
        return
    if ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS:
        return

    if chat.type == ChatType.PRIVATE and user.id not in SUPER_ADMIN_IDS:
        await msg.reply_text("ℹ️ Por favor ejecuta este comando dentro del grupo.")
        return

    if not await is_admin(context, chat.id, user.id):
        await msg.reply_text("🚫 Solo administradores/owner pueden cambiar este ajuste.")
        return

    args = context.args if hasattr(context, "args") else []
    if not args:
        await msg.reply_text("Uso: /set_welcome_delete <segundos|off>. Ej: /set_welcome_delete 180")
        return

    val_raw = args[0].strip().lower()
    if val_raw in ("off", "desactivar", "0"):
        seconds = 0
    else:
        try:
            seconds = int(val_raw)
            if seconds < 0:
                raise ValueError()
        except Exception:
            await msg.reply_text("❌ Valor inválido. Usa un entero ≥ 0 o 'off'.")
            return

    save_delete_seconds_for_chat(chat.id, seconds)
    sent = await msg.reply_text(
        f"✅ Auto-borrado actualizado para este chat: {seconds} s"
        + (" (desactivado)" if seconds == 0 else "")
    )
    _record_bot_message(context, sent)


async def set_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permite a un admin/owner (o SUPER_ADMIN_IDS) definir el texto de registro (por grupo)."""
    chat = update.effective_chat
    msg = update.effective_message
    user = msg.from_user if msg else None
    if not chat or not msg or not user:
        return
    if ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS:
        return

    # Debe ejecutarse en el grupo (salvo superadmins)
    if chat.type == ChatType.PRIVATE and user.id not in SUPER_ADMIN_IDS:
        await msg.reply_text("ℹ️ Por favor ejecuta este comando dentro del grupo.")
        return

    if not await is_admin(context, chat.id, user.id):
        await msg.reply_text("🚫 Solo administradores/owner pueden cambiar el mensaje de registro.")
        return

    # Nuevo comportamiento: sistema de espera de mensaje
    chat_user_key = (chat.id, user.id)
    waiting_for_message[chat_user_key] = "waiting_for_registration"
    
    sent = await msg.reply_text(
        "✏️ <b>Configuración de mensaje de registro</b>\n\n"
        "📝 Por favor, envía ahora el mensaje que quieres usar para invitar a registrarse.\n\n"
        "💡 <i>Puedes usar saltos de línea, emojis y formatear el texto como desees. "
        "El próximo mensaje que envíes será usado exactamente como lo escribas.</i>\n\n"
        "❌ Escribe /cancelar para cancelar esta operación.",
        parse_mode=ParseMode.HTML
    )
    _record_bot_message(context, sent)


async def reset_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restaura la bienvenida por defecto para el grupo."""
    chat = update.effective_chat
    msg = update.effective_message
    user = msg.from_user if msg else None
    if not chat or not msg or not user:
        return
    if ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS:
        return

    if chat.type == ChatType.PRIVATE and user.id not in SUPER_ADMIN_IDS:
        await msg.reply_text("ℹ️ Por favor ejecuta este comando dentro del grupo.")
        return

    if not await is_admin(context, chat.id, user.id):
        await msg.reply_text("🚫 Solo administradores/owner pueden resetear la bienvenida.")
        return

    reset_welcome_text(chat.id)
    sent = await msg.reply_text("↩️ Bienvenida restaurada a la versión por defecto.")
    _record_bot_message(context, sent)
    await test_welcome(update, context)


async def reset_welcome_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina el override de auto-borrado y vuelve al valor del .env."""
    chat = update.effective_chat
    msg = update.effective_message
    user = msg.from_user if msg else None
    if not chat or not msg or not user:
        return
    if ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS:
        return

    if chat.type == ChatType.PRIVATE and user.id not in SUPER_ADMIN_IDS:
        await msg.reply_text("ℹ️ Por favor ejecuta este comando dentro del grupo.")
        return

    if not await is_admin(context, chat.id, user.id):
        await msg.reply_text("🚫 Solo administradores/owner pueden resetear este ajuste.")
        return

    reset_delete_seconds_for_chat(chat.id)
    sent = await msg.reply_text(
        f"↩️ Auto-borrado restaurado al valor global: {WELCOME_DELETE_SECONDS} s"
        + (" (desactivado)" if WELCOME_DELETE_SECONDS == 0 else "")
    )
    _record_bot_message(context, sent)


async def cache_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda message_id de cada mensaje para permitir limpieza manual.
    Incluye mensajes de servicio (join/left), textos, multimedia, etc.
    """
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or not msg:
        return
    try:
        if getattr(msg, "message_id", None):
            uid = msg.from_user.id if getattr(msg, "from_user", None) else None
            # Consideramos comando si empieza con '/' en texto o caption
            raw = (msg.text or msg.caption or "").strip()
            is_cmd = raw.startswith("/") if raw else False
            is_service = bool(getattr(msg, "new_chat_members", None)) or \
                bool(getattr(msg, "left_chat_member", None)) or \
                bool(getattr(msg, "pinned_message", None)) or \
                bool(getattr(msg, "new_chat_title", None)) or \
                bool(getattr(msg, "new_chat_photo", None)) or \
                bool(getattr(msg, "delete_chat_photo", False)) or \
                bool(getattr(msg, "group_chat_created", False)) or \
                bool(getattr(msg, "supergroup_chat_created", False)) or \
                bool(getattr(msg, "channel_chat_created", False))
            MESSAGE_CACHE[chat.id].append((msg.message_id, uid, is_cmd, is_service))
            # Asegurar que el auto-clean esté programado si existe configuración
            if chat.id not in SCHEDULED_AUTOCLEAN_CHATS:
                _schedule_auto_clean_if_configured(context, chat.id)
    except Exception:
        pass


async def auto_clean_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data if hasattr(context, "job") and context.job else {}
    chat_id = data.get("chat_id")
    if not chat_id:
        return
    try:
        deleted, skipped_pinned, failed = await _perform_clean(chat_id, context, AUTO_CLEAN_DEFAULT_N)
        print(f"[DEBUG] Auto-clean chat {chat_id}: del={deleted}, skipped={skipped_pinned}, failed={failed}")
    except Exception as e:
        print(f"[DEBUG] Error en auto-clean de chat {chat_id}: {e}")


async def _perform_clean(chat_id: int, context: ContextTypes.DEFAULT_TYPE, n: int) -> Tuple[int, int, int]:
    # Obtener mensaje fijado actual
    pinned_id = None
    try:
        c = await context.bot.get_chat(chat_id)
        if getattr(c, "pinned_message", None):
            pinned_id = c.pinned_message.message_id
    except Exception:
        pinned_id = None

    deleted = 0
    skipped_pinned = 0
    failed = 0

    items = list(MESSAGE_CACHE.get(chat_id, []))
    bot_id = context.bot.id if getattr(context, "bot", None) else None
    for rec in reversed(items):
        # Compatibilidad: registros antiguos pueden no tener is_service
        if len(rec) == 3:
            mid, uid, is_cmd = rec  # type: ignore
            is_service = False
        else:
            mid, uid, is_cmd, is_service = rec  # type: ignore
        if deleted >= n:
            break
        if pinned_id and mid == pinned_id:
            skipped_pinned += 1
            continue
        # Determinar si el mensaje es del propio bot (siempre se puede borrar)
        is_bot_message = (bot_id is not None and uid == bot_id)
        # Saltar admins/superadmins salvo comandos o mensajes del bot
        if uid is not None and not is_cmd and not is_bot_message and not is_service:
            try:
                if uid in SUPER_ADMIN_IDS:
                    continue
                member = await context.bot.get_chat_member(chat_id, uid)
                status = getattr(member, "status", "")
                if status in ("creator", "administrator"):
                    continue
            except Exception:
                pass
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            deleted += 1
        except Exception:
            failed += 1
            continue
    return deleted, skipped_pinned, failed


async def clean_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina los últimos N mensajes no fijados en este chat. Uso: /clean_chat [N] (por defecto 200)."""
    chat = update.effective_chat
    msg = update.effective_message
    user = msg.from_user if msg else None
    if not chat or not msg or not user:
        return
    if ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS:
        return

    if chat.type == ChatType.PRIVATE and user.id not in SUPER_ADMIN_IDS:
        await msg.reply_text("ℹ️ Por favor ejecuta este comando dentro del grupo.")
        return

    if not await is_admin(context, chat.id, user.id):
        await msg.reply_text("🚫 Solo administradores/owner pueden limpiar el chat.")
        return

    # Cantidad a borrar
    n = 200
    args = context.args if hasattr(context, "args") else []
    if args:
        try:
            n = max(1, min(1000, int(args[0])))
        except Exception:
            await msg.reply_text("❌ Valor inválido. Usa un entero entre 1 y 1000. Ej: /clean_chat 200")
            return

    deleted, skipped_pinned, failed = await _perform_clean(chat.id, context, n)

    # Intentar borrar el mensaje que invoca el comando
    try:
        await context.bot.delete_message(chat_id=chat.id, message_id=msg.message_id)
    except Exception:
        pass

    # Enviar resumen y borrarlo a los 5s
    thread_kwargs = {"message_thread_id": getattr(msg, "message_thread_id", None)} if getattr(msg, "message_thread_id", None) else {}
    summary = await context.bot.send_message(
        chat_id=chat.id,
        text=(
            f"🧹 Limpieza completada. Eliminados: {deleted}. "
            f"Skipped fijados: {skipped_pinned}. Fallidos: {failed}."
        ),
        disable_web_page_preview=True,
        **thread_kwargs,
    )

    try:
        if getattr(context, "job_queue", None):
            context.job_queue.run_once(
                delete_welcome_job,
                when=5,
                data={"chat_id": chat.id, "message_id": summary.message_id},
                name=f"del_clean_summary_{chat.id}_{summary.message_id}",
            )
        else:
            async def _del_summary(bot, cid, mid):
                try:
                    await asyncio.sleep(5)
                    await bot.delete_message(chat_id=cid, message_id=mid)
                except Exception:
                    pass
            if getattr(context, "application", None) and hasattr(context.application, "create_task"):
                context.application.create_task(_del_summary(context.bot, chat.id, summary.message_id))
            else:
                asyncio.create_task(_del_summary(context.bot, chat.id, summary.message_id))
    except Exception:
        pass


async def reset_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restaura el mensaje de registro por defecto para el grupo."""
    chat = update.effective_chat
    msg = update.effective_message
    user = msg.from_user if msg else None
    if not chat or not msg or not user:
        return
    if ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS:
        return

    if chat.type == ChatType.PRIVATE and user.id not in SUPER_ADMIN_IDS:
        await msg.reply_text("ℹ️ Por favor ejecuta este comando dentro del grupo.")
        return

    if not await is_admin(context, chat.id, user.id):
        await msg.reply_text("🚫 Solo administradores/owner pueden resetear el mensaje de registro.")
        return

    reset_registration_text(chat.id)
    sent = await msg.reply_text("↩️ Mensaje de registro restaurado a la versión por defecto.")
    _record_bot_message(context, sent)
    await test_welcome(update, context)


async def set_auto_clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Programa limpieza automática del chat cada X horas (0=off). Uso: /set_auto_clean <horas|off>."""
    chat = update.effective_chat
    msg = update.effective_message
    user = msg.from_user if msg else None
    if not chat or not msg or not user:
        return
    if ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS:
        return

    if chat.type == ChatType.PRIVATE and user.id not in SUPER_ADMIN_IDS:
        await msg.reply_text("ℹ️ Por favor ejecuta este comando dentro del grupo.")
        return

    if not await is_admin(context, chat.id, user.id):
        await msg.reply_text("🚫 Solo administradores/owner pueden programar limpieza automática.")
        return

    args = context.args if hasattr(context, "args") else []
    if not args:
        await msg.reply_text("Uso: /set_auto_clean <horas|off>. Ej: /set_auto_clean 12")
        return

    raw = args[0].strip().lower()
    if raw in ("off", "desactivar", "0"):
        hours = 0
    else:
        try:
            hours = int(raw)
            if hours < 0:
                raise ValueError()
        except Exception:
            await msg.reply_text("❌ Valor inválido. Usa un entero ≥ 0 u 'off'.")
            return

    save_auto_clean_hours(chat.id, hours)
    # Reprogramar en JobQueue si corresponde
    _schedule_auto_clean_if_configured(context, chat.id)
    await msg.reply_text(
        f"✅ Auto-clean {'desactivado' if hours == 0 else f'programado cada {hours} h'}."
    )


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela cualquier operación en curso."""
    chat = update.effective_chat
    msg = update.effective_message
    user = msg.from_user if msg else None
    if not chat or not msg or not user:
        return
    
    chat_user_key = (chat.id, user.id)
    if chat_user_key in waiting_for_message:
        del waiting_for_message[chat_user_key]
        sent = await msg.reply_text("❌ Operación cancelada.")
        _record_bot_message(context, sent)
    else:
        sent = await msg.reply_text("ℹ️ No hay ninguna operación en curso para cancelar.")
        _record_bot_message(context, sent)


async def handle_waiting_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa mensajes cuando el usuario está esperando enviar contenido."""
    chat = update.effective_chat
    msg = update.effective_message
    user = msg.from_user if msg else None
    if not chat or not msg or not user:
        return
    
    chat_user_key = (chat.id, user.id)
    
    # Verificar si el usuario está esperando enviar algo
    if chat_user_key not in waiting_for_message:
        return  # No hacer nada, dejar que otros handlers procesen el mensaje
    
    # Obtener el tipo de espera
    waiting_type = waiting_for_message[chat_user_key]
    
    if waiting_type == "waiting_for_welcome":
        # Verificar permisos nuevamente
        if not await is_admin(context, chat.id, user.id):
            del waiting_for_message[chat_user_key]
            sent = await msg.reply_text("🚫 Solo administradores/owner pueden cambiar la bienvenida.")
            _record_bot_message(context, sent)
            return
        
        # Obtener el texto del mensaje
        new_text = msg.text or msg.caption or ""
        if not new_text.strip():
            sent = await msg.reply_text("❌ El mensaje no puede estar vacío. Envía un mensaje con texto o usa /cancelar.")
            _record_bot_message(context, sent)
            return
        
        # Guardar el nuevo texto de bienvenida
        save_welcome_text(chat.id, new_text.strip())
        
        # Limpiar el estado de espera
        del waiting_for_message[chat_user_key]
        
        # Confirmar y mostrar vista previa
        sent = await msg.reply_text("✅ ¡Mensaje de bienvenida actualizado correctamente!")
        _record_bot_message(context, sent)
        await test_welcome(update, context)
    
    elif waiting_type == "waiting_for_registration":
        # Verificar permisos nuevamente
        if not await is_admin(context, chat.id, user.id):
            del waiting_for_message[chat_user_key]
            sent = await msg.reply_text("🚫 Solo administradores/owner pueden cambiar el mensaje de registro.")
            _record_bot_message(context, sent)
            return
        
        # Obtener el texto del mensaje
        new_text = msg.text or msg.caption or ""
        if not new_text.strip():
            sent = await msg.reply_text("❌ El mensaje no puede estar vacío. Envía un mensaje con texto o usa /cancelar.")
            _record_bot_message(context, sent)
            return
        
        # Guardar el nuevo texto de registro
        save_registration_text(chat.id, new_text.strip())
        
        # Limpiar el estado de espera
        del waiting_for_message[chat_user_key]
        
        # Confirmar y mostrar vista previa
        sent = await msg.reply_text("✅ ¡Mensaje de registro actualizado correctamente!")
        _record_bot_message(context, sent)
        await test_welcome(update, context)


# === Arranque ===

COMMANDS: List[BotCommand] = [
    BotCommand("help", "Ver ayuda y lista de comandos"),
    BotCommand("whoami", "Ver tu user_id"),
    BotCommand("debug_admin", "Debug de permisos de admin"),
    BotCommand("id", "Mostrar chat_id y (si aplica) topic_id"),
    BotCommand("test_welcome", "Probar mensaje único combinado"),
    BotCommand("get_welcome", "Ver texto de bienvenida actual"),
    BotCommand("set_welcome", "Cambiar bienvenida (admins/owner)"),
    BotCommand("reset_welcome", "Restaurar bienvenida por defecto (admins/owner)"),
    BotCommand("get_registration", "Ver texto de registro actual"),
    BotCommand("set_registration", "Cambiar mensaje de registro (admins/owner)"),
    BotCommand("reset_registration", "Restaurar mensaje de registro por defecto (admins/owner)"),
    BotCommand("cancelar", "Cancelar operación en curso"),
    BotCommand("get_welcome_delete", "Ver el tiempo de auto-borrado actual"),
    BotCommand("set_welcome_delete", "Cambiar auto-borrado de bienvenida (admins/owner)"),
    BotCommand("reset_welcome_delete", "Volver al auto-borrado global (.env)"),
    BotCommand("clean_chat", "Eliminar últimos N mensajes no fijados (admins)"),
    BotCommand("set_auto_clean", "Programar limpieza automática por horas (admins)"),
]

async def post_init(app: Application):
    # Publica la lista para que Telegram muestre los comandos al escribir '/'
    try:
        await app.bot.set_my_commands(COMMANDS)
    except Exception as e:
        print("No se pudo publicar setMyCommands:", e)
    # Reprogramar borrados pendientes por chat (si existen archivos)
    try:
        base = Path(__file__).parent
        for p in base.glob("pending_deletes_*.jsonl"):
            try:
                chat_id = int(p.stem.split("_")[-1])
            except Exception:
                continue
            records = _load_pending_deletes(chat_id)
            now = int(time.time())
            if not records:
                continue
            print(f"[DEBUG] [persist] Reprogramando {len(records)} borrados pendientes para chat {chat_id}")
            for rec in records:
                try:
                    mid = int(rec.get("message_id"))
                    delete_at = int(rec.get("delete_at"))
                    thread_id = rec.get("thread_id")
                    delay = max(0, delete_at - now)
                    _schedule_delete_with_persistence(app, chat_id, mid, delay, thread_id)
                except Exception:
                    continue
    except Exception as e:
        print(f"[DEBUG] [persist] Error reprogramando pendientes: {e}")


def main():
    if not BOT_TOKEN:
        raise SystemExit("Falta BOT_TOKEN en .env")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Comandos
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("debug_admin", cmd_debug_admin))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("test_welcome", test_welcome))
    app.add_handler(CommandHandler("get_welcome", get_welcome))
    app.add_handler(CommandHandler("set_welcome", set_welcome))
    app.add_handler(CommandHandler("reset_welcome", reset_welcome))
    app.add_handler(CommandHandler("get_registration", get_registration))
    app.add_handler(CommandHandler("set_registration", set_registration))
    app.add_handler(CommandHandler("reset_registration", reset_registration))
    app.add_handler(CommandHandler("cancelar", cmd_cancelar))
    app.add_handler(CommandHandler("get_welcome_delete", get_welcome_delete))
    app.add_handler(CommandHandler("set_welcome_delete", set_welcome_delete))
    app.add_handler(CommandHandler("reset_welcome_delete", reset_welcome_delete))
    app.add_handler(CommandHandler("clean_chat", clean_chat))
    app.add_handler(CommandHandler("set_auto_clean", set_auto_clean))

    # Evento: nuevos miembros (prioritario) – registrar antes del catch-all
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida), group=0)

    # Handler para mensajes en espera (solo texto/caption)
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_waiting_messages), group=1)

    # Cache de mensajes para limpieza (último)
    app.add_handler(MessageHandler(filters.ALL, cache_message), group=2)

    # Long Polling (no requiere puertos abiertos)
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
