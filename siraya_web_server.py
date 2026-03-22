"""
Siraya Web Server - Flask Production Server
============================================

Avvia un server Flask per esporre il webhook WhatsApp.
Completamente separato dall'app Streamlit (siraya/app.py).

Uso:
    gunicorn -w 4 -b 0.0.0.0:5000 siraya_web_server:app

Oppure per test locale:
    python siraya_web_server.py
"""

import os
import sys
import logging
from pathlib import Path
from flask import Flask, jsonify
from flask_cors import CORS

# ═══════════════════════════════════════════════════════════
# Setup logging
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Configura sys.path per importare siraya
# ═══════════════════════════════════════════════════════════

_root_dir = Path(__file__).parent.absolute()
if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))

logger.info(f"📁 Project root: {_root_dir}")
logger.info(f"🐍 Python path: {sys.path[0]}")

# ═══════════════════════════════════════════════════════════
# Crea app Flask
# ═══════════════════════════════════════════════════════════

app = Flask(__name__)
CORS(app)  # Abilita CORS per test

# ═══════════════════════════════════════════════════════════
# Registra blueprints (webhook routes)
# ═══════════════════════════════════════════════════════════

try:
    from siraya.webhooks.whatsapp_webhook import whatsapp_bp
    app.register_blueprint(whatsapp_bp)
    logger.info("✅ Registrato WhatsApp webhook blueprint")
except ImportError as e:
    logger.error(f"❌ Errore caricamento webhook: {e}")
    raise

# ═══════════════════════════════════════════════════════════
# Root endpoint
# ═══════════════════════════════════════════════════════════

@app.route('/', methods=['GET'])
def index():
    """Root endpoint."""
    return jsonify({
        "service": "SIRAYA Web Server",
        "version": "1.0",
        "endpoints": {
            "whatsapp_webhook": "POST /whatsapp/message",
            "whatsapp_health": "GET /whatsapp/health",
            "health": "GET /health"
        }
    })


@app.route('/health', methods=['GET'])
def health():
    """Global health check."""
    return jsonify({
        "status": "running",
        "service": "SIRAYA Web Server"
    }), 200


# ═══════════════════════════════════════════════════════════
# Error handlers
# ═══════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    logger.error(f"500 Internal Server Error: {e}")
    return jsonify({"error": "Internal server error"}), 500


# ═══════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'
    
    logger.info(f"🚀 Avvio SIRAYA Web Server su porta {port} (debug={debug})")
    app.run(host='0.0.0.0', port=port, debug=debug)
