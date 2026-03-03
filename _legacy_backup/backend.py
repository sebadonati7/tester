"""
CHATBOT.ALPHA v2 - Analytics Dashboard
Analytics engine per visualizzazione KPI clinici e operativi.

V4.0: Supabase Migration - Zero-File Policy
Porta: 8502
Principi: Zero Pandas, Zero PX, Robustezza Assoluta
"""

import streamlit as st

# CONFIGURAZIONE PAGINA - DEVE ESSERE LA PRIMA ISTRUZIONE STREAMLIT
# Wrappata in condizione per evitare errori quando importato da frontend.py
if __name__ == "__main__":
    st.set_page_config(
        page_title="Health Navigator | Strategic Analytics",
        page_icon="🧬",
        layout="wide"
    )

import json
import os
import re
import io
import threading
import csv
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import plotly.graph_objects as go

# === GESTIONE DIPENDENZE OPZIONALI ===
# CRITICAL: Check fatto DOPO st.set_page_config per evitare crash
try:
    import xlsxwriter
    XLSX_AVAILABLE = True
except ImportError:
    XLSX_AVAILABLE = False
    # Warning mostrato in main() per non violare order rule

# === COSTANTI ===
# V5.0: Path log unificato - identico a frontend.py per garantire coerenza
# V3.2: Path centralizzato da app.py per garantire sincronizzazione Streamlit Cloud
# Cloud-ready: Usa env var per path log persistente, ma default identico a frontend
LOG_DIR = os.environ.get("TRIAGE_LOGS_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(LOG_DIR, exist_ok=True)  # Crea directory se non esiste
# Default path (usato se non passato da app.py)
LOG_FILE = os.path.join(LOG_DIR, "triage_logs.jsonl")
DISTRICTS_FILE = "distretti_sanitari_er.json"
def load_json_file(filepath: str) -> Dict:
    """Caricamento sicuro dei file JSON."""
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

# === MAPPATURE CLINICHE ===
RED_FLAGS_KEYWORDS = [
    "svenimento", "sangue", "confusione", "petto", "respiro",
    "paralisi", "convulsioni", "coscienza", "dolore torace",
    "emorragia", "trauma cranico", "infarto", "ictus"
]

SINTOMI_COMUNI = [
    "febbre", "tosse", "mal di testa", "nausea", "dolore addominale",
    "vertigini", "debolezza", "affanno", "palpitazioni", "diarrea",
    "vomito", "mal di gola", "dolore articolare", "eruzioni cutanee",
    "gonfiore", "bruciore", "prurito", "stanchezza"
]

SPECIALIZZAZIONI = [
    "Cardiologia", "Neurologia", "Ortopedia", "Gastroenterologia",
    "Pediatria", "Ginecologia", "Dermatologia", "Psichiatria",
    "Otorinolaringoiatria", "Oftalmologia", "Generale"
]

# === THREAD-SAFETY E CACHE ===
_WRITE_LOCK = threading.Lock()  # Lock globale per scrittura thread-safe JSONL
_FILE_CACHE = {}  # Cache per ottimizzazione mtime: {filepath: {'mtime': float, 'records': List, 'sessions': Dict}}

# Schema obbligatorio per validazione
# V6.0: Schema flessibile - supporta sia log "summary" (vecchi) che "interaction" (nuovi)
REQUIRED_FIELDS_SUMMARY = {
    'session_id': str,
    'timestamp_start': str,
    'timestamp_end': str,
}

REQUIRED_FIELDS_INTERACTION = {
    'session_id': str,
    'timestamp': str,
    'user_input': str,
    'bot_response': str,
}
def cleanup_streamlit_cache():
    """Rimuove le cache fisiche che possono causare il 'Failed to fetch'"""
    cache_dirs = ['.streamlit/cache', '__pycache__']
    for d in cache_dirs:
        if os.path.exists(d):
            try:
                shutil.rmtree(d)
            except:
                pass

cleanup_streamlit_cache()
# --------------------------

# === CLASSE PRINCIPALE: TRIAGE DATA STORE ===
class TriageDataStore:
    """
    Storage e analisi dati triage con parsing robusto.
    V4.0: Supporto Supabase per Zero-File Policy.
    """
    
    def __init__(self, filepath: str = None, use_supabase: bool = True):
        self.filepath = filepath
        self.use_supabase = use_supabase
        self.records: List[Dict] = []
        self.sessions: Dict[str, List[Dict]] = {}
        self.parse_errors = 0
        self.validation_errors = 0
        
        # Cache key per questo filepath (se usato)
        if filepath:
            self._cache_key = str(Path(filepath).absolute())
        else:
            self._cache_key = "supabase_cache"
        
        self._load_data()
        self._enrich_data()
    
    def _validate_record_schema(self, record: Dict, line_num: int = None) -> bool:
        """
        V6.0: Validazione flessibile - supporta sia log "summary" che "interaction".
        
        Args:
            record: Record da validare
            line_num: Numero riga (per logging)
        
        Returns:
            bool: True se valido, False se scartato
        """
        # === DETECTION: Summary vs Interaction ===
        is_interaction = 'timestamp' in record and 'user_input' in record and 'bot_response' in record
        is_summary = 'timestamp_start' in record and 'timestamp_end' in record
        
        if not is_interaction and not is_summary:
            self.validation_errors += 1
            log_msg = f"Record scartato (linea {line_num}): formato sconosciuto (né summary né interaction)"
            print(f"⚠️ {log_msg}")
            return False
        
        # === VALIDAZIONE INTERACTION (V6.0 - Real-time) ===
        if is_interaction:
            # Campi obbligatori per interaction
            for field, expected_type in REQUIRED_FIELDS_INTERACTION.items():
                if field not in record:
                    self.validation_errors += 1
                    log_msg = f"Record interaction scartato (linea {line_num}): campo '{field}' mancante"
                    print(f"⚠️ {log_msg}")
                    return False
                
                if not isinstance(record[field], expected_type):
                    self.validation_errors += 1
                    log_msg = f"Record interaction scartato (linea {line_num}): campo '{field}' tipo errato"
                    print(f"⚠️ {log_msg}")
                    return False
            
            # Validazione timestamp ISO 8601
            try:
                datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                self.validation_errors += 1
                log_msg = f"Record interaction scartato (linea {line_num}): timestamp formato non valido"
                print(f"⚠️ {log_msg}")
                return False
            
            # Interaction log valido (urgency_level opzionale, può essere in metadata)
            return True
        
        # === VALIDAZIONE SUMMARY (Legacy - Fine Sessione) ===
        if is_summary:
            # Campi obbligatori per summary
            for field, expected_type in REQUIRED_FIELDS_SUMMARY.items():
                if field not in record:
                    self.validation_errors += 1
                    log_msg = f"Record summary scartato (linea {line_num}): campo '{field}' mancante"
                    print(f"⚠️ {log_msg}")
                    return False
                
                if not isinstance(record[field], expected_type):
                    self.validation_errors += 1
                    log_msg = f"Record summary scartato (linea {line_num}): campo '{field}' tipo errato"
                    print(f"⚠️ {log_msg}")
                    return False
            
            # Verifica presenza urgency_level (obbligatorio per summary)
            urgency_found = False
            
            if 'outcome' in record and isinstance(record['outcome'], dict):
                if 'urgency_level' in record['outcome']:
                    urgency_found = True
            
            if not urgency_found and 'metadata' in record and isinstance(record['metadata'], dict):
                if 'urgency' in record['metadata'] or 'urgency_level' in record['metadata']:
                    urgency_found = True
            
            if not urgency_found and ('urgency' in record or 'urgency_level' in record):
                urgency_found = True
            
            if not urgency_found:
                self.validation_errors += 1
                log_msg = f"Record summary scartato (linea {line_num}): urgency_level non trovato"
                print(f"⚠️ {log_msg}")
                return False
            
            # Validazione formato timestamp (ISO 8601)
            for ts_field in ['timestamp_start', 'timestamp_end']:
                if ts_field in record:
                    ts_str = record[ts_field]
                    try:
                        datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        self.validation_errors += 1
                        log_msg = f"Record summary scartato (linea {line_num}): timestamp '{ts_field}' formato non valido"
                        print(f"⚠️ {log_msg}")
                        return False
            
            return True
        
        return False
    
    def _load_data(self):
        """
        Caricamento dati con supporto Supabase e fallback JSONL.
        V4.0: Prima prova Supabase, poi fallback su file locale.
        """
        # === SUPABASE LOADING (Priority) ===
        if self.use_supabase:
            try:
                from session_storage import get_logger
                
                logger = get_logger()
                
                if logger.client:
                    st.info("📡 Caricamento dati da Supabase...")
                    
                    # Recupera tutti i log
                    raw_logs = logger.get_all_logs_for_analytics()
                    
                    # DEBUG: Print raw response to diagnose RLS/parsing issues
                    print(f"🔍 DEBUG: Supabase response type: {type(raw_logs)}")
                    print(f"🔍 DEBUG: Supabase response length: {len(raw_logs) if raw_logs else 0}")
                    if raw_logs and len(raw_logs) > 0:
                        print(f"🔍 DEBUG: First record keys: {list(raw_logs[0].keys()) if isinstance(raw_logs[0], dict) else 'NOT A DICT'}")
                        print(f"🔍 DEBUG: First record sample: {str(raw_logs[0])[:200] if raw_logs else 'EMPTY'}")
                    elif raw_logs is None:
                        print("🔍 DEBUG: Supabase returned None (check RLS policies)")
                    elif raw_logs == []:
                        print("🔍 DEBUG: Supabase returned empty list [] (check RLS policies or table is empty)")
                    
                    if raw_logs:
                        # Converti logs Supabase al formato interno
                        for log in raw_logs:
                            try:
                                # Parse metadata JSON
                                metadata = {}
                                if 'metadata' in log and log['metadata']:
                                    try:
                                        metadata = json.loads(log['metadata'])
                                    except:
                                        pass
                                
                                # Formato record per compatibilità con analytics
                                record = {
                                    'session_id': log.get('session_id', 'unknown'),
                                    'timestamp': log.get('timestamp', ''),
                                    'user_input': log.get('user_input', ''),
                                    'bot_response': log.get('bot_response', ''),
                                    'duration_ms': log.get('duration_ms', 0),
                                    'metadata': metadata
                                }
                                
                                # Validazione base
                                if record['session_id'] and record['timestamp']:
                                    self.records.append(record)
                                    
                                    # Raggruppa per sessione
                                    sid = record['session_id']
                                    if sid not in self.sessions:
                                        self.sessions[sid] = []
                                    self.sessions[sid].append(record)
                            
                            except Exception as e:
                                self.parse_errors += 1
                                continue
                        
                        st.success(f"✅ Caricati {len(self.records)} record da Supabase")
                        return
                    else:
                        st.warning("⚠️ Nessun log disponibile in Supabase")
                        
            except ImportError:
                st.warning("⚠️ session_storage non disponibile, uso fallback file locale")
            except Exception as e:
                st.error(f"❌ Errore caricamento Supabase: {e}")
        
        # === FALLBACK: JSONL FILE LOADING ===
        if not self.filepath:
            st.warning("⚠️ Nessuna fonte dati disponibile (né Supabase né file locale)")
            return
        
        filepath_obj = Path(self.filepath)
        
        if not filepath_obj.exists():
            st.warning(f"⚠️ File {self.filepath} non trovato. Nessun dato disponibile.")
            return
        
        if filepath_obj.stat().st_size == 0:
            st.warning(f"⚠️ File {self.filepath} vuoto. Inizia un triage per popolare i dati.")
            return
        
        # === CACHE-BUSTING: Verifica mtime ===
        current_mtime = filepath_obj.stat().st_mtime
        
        if self._cache_key in _FILE_CACHE:
            cached = _FILE_CACHE[self._cache_key]
            if cached['mtime'] == current_mtime:
                # Cache hit: usa dati cached
                self.records = cached['records'].copy()
                self.sessions = cached['sessions'].copy()
                return
        
        # Cache miss o file modificato: ricarica
        self.parse_errors = 0
        self.validation_errors = 0
        self.records = []
        self.sessions = {}
        
        # CRITICAL: Prova encoding multipli per massima resilienza
        encodings_to_try = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']
        
        for encoding in encodings_to_try:
            try:
                with open(self.filepath, 'r', encoding=encoding, errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        
                        try:
                            record = json.loads(line)
                            
                            # === VALIDAZIONE SCHEMA ===
                            if not self._validate_record_schema(record, line_num):
                                # Record scartato per validazione fallita
                                continue
                            
                            self.records.append(record)
                            
                        except json.JSONDecodeError as e:
                            self.parse_errors += 1
                            continue
                
                # Se siamo arrivati qui, l'encoding ha funzionato
                if encoding != 'utf-8':
                    print(f"Info: File caricato con encoding {encoding}")
                break
                
            except UnicodeDecodeError:
                # Prova con il prossimo encoding
                continue
            except Exception as e:
                if encoding == encodings_to_try[-1]:
                    # Ultimo tentativo fallito
                    st.error(f"❌ Errore lettura file con tutti gli encoding: {e}")
                    return
        
        # Aggiorna cache
        _FILE_CACHE[self._cache_key] = {
            'mtime': current_mtime,
            'records': self.records.copy(),
            'sessions': {}
        }
        
        if self.parse_errors > 0:
            st.info(f"ℹ️ {self.parse_errors} righe JSON corrotte saltate durante il caricamento.")
        
        if self.validation_errors > 0:
            st.warning(f"⚠️ {self.validation_errors} record scartati per validazione schema fallita (campi obbligatori mancanti).")
    
    def _parse_timestamp_iso(self, timestamp_str: str) -> Optional[datetime]:
        """
        Parsing ISO robusto con correzione bug temporale.
        Supporta formati: ISO 8601, datetime standard.
        """
        if not timestamp_str:
            return None
        
        try:
            # Rimuovi 'Z' finale e converti in offset +00:00
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1] + '+00:00'
            
            # Parsing ISO 8601
            dt = datetime.fromisoformat(timestamp_str)
            
            # Fix timezone-aware to naive (usa orario locale)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            
            return dt
        
        except ValueError:
            # Fallback: formati alternativi
            for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
                try:
                    return datetime.strptime(timestamp_str, fmt)
                except ValueError:
                    continue
        
        except Exception as e:
            print(f"⚠️ Errore parsing timestamp '{timestamp_str}': {e}")
        
        return None
    
    def _enrich_data(self):
        """Arricchimento dati con calcoli temporali e NLP. Aggiorna anche cache."""
        for record in self.records:
            # === PARSING TEMPORALE ROBUSTO ===
            # Usa timestamp_end se disponibile, altrimenti timestamp_start
            timestamp_str = record.get('timestamp_end') or record.get('timestamp_start') or record.get('timestamp')
            dt = self._parse_timestamp_iso(timestamp_str)
            
            if dt:
                record['datetime'] = dt
                record['date'] = dt.date()
                record['year'] = dt.isocalendar()[0]
                record['month'] = dt.month
                record['week'] = dt.isocalendar()[1]  # Settimana ISO
                record['day_of_week'] = dt.weekday()  # 0=Lunedì, 6=Domenica
                record['hour'] = dt.hour
            else:
                # Fallback: timestamp corrente se mancante
                now = datetime.now()
                record['datetime'] = now
                record['date'] = now.date()
                record['year'] = now.year
                record['month'] = now.month
                record['week'] = now.isocalendar()[1]
                record['day_of_week'] = now.weekday()
                record['hour'] = now.hour
            
            # === NLP E CLASSIFICAZIONE ===
            user_input = str(record.get('user_input', '')).lower()
            bot_response = str(record.get('bot_response', '')).lower()
            combined_text = user_input + " " + bot_response
            
            # Red Flags Detection
            record['red_flags'] = [kw for kw in RED_FLAGS_KEYWORDS if kw in combined_text]
            record['has_red_flag'] = len(record['red_flags']) > 0
            
            # Sintomi Detection
            record['sintomi_rilevati'] = [s for s in SINTOMI_COMUNI if s in combined_text]
            
            # Estrazione Urgenza (priorità: outcome > metadata > root)
            urgency = None
            if 'outcome' in record and isinstance(record['outcome'], dict):
                urgency = record['outcome'].get('urgency_level') or record['outcome'].get('urgency')
            if urgency is None and 'metadata' in record and isinstance(record['metadata'], dict):
                urgency = record['metadata'].get('urgency_level') or record['metadata'].get('urgency')
            if urgency is None:
                urgency = record.get('urgency_level') or record.get('urgency', 3)
            
            record['urgenza'] = urgency if urgency is not None else 3
            record['area_clinica'] = record.get('area_clinica', 'Non Specificato')
            record['specializzazione'] = record.get('specializzazione', 'Generale')
            
            # === MAPPING COMUNE → DISTRETTO (Case-Insensitive & Trim-Safe) ===
            comune_raw = record.get('comune') or record.get('location') or record.get('LOCATION')
            if comune_raw:
                comune_normalized = str(comune_raw).lower().strip()
                try:
                    district_data = load_district_mapping()
                    if district_data:
                        mapping = district_data.get("comune_to_district_mapping", {})
                        distretto = mapping.get(comune_normalized, "UNKNOWN")
                        record['distretto'] = distretto
                except Exception:
                    record['distretto'] = "UNKNOWN"
            else:
                record['distretto'] = "UNKNOWN"
            
            # Organizzazione per Sessione
            session_id = record.get('session_id')
            if session_id:
                if session_id not in self.sessions:
                    self.sessions[session_id] = []
                self.sessions[session_id].append(record)
        
        # Aggiorna cache sessions
        if self._cache_key in _FILE_CACHE:
            _FILE_CACHE[self._cache_key]['sessions'] = self.sessions.copy()
    
    def filter(self, year: Optional[int] = None, month: Optional[int] = None, 
               week: Optional[int] = None, district: Optional[str] = None) -> 'TriageDataStore':
        """
        Filtraggio records con creazione nuovo datastore.
        """
        filtered = TriageDataStore.__new__(TriageDataStore)
        filtered.filepath = self.filepath
        filtered.parse_errors = 0
        filtered.validation_errors = 0  # Reset per filtered datastore
        filtered._cache_key = str(Path(filtered.filepath).absolute())
        filtered.records = self.records.copy()
        filtered.sessions = {}
        
        if year is not None:
            filtered.records = [r for r in filtered.records if r.get('year') == year]
        
        if month is not None:
            filtered.records = [r for r in filtered.records if r.get('month') == month]
        
        if week is not None:
            filtered.records = [r for r in filtered.records if r.get('week') == week]
        
        if district and district != "Tutti":
            # Case-insensitive district filtering (usa campo 'distretto' se disponibile)
            district_normalized = str(district).lower().strip()
            filtered.records = [
                r for r in filtered.records 
                if str(r.get('distretto', '')).lower().strip() == district_normalized
            ]
        
        # Ricostruisci sessions
        for record in filtered.records:
            sid = record.get('session_id')
            if sid:
                if sid not in filtered.sessions:
                    filtered.sessions[sid] = []
                filtered.sessions[sid].append(record)
        
        return filtered
    
    @staticmethod
    def append_record_thread_safe(filepath: str, record: Dict) -> bool:
        """
        Scrittura thread-safe di un record su file JSONL.
        Usa lock globale per prevenire corruzioni in ambienti multi-utente.
        
        Args:
            filepath: Path al file JSONL
            record: Record da scrivere (dict)
        
        Returns:
            bool: True se scritto con successo, False altrimenti
        """
        try:
            with _WRITE_LOCK:
                # Validazione record prima di scrivere
                temp_store = TriageDataStore.__new__(TriageDataStore)
                temp_store.validation_errors = 0
                
                if not temp_store._validate_record_schema(record):
                    return False
                
                # Scrittura atomica: append con flush
                with open(filepath, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
                    f.flush()
                    os.fsync(f.fileno())  # Force write to disk
                
                # Invalida cache per questo filepath
                cache_key = str(Path(filepath).absolute())
                if cache_key in _FILE_CACHE:
                    del _FILE_CACHE[cache_key]
                
                return True
                
        except Exception as e:
            return False
    
    def to_csv(self, include_enriched: bool = True) -> bytes:
        """
        Esporta dati in formato CSV pronto per download Streamlit.
        
        Args:
            include_enriched: Se True, include campi arricchiti (year, month, week, distretto, ecc.)
        
        Returns:
            bytes: CSV in memoria (pronto per st.download_button)
        """
        if not self.records:
            return b''
        
        output = io.StringIO()
        
        base_columns = [
            'session_id', 'timestamp_start', 'timestamp_end', 'total_duration_seconds',
            'urgency_level', 'disposition', 'facility_recommended', 'comune', 'distretto'
        ]
        
        enriched_columns = [
            'year', 'month', 'week', 'day_of_week', 'hour',
            'area_clinica', 'specializzazione', 'has_red_flag', 'red_flags_count'
        ]
        
        columns = base_columns + (enriched_columns if include_enriched else [])
        
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        
        for record in self.records:
            row = {}
            
            row['session_id'] = record.get('session_id', '')
            row['timestamp_start'] = record.get('timestamp_start', '')
            row['timestamp_end'] = record.get('timestamp_end', '')
            row['total_duration_seconds'] = record.get('total_duration_seconds', '')
            
            # Urgency (cerca in outcome/metadata/root)
            urgency = None
            if 'outcome' in record and isinstance(record['outcome'], dict):
                urgency = record['outcome'].get('urgency_level') or record['outcome'].get('urgency')
            if urgency is None and 'metadata' in record and isinstance(record['metadata'], dict):
                urgency = record['metadata'].get('urgency_level') or record['metadata'].get('urgency')
            row['urgency_level'] = urgency or record.get('urgenza', record.get('urgency', ''))
            
            if 'outcome' in record and isinstance(record['outcome'], dict):
                row['disposition'] = record['outcome'].get('disposition', '')
                row['facility_recommended'] = record['outcome'].get('facility_recommended', '')
            
            row['comune'] = record.get('comune') or record.get('location') or record.get('LOCATION', '')
            row['distretto'] = record.get('distretto', '')
            
            if include_enriched:
                row['year'] = record.get('year', '')
                row['month'] = record.get('month', '')
                row['week'] = record.get('week', '')
                row['day_of_week'] = record.get('day_of_week', '')
                row['hour'] = record.get('hour', '')
                row['area_clinica'] = record.get('area_clinica', '')
                row['specializzazione'] = record.get('specializzazione', '')
                row['has_red_flag'] = record.get('has_red_flag', False)
                row['red_flags_count'] = len(record.get('red_flags', []))
            
            writer.writerow(row)
        
        return output.getvalue().encode('utf-8-sig')  # UTF-8 BOM per Excel compatibility
    
    def to_excel(self, kpi_vol: Dict = None, kpi_clin: Dict = None, kpi_ctx: Dict = None, 
                 kpi_completo: Dict = None, district: str = None, date_from: str = None, 
                 date_to: str = None) -> Optional[bytes]:
        """
        Esporta dati in formato Excel con fogli multipli (Dashboard KPI + Dettaglio).
        Metodo della classe per coerenza architetturale.
        
        Args:
            kpi_vol: KPI volumetrici (opzionale)
            kpi_clin: KPI clinici (opzionale)
            kpi_ctx: KPI context-aware (opzionale)
            kpi_completo: KPI completo (15 KPI avanzati)
            district: Nome distretto per titolo dinamico
            date_from: Data inizio periodo
            date_to: Data fine periodo
        
        Returns:
            bytes: Excel in memoria (pronto per st.download_button) o None se xlsxwriter non disponibile
        """
        if not XLSX_AVAILABLE:
            return None
        
        # Gestione caso "No Data" - verifica se ci sono record
        if not self.records:
            # Crea Excel con messaggio elegante "No Data"
            output = io.BytesIO()
            workbook = xlsxwriter.Workbook(output, {'in_memory': True})
            ws_dashboard = workbook.add_worksheet('Dashboard')
            
            title_format = workbook.add_format({'bold': True, 'font_size': 16, 'bg_color': '#1e293b', 'font_color': 'white'})
            info_format = workbook.add_format({'italic': True, 'font_size': 12, 'font_color': '#666666'})
            
            periodo = f"{district or 'TUTTI I DISTRETTI'}"
            if date_from and date_to:
                periodo += f" - {date_from} / {date_to}"
            elif date_from:
                periodo += f" - Dal {date_from}"
            
            ws_dashboard.merge_range('A1:D1', f'ANALISI DATI {periodo}', title_format)
            ws_dashboard.set_row(0, 30)
            ws_dashboard.write(2, 0, '⚠️ Nessun dato disponibile per i filtri selezionati.', info_format)
            ws_dashboard.write(3, 0, 'Modifica i filtri temporali o geografici per visualizzare i dati.', info_format)
            
            workbook.close()
            output.seek(0)
            return output.read()
        
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        
        # Formati
        header_format = workbook.add_format({'bold': True, 'bg_color': '#4472C4', 'font_color': 'white'})
        title_format = workbook.add_format({'bold': True, 'font_size': 16, 'bg_color': '#1e293b', 'font_color': 'white'})
        number_format = workbook.add_format({'num_format': '0.00'})
        percent_format = workbook.add_format({'num_format': '0.00%'})
        
        # === FOGLIO 1: DASHBOARD KPI ===
        ws_dashboard = workbook.add_worksheet('Dashboard')
        
        # Titolo dinamico
        periodo = f"{district or 'TUTTI I DISTRETTI'}"
        if date_from and date_to:
            periodo += f" - {date_from} / {date_to}"
        elif date_from:
            periodo += f" - Dal {date_from}"
        
        ws_dashboard.merge_range('A1:D1', f'ANALISI DATI {periodo}', title_format)
        ws_dashboard.set_row(0, 30)
        
        row = 2
        ws_dashboard.write(row, 0, 'KPI', header_format)
        ws_dashboard.write(row, 1, 'Descrizione', header_format)
        ws_dashboard.write(row, 2, 'Valore', header_format)
        ws_dashboard.write(row, 3, 'Unità', header_format)
        row += 1
        
        # KPI Completo (15 KPI avanzati)
        if kpi_completo:
            kpi_mapping = [
                ('Accuratezza Clinica', 'accuratezza_clinica', '%', percent_format),
                ('Latenza Media', 'latenza_media_secondi', 'secondi', number_format),
                ('Tasso Completamento', 'tasso_completamento', '%', percent_format),
                ('Aderenza Protocolli', 'aderenza_protocolli', '%', percent_format),
                ('User Sentiment', 'sentiment_medio', 'score', number_format),
                ('Efficienza Reindirizzamento', 'efficienza_reindirizzamento', '%', percent_format),
                ('Sessioni Univoche', 'sessioni_uniche', 'n', number_format),
                ('Tempo Mediano Triage', 'tempo_mediano_triage_minuti', 'minuti', number_format),
                ('Tasso Divergenza Algoritmica', 'tasso_divergenza_algoritmica', '%', percent_format),
                ('Tasso Omissione Red Flags', 'tasso_omissione_red_flags', '%', percent_format),
                ('Funnel Drop-off Rate', 'funnel_dropoff.dropoff_rate', '%', percent_format),
                ('Indice Esitazione', 'indice_esitazione_secondi', 'secondi', number_format),
                ('Fast Track Efficiency Ratio', 'fast_track_efficiency_ratio', 'ratio', number_format),
            ]
            
            for name, key, unit, fmt in kpi_mapping:
                if '.' in key:
                    # Nested key (es. funnel_dropoff.dropoff_rate)
                    parts = key.split('.')
                    val = kpi_completo.get(parts[0], {})
                    if isinstance(val, dict):
                        val = val.get(parts[1], 0)
                else:
                    val = kpi_completo.get(key, 0)
                
                if isinstance(val, (int, float)):
                    ws_dashboard.write(row, 0, name)
                    ws_dashboard.write(row, 1, f'KPI: {name}')
                    ws_dashboard.write(row, 2, val, fmt)
                    ws_dashboard.write(row, 3, unit)
                    row += 1
        
        # KPI Volumetrici aggiuntivi
        if kpi_vol:
            ws_dashboard.write(row, 0, 'Interazioni Totali')
            ws_dashboard.write(row, 1, 'Numero totale di interazioni')
            ws_dashboard.write(row, 2, kpi_vol.get('interazioni_totali', 0), number_format)
            ws_dashboard.write(row, 3, 'n')
            row += 1
            
            ws_dashboard.write(row, 0, 'Completion Rate')
            ws_dashboard.write(row, 1, 'Percentuale sessioni completate')
            ws_dashboard.write(row, 2, kpi_vol.get('completion_rate', 0), percent_format)
            ws_dashboard.write(row, 3, '%')
            row += 1
        
        # KPI Clinici aggiuntivi
        if kpi_clin:
            ws_dashboard.write(row, 0, 'Prevalenza Red Flags')
            ws_dashboard.write(row, 1, 'Percentuale casi con red flags')
            ws_dashboard.write(row, 2, kpi_clin.get('prevalenza_red_flags', 0), percent_format)
            ws_dashboard.write(row, 3, '%')
            row += 1
        
        # KPI Context-Aware aggiuntivi
        if kpi_ctx:
            ws_dashboard.write(row, 0, 'Tasso Deviazione PS')
            ws_dashboard.write(row, 1, 'Percentuale casi indirizzati al PS')
            ws_dashboard.write(row, 2, kpi_ctx.get('tasso_deviazione_ps', 0), percent_format)
            ws_dashboard.write(row, 3, '%')
        
        # Pulsanti download replicati in basso (simulazione con note)
        last_row = row + 3
        ws_dashboard.write(last_row, 0, 'Nota: I pulsanti di download sono disponibili nell\'interfaccia web', 
                          workbook.add_format({'italic': True, 'font_color': '#666666'}))
        
        # === FOGLIO 2: DETTAGLIO PER DISTRETTO E AUSL ===
        ws_dettaglio = workbook.add_worksheet('Dettaglio')
        
        # Titolo
        ws_dettaglio.merge_range('A1:F1', f'ANALISI DETTAGLIO {periodo}', title_format)
        ws_dettaglio.set_row(0, 30)
        
        row = 2
        headers = ['Distretto', 'AUSL', 'Sessioni', 'Interazioni', 'Urgenza Media', 'Red Flags %']
        for col, header in enumerate(headers):
            ws_dettaglio.write(row, col, header, header_format)
        row += 1
        
        # Carica district_data per mappatura AUSL
        try:
            district_data = load_district_mapping()
        except:
            district_data = {}
        
        # Aggrega per distretto
        district_stats = defaultdict(lambda: {'ausl': '', 'sessions': set(), 'interactions': 0, 
                                               'urgency_sum': 0, 'urgency_count': 0, 'red_flags': 0})
        
        for record in self.records:
            dist = record.get('distretto', 'UNKNOWN')
            district_stats[dist]['sessions'].add(record.get('session_id'))
            district_stats[dist]['interactions'] += 1
            urgency = record.get('urgenza', 3)
            district_stats[dist]['urgency_sum'] += urgency
            district_stats[dist]['urgency_count'] += 1
            if record.get('has_red_flag', False):
                district_stats[dist]['red_flags'] += 1
            
            # Mappa AUSL
            if district_data:
                for ausl_item in district_data.get('health_districts', []):
                    if 'districts' in ausl_item:
                        for d in ausl_item['districts']:
                            if d.get('name') == dist:
                                district_stats[dist]['ausl'] = ausl_item.get('ausl', '')
        
        # Scrivi statistiche per distretto
        for dist, stats in sorted(district_stats.items()):
            sessions_count = len(stats['sessions'])
            urgency_avg = stats['urgency_sum'] / stats['urgency_count'] if stats['urgency_count'] > 0 else 0
            red_flags_pct = (stats['red_flags'] / stats['interactions'] * 100) if stats['interactions'] > 0 else 0
            
            ws_dettaglio.write(row, 0, dist)
            ws_dettaglio.write(row, 1, stats['ausl'] or 'N/D')
            ws_dettaglio.write(row, 2, sessions_count, number_format)
            ws_dettaglio.write(row, 3, stats['interactions'], number_format)
            ws_dettaglio.write(row, 4, urgency_avg, number_format)
            ws_dettaglio.write(row, 5, red_flags_pct, percent_format)
            row += 1
        
        workbook.close()
        output.seek(0)
        return output.read()
        
        ws_raw = workbook.add_worksheet('Dati Grezzi')
        header_format = workbook.add_format({'bold': True, 'bg_color': '#4472C4', 'font_color': 'white'})
        
        headers = [
            'Timestamp End', 'Session ID', 'Urgency Level', 'Disposition', 
            'Facility', 'Comune', 'Distretto', 'Year', 'Month', 'Week',
            'Area Clinica', 'Specializzazione', 'Has Red Flag'
        ]
        
        for col, header in enumerate(headers):
            ws_raw.write(0, col, header, header_format)
        
        row = 1
        for record in self.records:
            ws_raw.write(row, 0, record.get('timestamp_end', ''))
            ws_raw.write(row, 1, record.get('session_id', ''))
            
            urgency = record.get('urgenza')
            if urgency is None and 'outcome' in record and isinstance(record['outcome'], dict):
                urgency = record['outcome'].get('urgency_level') or record['outcome'].get('urgency')
            ws_raw.write(row, 2, urgency or '')
            
            if 'outcome' in record and isinstance(record['outcome'], dict):
                ws_raw.write(row, 3, record['outcome'].get('disposition', ''))
                ws_raw.write(row, 4, record['outcome'].get('facility_recommended', ''))
            
            ws_raw.write(row, 5, record.get('comune') or record.get('location', ''))
            ws_raw.write(row, 6, record.get('distretto', ''))
            ws_raw.write(row, 7, record.get('year', ''))
            ws_raw.write(row, 8, record.get('month', ''))
            ws_raw.write(row, 9, record.get('week', ''))
            ws_raw.write(row, 10, record.get('area_clinica', ''))
            ws_raw.write(row, 11, record.get('specializzazione', ''))
            ws_raw.write(row, 12, record.get('has_red_flag', False))
            
            row += 1
        
        workbook.close()
        output.seek(0)
        return output.read()
    
    def reload_if_updated(self) -> bool:
        """
        Ricarica i dati solo se il file è stato modificato (cache-busting).
        Utile per aggiornare dashboard dopo nuovi triage.
        
        Returns:
            bool: True se ricaricato, False se cache valida
        """
        filepath_obj = Path(self.filepath)
        if not filepath_obj.exists():
            return False
        
        current_mtime = filepath_obj.stat().st_mtime
        
        if self._cache_key in _FILE_CACHE:
            cached = _FILE_CACHE[self._cache_key]
            if cached['mtime'] == current_mtime:
                return False  # Cache valida
        
        # File modificato: ricarica
        self._load_data()
        self._enrich_data()
        return True
    
    def get_unique_values(self, field: str) -> List:
        """Estrae valori unici per un campo."""
        values = set()
        for record in self.records:
            val = record.get(field)
            if val is not None:
                values.add(val)
        return sorted(list(values))


# === FUNZIONI KPI ===
def calculate_kpi_volumetrici(datastore: TriageDataStore) -> Dict:
    """KPI Volumetrici: Sessioni, Throughput, Completion Rate."""
    kpi = {}
    
    # Sessioni Univoche
    kpi['sessioni_uniche'] = len(datastore.sessions)
    
    # Interazioni Totali
    kpi['interazioni_totali'] = len(datastore.records)
    
    # Throughput Orario (distribuzione)
    hours = [r.get('hour', 0) for r in datastore.records if r.get('hour') is not None]
    kpi['throughput_orario'] = Counter(hours)
    
    # Completion Rate (sessioni che raggiungono DISPOSITION)
    completed_sessions = 0
    for sid, records in datastore.sessions.items():
        # Controlla se c'è un record con step DISPOSITION o parole chiave finali
        for r in records:
            bot_resp = str(r.get('bot_response', '')).lower()
            if 'raccomand' in bot_resp or 'disposition' in bot_resp or 'pronto soccorso' in bot_resp:
                completed_sessions += 1
                break
    
    kpi['completion_rate'] = (completed_sessions / kpi['sessioni_uniche'] * 100) if kpi['sessioni_uniche'] > 0 else 0
    
    # Tempo Mediano Triage (calcolo approssimativo)
    session_durations = []
    for sid, records in datastore.sessions.items():
        if len(records) >= 2:
            timestamps = [r.get('datetime') for r in records if r.get('datetime')]
            if len(timestamps) >= 2:
                duration = (max(timestamps) - min(timestamps)).total_seconds() / 60  # minuti
                if duration < 120:  # Escludi sessioni "zombie" > 2h
                    session_durations.append(duration)
    
    if session_durations:
        session_durations.sort()
        median_idx = len(session_durations) // 2
        kpi['tempo_mediano_minuti'] = session_durations[median_idx]
    else:
        kpi['tempo_mediano_minuti'] = 0
    
    # Profondità Media (interazioni per sessione)
    kpi['profondita_media'] = kpi['interazioni_totali'] / kpi['sessioni_uniche'] if kpi['sessioni_uniche'] > 0 else 0
    
    return kpi


def calculate_kpi_clinici(datastore: TriageDataStore) -> Dict:
    """KPI Clinici: Sintomi, Urgenza, Red Flags."""
    kpi = {}
    
    # Spettro Sintomatologico Completo
    all_sintomi = []
    for r in datastore.records:
        all_sintomi.extend(r.get('sintomi_rilevati', []))
    kpi['spettro_sintomi'] = Counter(all_sintomi)
    
    # Stratificazione Urgenza
    urgenze = [r.get('urgenza', 3) for r in datastore.records]
    kpi['stratificazione_urgenza'] = Counter(urgenze)
    
    # Prevalenza Red Flags
    red_flags_count = sum(1 for r in datastore.records if r.get('has_red_flag', False))
    kpi['prevalenza_red_flags'] = (red_flags_count / len(datastore.records) * 100) if datastore.records else 0
    
    # Red Flags per Tipo
    all_red_flags = []
    for r in datastore.records:
        all_red_flags.extend(r.get('red_flags', []))
    kpi['red_flags_dettaglio'] = Counter(all_red_flags)
    
    return kpi


def calculate_kpi_context_aware(datastore: TriageDataStore) -> Dict:
    """KPI Context-Aware: Urgenza per Specializzazione, Deviazione PS."""
    kpi = {}
    
    # Urgenza Media per Specializzazione
    urgenza_per_spec = defaultdict(list)
    for r in datastore.records:
        spec = r.get('specializzazione', 'Generale')
        urgenza = r.get('urgenza', 3)
        urgenza_per_spec[spec].append(urgenza)
    
    kpi['urgenza_media_per_spec'] = {
        spec: sum(urgs) / len(urgs) if urgs else 0
        for spec, urgs in urgenza_per_spec.items()
    }
    
    # Tasso Deviazione PS per Area
    ps_keywords = ['pronto soccorso', 'ps', 'emergenza', '118']
    territorial_keywords = ['cau', 'guardia medica', 'medico di base', 'farmacia']
    
    deviazione_ps = 0
    deviazione_territoriale = 0
    
    for r in datastore.records:
        bot_resp = str(r.get('bot_response', '')).lower()
        if any(kw in bot_resp for kw in ps_keywords):
            deviazione_ps += 1
        elif any(kw in bot_resp for kw in territorial_keywords):
            deviazione_territoriale += 1
    
    total_recommendations = deviazione_ps + deviazione_territoriale
    kpi['tasso_deviazione_ps'] = (deviazione_ps / total_recommendations * 100) if total_recommendations > 0 else 0
    kpi['tasso_deviazione_territoriale'] = (deviazione_territoriale / total_recommendations * 100) if total_recommendations > 0 else 0
    
    return kpi


def calculate_kpi_completo(datastore: TriageDataStore) -> Dict:
    """
    Framework KPI Completo - Calcola tutti i 15 KPI clinici avanzati.
    Logica di calcolo descritta per ciascun KPI.
    """
    kpi = {}
    
    # 1. ACCURATEZZA CLINICA
    # Valutazione della coerenza tra sintomi dichiarati e disposizione finale
    accurate_sessions = 0
    total_sessions_with_disposition = 0
    
    for sid, records in datastore.sessions.items():
        user_symptoms = []
        final_disposition = None
        final_urgency = None
        
        for r in records:
            user_input = str(r.get('user_input', '')).lower()
            # Estrai sintomi menzionati
            for symptom in SINTOMI_COMUNI:
                if symptom in user_input:
                    user_symptoms.append(symptom)
            
            # Estrai disposizione finale
            if 'outcome' in r and isinstance(r['outcome'], dict):
                final_disposition = r['outcome'].get('disposition')
                final_urgency = r['outcome'].get('urgency_level')
        
        if final_disposition and user_symptoms:
            total_sessions_with_disposition += 1
            # Logica semplificata: se urgenza alta e sintomi gravi, o urgenza bassa e sintomi lievi
            has_red_flag = any(rf in ' '.join(user_symptoms) for rf in RED_FLAGS_KEYWORDS)
            if (final_urgency and final_urgency >= 4 and has_red_flag) or \
               (final_urgency and final_urgency <= 2 and not has_red_flag):
                accurate_sessions += 1
    
    kpi['accuratezza_clinica'] = (accurate_sessions / total_sessions_with_disposition * 100) if total_sessions_with_disposition > 0 else 0
    
    # 2. LATENZA MEDIA
    # Tempo di risposta del modello AI tra prompt utente e generazione triage
    latencies = []
    for r in datastore.records:
        # Stima latenza: differenza tra timestamp record e timestamp precedente (se interaction log)
        if 'timestamp' in r:
            # Per interaction log, latenza è tempo tra user_input e bot_response
            # Approssimazione: se ci sono più record nella stessa sessione, calcola differenza
            pass  # Richiede timestamp più granulari
    
    # Approssimazione: usa durata totale sessione / numero interazioni
    total_latency_seconds = 0
    total_interactions = 0
    for sid, records in datastore.sessions.items():
        if len(records) >= 2:
            timestamps = [r.get('datetime') for r in records if r.get('datetime')]
            if len(timestamps) >= 2:
                session_duration = (max(timestamps) - min(timestamps)).total_seconds()
                total_latency_seconds += session_duration / len(records)  # Latenza media per sessione
                total_interactions += len(records)
    
    kpi['latenza_media_secondi'] = (total_latency_seconds / len(datastore.sessions)) if datastore.sessions else 0
    
    # 3. TASSO DI COMPLETAMENTO
    # Percentuale utenti che terminano l'intero flusso fino alla raccomandazione finale
    completed = 0
    for sid, records in datastore.sessions.items():
        for r in records:
            bot_resp = str(r.get('bot_response', '')).lower()
            if 'raccomand' in bot_resp or 'disposition' in bot_resp or 'pronto soccorso' in bot_resp:
                completed += 1
                break
    
    kpi['tasso_completamento'] = (completed / len(datastore.sessions) * 100) if datastore.sessions else 0
    
    # 4. ADERENZA AI PROTOCOLLI
    # Verifica se il flusso ha seguito le linee guida regionali
    # Logica: controlla se sono stati raccolti tutti i dati necessari (age, location, sintomi)
    protocol_adherent = 0
    for sid, records in datastore.sessions.items():
        has_age = False
        has_location = False
        has_symptoms = False
        
        for r in records:
            if r.get('age') or 'età' in str(r.get('user_input', '')).lower():
                has_age = True
            if r.get('comune') or r.get('location'):
                has_location = True
            if any(s in str(r.get('user_input', '')).lower() for s in SINTOMI_COMUNI):
                has_symptoms = True
        
        if has_age and has_location and has_symptoms:
            protocol_adherent += 1
    
    kpi['aderenza_protocolli'] = (protocol_adherent / len(datastore.sessions) * 100) if datastore.sessions else 0
    
    # 5. USER SENTIMENT
    # Analisi del tono dell'utente (positivo/neutro/negativo/urgente)
    positive_keywords = ['grazie', 'perfetto', 'ottimo', 'bene', 'ok']
    negative_keywords = ['male', 'peggio', 'preoccupato', 'paura', 'ansia']
    urgent_keywords = ['subito', 'immediato', 'urgente', 'emergenza', 'ora']
    
    sentiment_scores = []
    for r in datastore.records:
        user_input = str(r.get('user_input', '')).lower()
        score = 0  # neutro
        if any(kw in user_input for kw in positive_keywords):
            score = 1
        elif any(kw in user_input for kw in negative_keywords):
            score = -1
        if any(kw in user_input for kw in urgent_keywords):
            score = -2  # molto negativo/urgente
        sentiment_scores.append(score)
    
    kpi['sentiment_medio'] = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0
    
    # 6. EFFICIENZA REINDIRIZZAMENTO
    # Capacità di deviare casi non urgenti verso strutture territoriali invece del PS
    non_urgent_to_territorial = 0
    non_urgent_to_ps = 0
    
    for r in datastore.records:
        urgency = r.get('urgenza', 3)
        if urgency <= 2:  # Non urgente
            bot_resp = str(r.get('bot_response', '')).lower()
            if any(kw in bot_resp for kw in ['cau', 'guardia medica', 'medico di base']):
                non_urgent_to_territorial += 1
            elif any(kw in bot_resp for kw in ['pronto soccorso', 'ps', '118']):
                non_urgent_to_ps += 1
    
    total_non_urgent = non_urgent_to_territorial + non_urgent_to_ps
    kpi['efficienza_reindirizzamento'] = (non_urgent_to_territorial / total_non_urgent * 100) if total_non_urgent > 0 else 0
    
    # 7. SESSIONI UNIVOCHE (già calcolato in calculate_kpi_volumetrici)
    kpi['sessioni_uniche'] = len(datastore.sessions)
    
    # 8. THROUGHPUT ORARIO (già calcolato in calculate_kpi_volumetrici)
    hours = [r.get('hour', 0) for r in datastore.records if r.get('hour') is not None]
    kpi['throughput_orario'] = Counter(hours)
    
    # 9. TEMPO MEDIANO DI TRIAGE (già calcolato in calculate_kpi_volumetrici)
    session_durations = []
    for sid, records in datastore.sessions.items():
        if len(records) >= 2:
            timestamps = [r.get('datetime') for r in records if r.get('datetime')]
            if len(timestamps) >= 2:
                duration = (max(timestamps) - min(timestamps)).total_seconds() / 60
                if duration < 120:
                    session_durations.append(duration)
    
    if session_durations:
        session_durations.sort()
        median_idx = len(session_durations) // 2
        kpi['tempo_mediano_triage_minuti'] = session_durations[median_idx]
    else:
        kpi['tempo_mediano_triage_minuti'] = 0
    
    # 10. TASSO DI DIVERGENZA ALGORITMICA
    # Misura quanto spesso l'AI suggerisce un esito diverso rispetto al sistema deterministico
    # Approssimazione: confronta urgenza AI con urgenza basata su keyword matching
    divergences = 0
    total_comparisons = 0
    
    for r in datastore.records:
        ai_urgency = r.get('urgenza', 3)
        # Calcola urgenza deterministica basata su keyword
        user_input = str(r.get('user_input', '')).lower()
        deterministic_urgency = 3  # default
        
        if any(kw in user_input for kw in RED_FLAGS_KEYWORDS):
            deterministic_urgency = 5
        elif any(kw in user_input for kw in ['dolore forte', 'sangue', 'svenimento']):
            deterministic_urgency = 4
        elif any(kw in user_input for kw in ['lieve', 'piccolo', 'niente']):
            deterministic_urgency = 2
        
        if abs(ai_urgency - deterministic_urgency) >= 2:  # Divergenza significativa
            divergences += 1
        total_comparisons += 1
    
    kpi['tasso_divergenza_algoritmica'] = (divergences / total_comparisons * 100) if total_comparisons > 0 else 0
    
    # 11. TASSO DI OMISSIONE RED FLAGS
    # Casi in cui sintomi critici menzionati non sono stati catturati
    red_flags_mentioned = 0
    red_flags_captured = 0
    
    for r in datastore.records:
        user_input = str(r.get('user_input', '')).lower()
        mentioned_rf = [rf for rf in RED_FLAGS_KEYWORDS if rf in user_input]
        if mentioned_rf:
            red_flags_mentioned += len(mentioned_rf)
            if r.get('has_red_flag', False):
                red_flags_captured += len(mentioned_rf)
    
    kpi['tasso_omissione_red_flags'] = ((red_flags_mentioned - red_flags_captured) / red_flags_mentioned * 100) if red_flags_mentioned > 0 else 0
    
    # 12. FUNNEL DROP-OFF
    # Identificazione dello step della chat in cui avviene la maggior parte degli abbandoni
    step_counts = defaultdict(int)
    for sid, records in datastore.sessions.items():
        if len(records) < 3:  # Sessione abbandonata precocemente
            step_counts['early_abandon'] += 1
        else:
            step_counts['completed'] += 1
    
    kpi['funnel_dropoff'] = {
        'early_abandon': step_counts['early_abandon'],
        'completed': step_counts['completed'],
        'dropoff_rate': (step_counts['early_abandon'] / len(datastore.sessions) * 100) if datastore.sessions else 0
    }
    
    # 13. INDICE DI ESITAZIONE
    # Misura del tempo di risposta dell'utente alle domande del bot
    # Approssimazione: tempo tra bot_response e user_input successivo
    hesitation_times = []
    for sid, records in datastore.sessions.items():
        sorted_records = sorted([r for r in records if r.get('datetime')], key=lambda x: x.get('datetime'))
        for i in range(len(sorted_records) - 1):
            if sorted_records[i].get('bot_response') and sorted_records[i+1].get('user_input'):
                time_diff = (sorted_records[i+1].get('datetime') - sorted_records[i].get('datetime')).total_seconds()
                if 5 < time_diff < 300:  # Tra 5 secondi e 5 minuti (escludi pause lunghe)
                    hesitation_times.append(time_diff)
    
    kpi['indice_esitazione_secondi'] = sum(hesitation_times) / len(hesitation_times) if hesitation_times else 0
    
    # 14. FAST TRACK EFFICIENCY RATIO
    # Rapporto di velocità tra gestione casi critici (codici rossi) e standard
    critical_durations = []
    standard_durations = []
    
    for sid, records in datastore.sessions.items():
        if len(records) >= 2:
            timestamps = [r.get('datetime') for r in records if r.get('datetime')]
            if len(timestamps) >= 2:
                duration = (max(timestamps) - min(timestamps)).total_seconds() / 60
                max_urgency = max([r.get('urgenza', 3) for r in records])
                
                if max_urgency >= 4:  # Critico
                    critical_durations.append(duration)
                else:
                    standard_durations.append(duration)
    
    avg_critical = sum(critical_durations) / len(critical_durations) if critical_durations else 0
    avg_standard = sum(standard_durations) / len(standard_durations) if standard_durations else 0
    
    kpi['fast_track_efficiency_ratio'] = (avg_standard / avg_critical) if avg_critical > 0 else 0
    
    # 15. COPERTURA GEOGRAFICA
    # Analisi della provenienza delle richieste rispetto alla densità delle strutture
    districts_count = Counter([r.get('distretto', 'UNKNOWN') for r in datastore.records])
    kpi['copertura_geografica'] = {
        'distretti_attivi': len([d for d in districts_count.values() if d > 0]),
        'distribuzione_distretti': dict(districts_count.most_common(10))
    }
    
    return kpi


# === INTEGRAZIONE DISTRETTI ===
def load_district_mapping() -> Dict:
    """Carica mapping distretti sanitari."""
    if not os.path.exists(DISTRICTS_FILE):
        st.warning(f"⚠️ File {DISTRICTS_FILE} non trovato.")
        return {"health_districts": [], "comune_to_district_mapping": {}}
    
    try:
        with open(DISTRICTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        st.error(f"❌ Errore caricamento distretti: {e}")
        return {"health_districts": [], "comune_to_district_mapping": {}}


def map_comune_to_district(comune: str, district_data: Dict) -> str:
    """Mappa comune a distretto sanitario."""
    if not comune or not district_data:
        return "UNKNOWN"
    
    mapping = district_data.get("comune_to_district_mapping", {})
    return mapping.get(comune.lower().strip(), "UNKNOWN")


# === EXPORT EXCEL ===
def export_to_excel(datastore: TriageDataStore, kpi_vol: Dict, kpi_clin: Dict, kpi_ctx: Dict) -> Optional[bytes]:
    """
    [DEPRECATED] Usa datastore.to_excel() invece.
    Mantenuta per retrocompatibilità.
    """
    return datastore.to_excel(kpi_vol, kpi_clin, kpi_ctx)

def _export_to_excel_legacy(datastore: TriageDataStore, kpi_vol: Dict, kpi_clin: Dict, kpi_ctx: Dict) -> Optional[bytes]:
    """
    Export professionale Excel con fogli separati.
    """
    if not XLSX_AVAILABLE:
        return None
    
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    
    # === FOGLIO 1: KPI AGGREGATI ===
    ws_kpi = workbook.add_worksheet('KPI Aggregati')
    
    # Formati
    header_format = workbook.add_format({'bold': True, 'bg_color': '#4472C4', 'font_color': 'white'})
    number_format = workbook.add_format({'num_format': '0.00'})
    
    row = 0
    ws_kpi.write(row, 0, 'Categoria', header_format)
    ws_kpi.write(row, 1, 'Metrica', header_format)
    ws_kpi.write(row, 2, 'Valore', header_format)
    row += 1
    
    # KPI Volumetrici
    for key, val in kpi_vol.items():
        if key not in ['throughput_orario']:
            ws_kpi.write(row, 0, 'Volumetrico')
            ws_kpi.write(row, 1, key)
            ws_kpi.write(row, 2, val, number_format)
            row += 1
    
    # KPI Clinici
    ws_kpi.write(row, 0, 'Clinico')
    ws_kpi.write(row, 1, 'prevalenza_red_flags')
    ws_kpi.write(row, 2, kpi_clin['prevalenza_red_flags'], number_format)
    row += 1
    
    # KPI Context-Aware
    ws_kpi.write(row, 0, 'Context-Aware')
    ws_kpi.write(row, 1, 'tasso_deviazione_ps')
    ws_kpi.write(row, 2, kpi_ctx['tasso_deviazione_ps'], number_format)
    row += 1
    
    # === FOGLIO 2: DATI GREZZI ===
    ws_raw = workbook.add_worksheet('Dati Grezzi')
    
    headers = ['Timestamp', 'Session ID', 'User Input', 'Bot Response', 'Urgenza', 'Area Clinica', 'Red Flags']
    for col, header in enumerate(headers):
        ws_raw.write(0, col, header, header_format)
    
    for row_idx, record in enumerate(datastore.records, 1):
        ws_raw.write(row_idx, 0, str(record.get('timestamp', '')))
        ws_raw.write(row_idx, 1, str(record.get('session_id', '')))
        ws_raw.write(row_idx, 2, str(record.get('user_input', ''))[:100])
        ws_raw.write(row_idx, 3, str(record.get('bot_response', ''))[:100])
        ws_raw.write(row_idx, 4, record.get('urgenza', 3))
        ws_raw.write(row_idx, 5, record.get('area_clinica', 'N/D'))
        ws_raw.write(row_idx, 6, ', '.join(record.get('red_flags', [])))
    
    workbook.close()
    output.seek(0)
    return output.getvalue()


# === VISUALIZZAZIONI PLOTLY (Aggiornate v2.1 - Gennaio 2026) ===

def render_throughput_chart(kpi_vol: Dict):
    """Grafico throughput orario con protezione zero-data."""
    throughput = kpi_vol.get('throughput_orario', {})
    if not throughput or len(throughput) == 0:
        st.info("ℹ️ Nessun dato disponibile per throughput orario.")
        return
    
    hours = sorted(throughput.keys())
    counts = [throughput[h] for h in hours]
    
    fig = go.Figure(data=[
        go.Bar(
            x=hours, 
            y=counts, 
            marker_color='#4A90E2',
            hovertemplate='<b>Ora %{x}:00</b><br>Accessi: %{y}<extra></extra>'
        )
    ])
    
    fig.update_layout(
        title="Throughput Orario (Distribuzione Accessi)",
        xaxis_title="Ora del Giorno",
        yaxis_title="N° Interazioni",
        height=400,
        hovermode='x unified',
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(family="Arial, sans-serif", size=12)
    )
    
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='#e5e7eb')
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='#e5e7eb')
    
    st.plotly_chart(fig, use_container_width=True)


def render_urgenza_pie(kpi_clin: Dict):
    """Grafico urgenza con protezione zero-data."""
    stratificazione = kpi_clin.get('stratificazione_urgenza', {})
    if not stratificazione or len(stratificazione) == 0:
        st.info("ℹ️ Nessun dato disponibile per stratificazione urgenza.")
        return
    """Grafico a torta stratificazione urgenza."""
    stratificazione = kpi_clin.get('stratificazione_urgenza', {})
    if not stratificazione:
        st.info("Nessun dato disponibile per stratificazione urgenza.")
        return
    
    labels = [f"Codice {k}" for k in sorted(stratificazione.keys())]
    values = [stratificazione[k] for k in sorted(stratificazione.keys())]
    
    # Palette colori clinica standard ER
    colors = ['#00C853', '#FFEB3B', '#FF9800', '#FF5722', '#B71C1C']
    
    fig = go.Figure(data=[
        go.Pie(
            labels=labels, 
            values=values, 
            marker_colors=colors[:len(labels)],
            hovertemplate='<b>%{label}</b><br>Casi: %{value}<br>Percentuale: %{percent}<extra></extra>',
            textinfo='label+percent',
            textposition='auto'
        )
    ])
    
    fig.update_layout(
        title="Stratificazione Urgenza (Codici 1-5)",
        height=400,
        showlegend=True,
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(family="Arial, sans-serif", size=12)
    )
    
    st.plotly_chart(fig, use_container_width=True)

def render_sintomi_table(kpi_clin: Dict):
    """Tabella spettro sintomi completo (NON troncato)."""
    spettro = kpi_clin.get('spettro_sintomi', {})
    if not spettro:
        st.info("Nessun sintomo rilevato nei dati.")
        return
    
    st.subheader("📋 Spettro Sintomatologico Completo")
    
    # Converti in lista ordinata
    sintomi_list = sorted(spettro.items(), key=lambda x: x[1], reverse=True)
    
    # Rendering tabella
    st.dataframe(
        {
            'Sintomo': [s[0].title() for s in sintomi_list],
            'Frequenza': [s[1] for s in sintomi_list]
        },
        use_container_width=True,  # <--- CORRETTO: Sostituito use_container_width=True
        height=400
    )
# === MAIN APPLICATION ===
def render_dashboard(log_file_path: str = None):
    """
    Renderizza dashboard analytics completo.
    V4.0: Funzione modularizzata per essere importata da frontend.py.
    
    Args:
        log_file_path: Path al file JSONL (opzionale, usa Supabase di default)
    """
    # Usa il path centralizzato se fornito, altrimenti usa il default
    global LOG_FILE
    if log_file_path:
        LOG_FILE = log_file_path
    
    # CRITICAL: Mostra warning xlsxwriter QUI, non nel global scope
    if not XLSX_AVAILABLE:
        st.warning("⚠️ xlsxwriter non disponibile. Export Excel disabilitato.\nInstalla con: `pip install xlsxwriter`")
    
    # Backend Refresh: Invalida cache ad ogni caricamento pagina per garantire dati freschi
    # Questo assicura che le nuove chat siano visibili in tempo reale
    cache_key = str(Path(LOG_FILE).absolute())
    if cache_key in _FILE_CACHE:
        del _FILE_CACHE[cache_key]
    
    # Carica dati con gestione errori robusta
    try:
        datastore = TriageDataStore(LOG_FILE)
        # Forza ricaricamento dati per garantire sincronizzazione
        datastore.reload_if_updated()
    except Exception as e:
        st.error(f"❌ Errore fatale durante caricamento dati: {e}")
        st.info("💡 Verifica che il file `triage_logs.jsonl` sia valido o rimuovilo per ripartire da zero.")
        return
    
    # FIX BUG SCOPE: Inizializza filtered_datastore immediatamente dopo datastore
    # Questo garantisce che la variabile esista sempre, anche se i filtri falliscono
    filtered_datastore = datastore
    
    try:
        district_data = load_district_mapping()
    except Exception as e:
        st.warning(f"⚠️ Errore caricamento distretti: {e}")
        district_data = {"health_districts": [], "comune_to_district_mapping": {}}
    
    # V6.0: Inizializza file log se non esiste
    if not os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                pass  # Crea file vuoto
            st.info("💡 File log creato. Inizia una chat per popolare i dati.")
        except Exception as e:
            st.error(f"❌ Errore creazione file log: {e}")
    
    # Early return se nessun dato
    if not datastore.records:
        st.warning("⚠️ Nessun dato disponibile. Inizia una chat per popolare i log.")
        st.info("💡 Avvia il **Chatbot Triage** tramite `app.py` per generare dati di triage.")
        return
    
    # FIX BUG SCOPE: filtered_datastore già inizializzato sopra, ma aggiorniamo qui se necessario
    
    # === TOP HEADER NAVIGATION: FILTRI ===
    st.markdown("---")
    
    # Row 1: Filtri Temporali
    col_temp1, col_temp2, col_temp3, col_temp4 = st.columns([2, 2, 2, 2])
    
    with col_temp1:
        # Filtro Anno/Mese (Aggregazione automatica)
        years = datastore.get_unique_values('year')
        all_years = sorted(set(years + [2025, 2026]), reverse=True)
        sel_year = st.selectbox("📅 Anno", all_years, key="header_year") if all_years else None
        
        month_names = {
            1: "Gennaio", 2: "Febbraio", 3: "Marzo", 4: "Aprile",
            5: "Maggio", 6: "Giugno", 7: "Luglio", 8: "Agosto",
            9: "Settembre", 10: "Ottobre", 11: "Novembre", 12: "Dicembre"
        }
        
        months_available = []
        if sel_year:
            filtered_by_year = datastore.filter(year=sel_year)
            months_available = filtered_by_year.get_unique_values('month')
        
        month_options = ['Tutti']
        for m in range(1, 13):
            month_label = f"{m:02d} - {month_names.get(m, 'Mese')}"
            if m in months_available:
                month_options.append(month_label)
            else:
                month_options.append(f"{month_label} (0 dati)")
        
        sel_month = st.selectbox("📆 Mese", month_options, key="header_month")
        sel_month = None if sel_month == 'Tutti' else int(sel_month.split(' - ')[0])
    
    with col_temp2:
        # Filtro Date Range (Dal / Al)
        date_from = st.date_input("📥 Dal", value=None, key="header_date_from")
        date_to = st.date_input("📤 Al", value=None, key="header_date_to")
    
    with col_temp3:
        # Cascading Geografico: AUSL → Distretto
        ausl_options = ['Tutti']
        ausl_to_districts = {}
        
        if district_data and 'health_districts' in district_data:
            for ausl_item in district_data['health_districts']:
                ausl_name = ausl_item.get('ausl', '')
                if ausl_name:
                    ausl_options.append(ausl_name)
                    ausl_to_districts[ausl_name] = []
                    if 'districts' in ausl_item:
                        for d in ausl_item['districts']:
                            if 'name' in d:
                                ausl_to_districts[ausl_name].append(d['name'])
        
        sel_ausl = st.selectbox("🏥 AUSL", ausl_options, key="header_ausl")
        
        # Distretto popolato dinamicamente in base ad AUSL
        district_options = ['Tutti']
        if sel_ausl != 'Tutti' and sel_ausl in ausl_to_districts:
            district_options.extend(sorted(ausl_to_districts[sel_ausl]))
        
        sel_district = st.selectbox("📍 Distretto", district_options, key="header_district")
        sel_district = None if sel_district == 'Tutti' else sel_district
    
    with col_temp4:
        # Export Dati
        st.markdown("### 📥 Export")
        
        if XLSX_AVAILABLE and datastore.records:
            try:
                # Pre-calcola KPI per export
                kpi_vol = calculate_kpi_volumetrici(filtered_datastore)
                kpi_clin = calculate_kpi_clinici(filtered_datastore)
                kpi_ctx = calculate_kpi_context_aware(filtered_datastore)
                kpi_completo = calculate_kpi_completo(filtered_datastore)
                
                # Formatta date per titolo
                date_from_str = date_from.strftime('%Y-%m-%d') if date_from else None
                date_to_str = date_to.strftime('%Y-%m-%d') if date_to else None
                
                excel_data = filtered_datastore.to_excel(
                    kpi_vol, kpi_clin, kpi_ctx, kpi_completo, 
                    sel_district, date_from_str, date_to_str
                )
                csv_data = datastore.to_csv(include_enriched=True)
                
                if csv_data:
                    st.download_button(
                        label="📄 CSV",
                        data=csv_data,
                        file_name=f"Report_Triage_{sel_year or 'ALL'}.csv",
                        mime="text/csv",
                        key="header_csv"
                    )
                
                if excel_data:
                    st.download_button(
                        label="📊 Excel",
                        data=excel_data,
                        file_name=f"Report_Triage_{sel_year or 'ALL'}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="header_excel"
                    )
            except Exception as e:
                st.error(f"❌ Errore export: {e}")
    
    st.markdown("---")
    
    # Applica filtri temporali e geografici (filtered_datastore già inizializzato sopra)
    filtered_datastore = datastore.filter(year=sel_year, month=sel_month, district=sel_district)
    
    # Filtro per date range se specificato
    if date_from or date_to:
        filtered_records = []
        for record in filtered_datastore.records:
            record_date = record.get('date')
            if record_date:
                if date_from and record_date < date_from:
                    continue
                if date_to and record_date > date_to:
                    continue
                filtered_records.append(record)
        filtered_datastore.records = filtered_records
        # Ricostruisci sessions
        filtered_datastore.sessions = {}
        for record in filtered_records:
            sid = record.get('session_id')
            if sid:
                if sid not in filtered_datastore.sessions:
                    filtered_datastore.sessions[sid] = []
                filtered_datastore.sessions[sid].append(record)
    
    # Empty State: Se nessun dato disponibile per i filtri
    if not filtered_datastore.records:
        st.warning("⚠️ Nessun dato disponibile per i filtri selezionati.")
        st.info("💡 Prova a modificare i filtri temporali o geografici.")
        return
    
    # === INLINE CSS (No external imports) ===
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    * { font-family: 'Inter', sans-serif !important; }
    .stApp { background-color: #f8fafc; }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)
    
    # === MAIN DASHBOARD ===
    st.title("🧬 SIRAYA Analytics | Dashboard Professionale")
    st.caption(f"📊 Dati: {len(filtered_datastore.records)} interazioni | {len(filtered_datastore.sessions)} sessioni")
    
    if not filtered_datastore.records:
        st.info("ℹ️ Nessun dato disponibile per i filtri selezionati.")
        return
    
    # === LIVE CRITICAL ALERTS ===
    st.markdown("### 🚨 Live Critical Alerts")
    
    # Get records from last hour
    from datetime import datetime, timedelta
    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)
    
    critical_records = []
    for record in datastore.records:
        try:
            ts_str = record.get('timestamp', '')
            # Parse ISO timestamp
            if ts_str:
                # Handle different timestamp formats
                if 'T' in ts_str:
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                else:
                    ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
                
                if ts >= one_hour_ago:
                    urgency_level = record.get('urgency_level', '').upper()
                    if urgency_level in ['ROSSO', 'ARANCIONE', 'RED', 'ORANGE']:
                        critical_records.append({
                            'timestamp': ts,
                            'session_id': record.get('session_id', 'N/D'),
                            'urgency': urgency_level,
                            'comune': record.get('comune', 'N/D'),
                            'chief_complaint': record.get('chief_complaint', 'N/D')[:100]
                        })
        except Exception:
            continue
    
    if critical_records:
        # Sort by timestamp (most recent first)
        critical_records.sort(key=lambda x: x['timestamp'], reverse=True)
        
        # Display in alert box
        st.markdown(f"""
        <div style='background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%); 
                    border-left: 4px solid #dc2626; 
                    border-radius: 12px; 
                    padding: 20px; 
                    margin-bottom: 20px;
                    box-shadow: 0 2px 8px rgba(220, 38, 38, 0.1);'>
            <h4 style='margin: 0 0 10px 0; color: #991b1b;'>
                ⚠️ {len(critical_records)} Casi Critici (Ultima Ora)
            </h4>
        </div>
        """, unsafe_allow_html=True)
        
        # Show details in expander
        with st.expander("📋 Dettagli Casi Critici", expanded=False):
            for i, rec in enumerate(critical_records[:10], 1):  # Max 10 most recent
                ts_str = rec['timestamp'].strftime('%H:%M:%S')
                urgency_emoji = "🔴" if rec['urgency'] in ['ROSSO', 'RED'] else "🟠"
                
                st.markdown(f"""
                **{urgency_emoji} Caso {i}** - {ts_str}  
                - **Sessione**: `{rec['session_id']}`  
                - **Comune**: {rec['comune']}  
                - **Sintomo**: {rec['chief_complaint']}...
                """)
                st.divider()
    else:
        st.success("✅ Nessun caso critico nell'ultima ora")
    
    st.markdown("---")
    
    # === KPI DISPLAY (All KPIs shown by default) ===
    
    # === CALCOLO KPI CON PROTEZIONE ERRORI ===
    try:
        kpi_vol = calculate_kpi_volumetrici(filtered_datastore)
    except Exception as e:
        st.error(f"❌ Errore calcolo KPI volumetrici: {e}")
        kpi_vol = {'sessioni_uniche': 0, 'interazioni_totali': 0, 'completion_rate': 0, 
                   'tempo_mediano_minuti': 0, 'profondita_media': 0, 'throughput_orario': {}}
    
    try:
        kpi_clin = calculate_kpi_clinici(filtered_datastore)
    except Exception as e:
        st.error(f"❌ Errore calcolo KPI clinici: {e}")
        kpi_clin = {'spettro_sintomi': {}, 'stratificazione_urgenza': {}, 
                    'prevalenza_red_flags': 0, 'red_flags_dettaglio': {}}
    
    try:
        kpi_ctx = calculate_kpi_context_aware(filtered_datastore)
    except Exception as e:
        st.error(f"❌ Errore calcolo KPI context-aware: {e}")
        kpi_ctx = {'urgenza_media_per_spec': {}, 'tasso_deviazione_ps': 0, 
                   'tasso_deviazione_territoriale': 0}
    
    # === CALCOLO KPI COMPLETO (15 KPI AVANZATI) ===
    try:
        kpi_completo = calculate_kpi_completo(filtered_datastore)
    except Exception as e:
        st.error(f"❌ Errore calcolo KPI completo: {e}")
        kpi_completo = {}
    
    # === SEZIONE 1: KPI VOLUMETRICI ===
    st.header("📈 KPI Volumetrici")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.metric("Sessioni Uniche", f"{kpi_vol['sessioni_uniche']}")
    
    with col2:
        st.metric("Interazioni Totali", f"{kpi_vol['interazioni_totali']}")
    
    with col3:
        st.metric("Completion Rate", f"{kpi_vol['completion_rate']:.1f}%")
    
    with col4:
        st.metric("Tempo Mediano", f"{kpi_vol['tempo_mediano_minuti']:.1f} min")
    
    with col5:
        st.metric("Profondità Media", f"{kpi_vol['profondita_media']:.1f}")
    
    st.divider()
    
    # Throughput Orario (con gestione errori)
    try:
        render_throughput_chart(kpi_vol)
    except Exception as e:
        st.error(f"❌ Errore rendering throughput: {e}")
    
    # === SEZIONE 2: KPI CLINICI ===
    st.header("🏥 KPI Clinici ed Epidemiologici")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.metric("Prevalenza Red Flags", f"{kpi_clin.get('prevalenza_red_flags', 0):.1f}%")
        
        # Red Flags Dettaglio
        if kpi_clin.get('red_flags_dettaglio'):
            st.subheader("🚨 Red Flags per Tipo")
            rf_list = sorted(kpi_clin['red_flags_dettaglio'].items(), key=lambda x: x[1], reverse=True)
            for rf, count in rf_list[:10]:
                st.write(f"**{rf.title()}**: {count}")
    
    with col2:
        try:
            render_urgenza_pie(kpi_clin)
        except Exception as e:
            st.error(f"❌ Errore rendering urgenza: {e}")
    
    st.divider()
    
    # Spettro Sintomi Completo (con gestione errori)
    try:
        render_sintomi_table(kpi_clin)
    except Exception as e:
        st.error(f"❌ Errore rendering sintomi: {e}")
    
    # === SEZIONE 3: KPI CONTEXT-AWARE ===
    st.header("🎯 KPI Context-Aware")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.metric("Deviazione Pronto Soccorso", f"{kpi_ctx['tasso_deviazione_ps']:.1f}%")
        st.metric("Deviazione Territoriale", f"{kpi_ctx['tasso_deviazione_territoriale']:.1f}%")
    
    with col2:
        # Urgenza Media per Specializzazione
        st.subheader("⚕️ Urgenza Media per Specializzazione")
        urgenza_spec = kpi_ctx.get('urgenza_media_per_spec', {})
        if urgenza_spec:
            sorted_spec = sorted(urgenza_spec.items(), key=lambda x: x[1], reverse=True)
            for spec, urg in sorted_spec:
                st.write(f"**{spec}**: {urg:.2f}")
    
    # === FOOTER ===
    st.divider()
    st.caption("SIRAYA Health Navigator V4.0 | Analytics Engine | Supabase-Powered")


def main(log_file_path: str = None):
    """
    Legacy main function - wrappa render_dashboard per compatibilità.
    Usa questa solo quando backend.py viene eseguito standalone.
    """
    render_dashboard(log_file_path)


# === ENTRY POINT ===
if __name__ == "__main__":
    render_dashboard()
