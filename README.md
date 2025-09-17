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
