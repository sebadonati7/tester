import logging
import os
import mimetypes
import json
import math
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import requests


# Load environment variables
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=True)
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
TEMP_DIR = PROJECT_ROOT / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

CONFIDENCE_THRESHOLD = 0.90 # in percentuale
MAX_DISTANCE_THRESHOLD = 300 # in km

TECH_ERROR = "Ripeti"
CONF_ERROR = "Ripeti"

# Initialize logger
logger = logging.getLogger("whatsapp-bot")

from neural_network.severity_classifier import SeverityClassifier

neural_network = None

def get_model():
    """Carica il modello solo la prima volta che viene richiesto (Singleton)"""
    global neural_network
    if neural_network is None:
        logger.info("Inizializzazione della rete neurale in corso...")
        neural_network = SeverityClassifier(str(PROJECT_ROOT / "neural_network" / "severity_classifier_v1.pth"))
    return neural_network

"""
3 tipi di dati:
testo -> non succede nulla viene ritornato il testo così come è e siamo apposto

immagine -> l'immagine viene scaricata e inviata alla rete neurale che la classifica, 
se l'accuratezza è superiore al 90% allora viene considerata buona altrimenti viene scartata e vengono fatte ulteriori domande all'utente

posizione -> in forma di latitudine e longitudine viene calcolata la distanza in linea d'aria 
e selezionata la città/struttura più vicina (ancora in via di sviluppo)

"""


def process_data_from_message(data_type: str, data) -> dict[str, str]:
    """
    Trasforma i dati strutturati in testo strutturato per il controller, funge da dispatcher per il trattamento che questi devono esguire
    - data_type: può essere 'text', 'image' o 'location'.
    - data: stringa per il testo, dict per image/location.
    """
    if data_type == 'text':
        text = data
    elif data_type == 'image':
        """
        Il campo 'data' è strutturato così
        {
            "mime_type": "image/jpeg",
            "sha256": "TwOTyq5hwIGF6s8YpzBmANWlIyEiwDZ4Ogn7sPr3jis=",
            "id": "1743413047032496",
            "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1743413047032496&source=webhook&ext=1777640108&hash=ARk31EpLz4yq2HNXRaxhtTjqp90b5oUA_-oLtxaqDxUKbg"
        }
        """
        text = evaluate_image(data)
    elif data_type == 'location':
        """
        Il campo 'data' è strutturato così
        {
            "latitude": 44.4034227,
            "longitude": 12.1088064
        }
        """
        text = evaluate_location(data['latitude'], data['longitude'])
    logger.info("Testo prodotto: " + text)
    return text




def evaluate_image(image_data: dict) -> str:
    """
    Scarica l'immagine WhatsApp, esegue la classificazione e ritorna testo solo
    se la confidenza è >= 90%.
    """
    mime_type = image_data.get("mime_type")
    media_id = image_data.get("id")
    media_url = image_data.get("url")

    if not mime_type or not media_id or not media_url:
        logger.warning("Dati immagine incompleti: %s", image_data)
        return 

    image_path: Optional[Path] = None
    try:
        image_path = _download_whatsapp_image(media_url=media_url, mime_type=mime_type, media_id=media_id)
        if not image_path:
            return TECH_ERROR

        model = get_model()
        result = model.evaluate_severity(str(image_path))

        if result.get("status") != "success":
            logger.error("Errore classificazione immagine: %s", result)
            return TECH_ERROR

        confidence = float(result.get("confidence", 0.0))
        if confidence < CONFIDENCE_THRESHOLD:
            return CONF_ERROR

        prediction = str(result.get("prediction", "")).lower()
        if "grave" in prediction:
            return "È una ferita alla pelle grave"
        return "È una ferita alla pelle lieve"

    except Exception as exc:
        logger.exception("Errore in evaluate_image: %s", exc)
        return TECH_ERROR
    finally:
        if image_path and image_path.exists():
            try:
                image_path.unlink()
            except Exception:
                logger.warning("Impossibile eliminare il file temporaneo: %s", image_path)

def evaluate_location(latitude: float, longitude: float) -> str:
    """
    Calcola la distanza in linea d'aria verso tutti i comuni della regione Emilia-Romagna
    e ritorna il nome del comune più vicino.
    Se il comune più vicino è più di 300km di distanza, ritorna "Lontano".
    """
    try:
        mappa_path = PROJECT_ROOT / "mappa_er.json"
        with open(mappa_path, 'r', encoding='utf-8') as f:
            mappa_data = json.load(f)
        
        comuni = mappa_data.get("objects", {}).get("comuni", {}).get("geometries", [])
        
        if not comuni:
            logger.warning("Nessun comune trovato nella mappa")
            return "Lontano"
        
        min_distance = float('inf')
        closest_comune = None
        
        for geometria in comuni:
            props = geometria.get("properties", {})
            comune_lat = props.get("lat")
            comune_lon = props.get("lon")
            comune_name = props.get("name")
            
            if comune_lat is None or comune_lon is None or not comune_name:
                continue
            
            distance_km = _haversine_distance(latitude, longitude, comune_lat, comune_lon)
            
            if distance_km < min_distance:
                min_distance = distance_km
                closest_comune = comune_name
        
        if closest_comune is None:
            return "Lontano"
        
        logger.info("Comune più vicino: %s (distanza: %.2f km)", closest_comune, min_distance)
        
        if min_distance > MAX_DISTANCE_THRESHOLD:
            return "Lontano"
        
        return closest_comune
        
    except Exception as exc:
        logger.exception("Errore in evaluate_location: %s", exc)
        return "Lontano"


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calcola la distanza in linea d'aria (km) tra due coordinate geografiche
    usando la formula dell'haversine.
    """
    R = 6371  # Raggio della Terra in km
    
    # Conversione da gradi a radianti
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    
    # Differenze
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    
    # Formula haversine
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    distance = R * c
    
    return distance


def _download_whatsapp_image(media_url: str, mime_type: str, media_id: str) -> Optional[Path]:
    """
    Scarica l'immagine dall'URL di Meta e la salva nella cartella temp.
    Ritorna il percorso (Path) del file salvato, o None se fallisce.
    """
    if not WHATSAPP_TOKEN:
        logger.error("WHATSAPP_TOKEN mancante per il download.")
        return None

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}"
    }

    try:
        # Usa stream=True per non caricare file enormi tutti in RAM
        response = requests.get(media_url, headers=headers, stream=True, timeout=20)
        response.raise_for_status()

        # Ricava l'estensione corretta (es. "image/jpeg" -> ".jpg")
        ext = mimetypes.guess_extension(mime_type) or ".jpg"
        file_name = f"{media_id}{ext}"
        file_path = TEMP_DIR / file_name

        # Salva il file
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        logger.info("Immagine %s salvata con successo in %s", media_id, file_path)
        return file_path

    except requests.exceptions.RequestException as e:
        logger.error("Errore durante il download dell'immagine: %s", e)
        return None

