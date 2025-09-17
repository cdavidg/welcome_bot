# QvaClick Telegram Welcome Bot

Bot de bienvenida para grupos de QvaClick. Este README documenta en detalle las características, comandos disponibles, configuración por chat, funcionamiento interno y recomendaciones de despliegue.

Resumen
-------
El bot detecta nuevos miembros en grupos y envía mensajes de bienvenida y/o un CTA (llamada a registrarse). Incluye:

- Mensaje combinado de bienvenida + registro (mejor apariencia en móviles)
- Programación de borrado automático de mensajes de bienvenida con persistencia en disco para tolerancia a reinicios
- Caché y herramientas para limpiar mensajes y manejar operaciones largas desde el chat (set_welcome, set_registration)
- Comandos administrativos y de gestión (ver/establecer/resetear textos, configurar auto-borrado y auto-clean)

Instalación
-----------
1. Clona el repo y entra al directorio:

```bash
git clone git@github.com:cdavidg/welcome_bot.git
cd welcome_bot
```

2. Crea e instala dependencias en un virtualenv:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

3. Configura variables de entorno (archivo `.env` en el mismo directorio):

- `BOT_TOKEN`: token del bot (obtenido en BotFather)
- `ALLOWED_CHAT_IDS`: (opcional) lista separada por comas de chat_id permitidos
- `WELCOME_TOPIC_ID`: (opcional) si se usa topics, id del topic donde publicar
- `SUPER_ADMIN_IDS`: (opcional) ids de usuarios con permisos globales
- `WELCOME_DELETE_SECONDS`: (opcional) valor global por defecto de TTL para borrado de bienvenida (0 desactiva)

Estructura de archivos por chat
------------------------------
El bot usa archivos por chat para personalizar comportamiento y texto:

- `welcome_<chat_id>.md` — Texto de bienvenida. Si no existe se usa `DEFAULT_WELCOME`.
- `registration_<chat_id>.md` — Texto de registro/CTA. Si no existe se usa `DEFAULT_REGISTRATION`.
- `welcome_delete_<chat_id>.txt` — TTL en segundos para el borrado automático en ese chat (si existe).
- `auto_clean_<chat_id>.txt` — Número de horas tras el cual se ejecuta la limpieza automática del chat.
- `pending_deletes_<chat_id>.jsonl` — Archivo interno que persiste las tareas de borrado programadas (no agregar al repo).

Comandos disponibles (completos)
-------------------------------
Los comandos que implementa el bot están pensados para administradores y para pruebas. A continuación la lista completa con su propósito y comportamiento esperado.

- `/help` — Muestra la ayuda y lista de comandos.
- `/whoami` — Devuelve tu `user_id` y username (útil para añadir a `SUPER_ADMIN_IDS`).
- `/debug_admin` — Ejecuta comprobaciones detalladas para determinar si un usuario es admin en el chat (útil para debug).
- `/id` — Muestra `chat_id` y `topic_id` (si aplica) del contexto donde se ejecuta.
- `/test_welcome` — Envía una vista previa del mensaje combinado (bienvenida + CTA) al chat desde el autor del comando.

Gestión de bienvenida
- `/get_welcome` — Muestra el texto de bienvenida actual para este chat.
- `/set_welcome` — Inicia un flujo de espera donde el siguiente mensaje enviado por el admin se guarda como la nueva bienvenida.
- `/reset_welcome` — Restaura el texto de bienvenida al valor por defecto.

Auto-borrado de bienvenida
- `/get_welcome_delete` — Muestra el tiempo de auto-borrado efectivo en este chat y el valor global.
- `/set_welcome_delete <segundos|off>` — Establece el TTL en segundos para este chat; `off` o `0` desactiva el borrado.
- `/reset_welcome_delete` — Vuelve al valor global (`WELCOME_DELETE_SECONDS` de `.env`).

Mensajes de registro
- `/get_registration` — Muestra el texto de registro/CTA actual para este chat.
- `/set_registration` — Igual que `set_welcome` pero para el mensaje de registro; el siguiente mensaje enviado se guarda.
- `/reset_registration` — Restaura el mensaje de registro por defecto.

Operaciones de limpieza y mantenimiento
- `/cancelar` — Cancela una operación en curso (por ejemplo durante `set_welcome`).
- `/clean_chat [N]` — Borra los últimos `N` mensajes no fijados en el chat (solo admins). Si no se proporciona `N`, usa un valor por defecto razonable.
- `/set_auto_clean <horas|off>` — Programa limpieza automática periódica en el chat; guarda el valor en `auto_clean_<chat_id>.txt`.

Flujo y comportamiento interno
-----------------------------
- Detección de nuevos miembros: el handler `bienvenida` se ejecuta en el grupo y recorre `message.new_chat_members`.
- Combinación de mensaje: el bot usa `send_combined_welcome` para construir un único mensaje con la mención del usuario, el texto de bienvenida y el texto de registro; incluye botones con enlaces.
- Registro de mensajes del bot: cada mensaje enviado por el bot se registra en `MESSAGE_CACHE` y mediante `_record_bot_message` para permitir limpieza posterior.
- Programación de borrados: cuando un mensaje debe autodestruirse, se llama a `_schedule_delete_with_persistence` que:
  - escribe un registro JSONL en `pending_deletes_<chat_id>.jsonl` con `message_id`, `delete_at` y `thread_id` si aplica;
  - programa un `JobQueue` para ejecutar `delete_welcome_job` a la hora adecuada;
  - al ejecutar el job, `delete_welcome_job` borra el mensaje y llama a `_remove_pending_delete` para limpiar el registro persistente.
- Rehidratación al arranque: en `post_init(app)` el bot carga los `pending_deletes` desde disco y reprograma los jobs con el delay restante.

Permisos y administración
-------------------------
- `SUPER_ADMIN_IDS` en `.env` permite forzar permisos a ciertos usuarios cuya ID se considera admin globalmente.
- El método `is_admin` comprueba primero `get_chat_member` y, si falla, cae en `get_chat_administrators` para verificar rol. Está diseñado para tolerar errores de red y excepciones de la API.

Recomendaciones de seguridad
---------------------------
- No subir `.env` ni tokens al repositorio. Usa variables de entorno o herramientas de secrets de systemd/containers.
- Limita `ALLOWED_CHAT_IDS` si el bot solo debe actuar en ciertos grupos.

Despliegue (ejemplo systemd)
----------------------------
Archivo de ejemplo `qvc-welcome.service`:

```ini
[Unit]
Description=QvaClick Welcome Bot
After=network.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/opt/qvaclick/bots/qvc-welcome-bot
Environment="BOT_TOKEN=..."
ExecStart=/opt/qvaclick/bots/qvc-welcome-bot/.venv/bin/python main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Desarrolladores
---------------
- Formato: `black` + `isort` + `flake8` (pre-commit configurado).
- VSCode: la carpeta `.vscode` incluye settings recomendadas y una tarea para ejecutar `tools/git_autocommit.sh`.

FAQ y notas
-----------
- ¿Por qué persisto borrados en disco? Porque `JobQueue` es en memoria. Si el proceso reinicia, los jobs en memoria se pierden; con persistencia reprogramamos las tareas pendientes en el arranque.
- ¿Cómo establecer una foto de perfil al bot? Usa BotFather y el comando `/setuserpic`.

Contacto
-------
Para soporte operativo y despliegue, contacta al equipo de infra o al repositorio en GitHub.
# QvaClick Telegram Welcome Bot

Bot de bienvenida para grupos de QvaClick.

Descripción
-----------

Este bot envía mensajes de bienvenida y llamadas a la acción (CTA) cuando nuevos miembros se unen a un grupo de Telegram. Además programa un borrado automático de los mensajes de bienvenida y garantiza que los borrados se reprogramen después de reinicios mediante una persistencia en disco.

Características principales
--------------------------
- Envío de mensaje combinado (bienvenida + CTA) para nuevos miembros.
- Programación de borrado automático de mensajes con persistencia en `pending_deletes_<chat_id>.jsonl`.
- Caché temporal para mensajes en espera y limpieza manual/automática.
- Comandos de prueba (`/test_welcome`) y manejo de permisos de administradores.

Archivos de configuración por chat
---------------------------------
- `welcome_<chat_id>.md`: Texto de bienvenida (puede contener markdown compatible con Telegram).
- `registration_<chat_id>.md`: Texto para el mensaje de registro/CTA separado si se desea.
- `auto_clean_<chat_id>.txt`: Número de horas para limpieza automática por chat (opcional).
- `welcome_delete_<chat_id>.txt`: TTL en segundos para borrar el mensaje de bienvenida (opcional).

Comandos disponibles
--------------------
- `/test_welcome` — Envía una vista previa del mensaje combinado (útil para probar formato y botones).
- `/help` — Muestra ayuda básica y los comandos disponibles.

Detalles técnicos
-----------------
- El bot está construido con `python-telegram-bot` y usa `JobQueue` para programar borrados.
- Para evitar pérdida de jobs al reiniciar, los borrados se persisten en archivos JSONL `pending_deletes_<chat_id>.jsonl` y se reprograman en `post_init` al arrancar.
- Archivos sensibles (tokens) deben proporcionarse vía variables de entorno o `systemd` secrets; no almacenar en el repo.

Despliegue
---------
1. Crear un virtualenv e instalar dependencias:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

2. Configurar variables de entorno (TOKEN, etc.) y `systemd` service si se usa.

3. Iniciar el bot con el script `bot-manager.sh` o mediante `systemd`.

Contribuir
----------
- Usar `pre-commit` para formateo y linting: `pip install pre-commit && pre-commit install`.
- Usar la tarea de VS Code "Auto Commit and Push" para preparar y subir cambios.
# QvaClick Telegram Welcome Bot

Bot de bienvenida para grupos de QvaClick.

Instrucciones de despliegue y configuración en el repo.
