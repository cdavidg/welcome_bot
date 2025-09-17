#!/bin/bash

# Script de gestión para QvaClick Welcome Bot
# Evita conflictos de instancias múltiples

BOT_NAME="qvc-welcome"
SERVICE_NAME="qvc-welcome.service"
BOT_DIR="/opt/qvaclick/bots/qvc-welcome-bot"

case "$1" in
    start)
        echo "🚀 Iniciando $BOT_NAME..."
        # Asegurar que no hay procesos manuales corriendo
        pkill -f "python.*main.py" 2>/dev/null || true
        sleep 2
        sudo systemctl start $SERVICE_NAME
        echo "✅ Bot iniciado"
        ;;
    stop)
        echo "🛑 Deteniendo $BOT_NAME..."
        sudo systemctl stop $SERVICE_NAME
        # Asegurar que todos los procesos se detengan
        sleep 3
        pkill -f "python.*main.py" 2>/dev/null || true
        echo "✅ Bot detenido"
        ;;
    restart)
        echo "🔄 Reiniciando $BOT_NAME..."
        # Detener limpiamente
        sudo systemctl stop $SERVICE_NAME
        sleep 3
        # Matar cualquier proceso restante
        pkill -f "python.*main.py" 2>/dev/null || true
        sleep 2
        # Iniciar nuevamente
        sudo systemctl start $SERVICE_NAME
        echo "✅ Bot reiniciado"
        ;;
    reload)
        echo "⚙️ Recargando configuración de $BOT_NAME..."
        # Recargar daemon y reiniciar
        sudo systemctl daemon-reload
        $0 restart
        ;;
    status)
        echo "📊 Estado de $BOT_NAME:"
        sudo systemctl status $SERVICE_NAME --no-pager
        echo ""
        echo "🔍 Procesos del bot:"
        ps aux | grep -E "(main.py|python.*qvc)" | grep -v grep || echo "No hay procesos manuales corriendo"
        ;;
    logs)
        echo "📋 Logs de $BOT_NAME:"
        sudo journalctl -u $SERVICE_NAME -f --no-pager
        ;;
    *)
        echo "🤖 Gestor del QvaClick Welcome Bot"
        echo ""
        echo "Uso: $0 {start|stop|restart|reload|status|logs}"
        echo ""
        echo "Comandos:"
        echo "  start    - Iniciar el bot"
        echo "  stop     - Detener el bot"
        echo "  restart  - Reiniciar el bot (recomendado después de cambios)"
        echo "  reload   - Recargar configuración y reiniciar"
        echo "  status   - Ver estado del bot"
        echo "  logs     - Ver logs en tiempo real"
        echo ""
        echo "Ejemplos:"
        echo "  $0 restart  # Después de cambiar .env"
        echo "  $0 status   # Verificar que está funcionando"
        echo "  $0 logs     # Ver logs en tiempo real"
        exit 1
        ;;
esac
