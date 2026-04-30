import json
import difflib
from pathlib import Path
from collections import defaultdict
import csv

KB_FILE_PATH = "master_kb.json"
REPORT_DIR = Path("audit_reports")
REPORT_DIR.mkdir(exist_ok=True)

# Servizi "canonici" approvati
CANONICAL_SERVICES = {
    "EMERGENCY": [
        "Pronto Soccorso Generale",
        "Terapia Intensiva",
        "Rianimazione e Stabilizzazione",
        "Shock Room",
    ],
    "CARDIOLOGY": [
        "Cardiologia",
        "Cardiochirurgia",
        "Emodinamica",
    ],
    "PEDIATRICS": [
        "Pediatria Generale",
        "Pediatria d'urgenza",
        "Neonatologia",
    ],
    "PSYCHIATRY": [
        "Assistenza Psicologica",
        "Psichiatria",
    ],
    "ADDICTION": [
        "Trattamento Dipendenze",
        "Disintossicazione",
    ],
    "GENERAL": [
        "Medicina Generale",
        "Chirurgia Generale",
        "Ortopedia",
        "Ginecologia",
        "Urologia",
    ]
}

def get_all_canonical() -> list:
    """Estrai lista flat di tutti i servizi canonici"""
    result = []
    for category, services in CANONICAL_SERVICES.items():
        result.extend(services)
    return result

def fuzzy_find_canonical(service: str, threshold: float = 0.75) -> tuple:
    """
    Trova il miglior match nel canonical dictionary.
    Ritorna (canonical_service, confidence_score)
    """
    canonical_list = get_all_canonical()
    best_match = None
    best_score = 0
    
    service_lower = service.lower().strip()
    
    for canonical in canonical_list:
        score = difflib.SequenceMatcher(None, service_lower, canonical.lower()).ratio()
        if score > best_score:
            best_score = score
            best_match = canonical
    
    if best_score >= threshold:
        return best_match, best_score
    else:
        return None, best_score

def interactive_cleanup():
    """
    Interfaccia interattiva per pulire i servizi.
    Chiede conferma per ogni mapping.
    """
    
    # Carica KB
    with open(KB_FILE_PATH, 'r', encoding='utf-8') as f:
        kb_data = json.load(f)
    
    # Estrai tutti i servizi unici dal KB
    all_services = set()
    for facility in kb_data.get("facilities", []):
        for service in facility.get("servizi_disponibili", []):
            all_services.add(service.strip())
    
    # Filtra i "rari" (< 5 occorrenze)
    service_frequency = defaultdict(int)
    for facility in kb_data.get("facilities", []):
        for service in facility.get("servizi_disponibili", []):
            service_frequency[service.strip()] += 1
    
    rare_services = sorted([s for s in all_services if service_frequency[s] < 5])
    
    print(f"\n🔍 Trovati {len(rare_services)} servizi rari (<5 occorrenze)")
    print(f"Iniziamo il cleanup interattivo...\n")
    
    mapping = {}  # servizio_sporco -> servizio_pulito
    
    for i, service in enumerate(rare_services, 1):
        print(f"\n{'='*70}")
        print(f"[{i}/{len(rare_services)}] Servizio: '{service}'")
        print(f"               Offerto da: {service_frequency[service]} strutture")
        print(f"{'='*70}")
        
        # Suggerisci match
        canonical, score = fuzzy_find_canonical(service, threshold=0.7)
        
        if canonical:
            print(f"   💡 Suggerimento: '{canonical}' (confidence: {score:.1%})")
            print(f"\n   Opzioni:")
            print(f"   [1] Accetta suggerimento")
            print(f"   [2] Scegli manualmente")
            print(f"   [3] Salta (mantieni originale)")
            
            choice = input(f"\n   La tua scelta (1/2/3): ").strip()
            
            if choice == "1":
                mapping[service] = canonical
                print(f"   ✅ MAPPATO: '{service}' → '{canonical}'")
            
            elif choice == "2":
                print(f"\n   📋 Servizi disponibili:")
                canonical_list = get_all_canonical()
                for j, c in enumerate(canonical_list, 1):
                    print(f"      [{j:2d}] {c}")
                
                try:
                    choice_num = int(input(f"\n   Seleziona numero (1-{len(canonical_list)}): ").strip())
                    if 1 <= choice_num <= len(canonical_list):
                        target_service = canonical_list[choice_num - 1]
                        mapping[service] = target_service
                        print(f"   ✅ MAPPATO: '{service}' → '{target_service}'")
                    else:
                        print(f"   ⚠️  Numero non valido. Skipped.")
                except ValueError:
                    print(f"   ⚠️  Input non valido. Skipped.")
            
            elif choice == "3":
                print(f"   ⏭️  SKIPPED: Mantieni originale")
            
            else:
                print(f"   ⚠️  Scelta non riconosciuta. Skipped.")
        
        else:
            print(f"   ❌ Nessun suggerimento automatico (< 70% confidence)")
            print(f"\n   Opzioni:")
            print(f"   [1] Scegli manualmente")
            print(f"   [2] Salta (mantieni originale)")
            
            choice = input(f"\n   La tua scelta (1/2): ").strip()
            
            if choice == "1":
                print(f"\n   📋 Servizi disponibili:")
                canonical_list = get_all_canonical()
                for j, c in enumerate(canonical_list, 1):
                    print(f"      [{j:2d}] {c}")
                
                try:
                    choice_num = int(input(f"\n   Seleziona numero (1-{len(canonical_list)}): ").strip())
                    if 1 <= choice_num <= len(canonical_list):
                        target_service = canonical_list[choice_num - 1]
                        mapping[service] = target_service
                        print(f"   ✅ MAPPATO: '{service}' → '{target_service}'")
                    else:
                        print(f"   ⚠️  Numero non valido. Skipped.")
                except ValueError:
                    print(f"   ⚠️  Input non valido. Skipped.")
            
            elif choice == "2":
                print(f"   ⏭️  SKIPPED: Mantieni originale")
            
            else:
                print(f"   ⚠️  Scelta non riconosciuta. Skipped.")
    
    return mapping

def apply_mapping(mapping: dict, kb_data: dict) -> dict:
    """
    Applica il mapping al KB.
    Sostituisce i servizi "sporchi" con i canonici.
    """
    for facility in kb_data.get("facilities", []):
        original_services = facility.get("servizi_disponibili", [])
        normalized_services = []
        
        for service in original_services:
            service_clean = service.strip()
            if service_clean in mapping:
                normalized_services.append(mapping[service_clean])
            else:
                normalized_services.append(service_clean)
        
        # Rimuovi duplicati mantenendo ordine
        facility["servizi_disponibili"] = list(dict.fromkeys(normalized_services))
    
    return kb_data

def save_mapping_csv(mapping: dict):
    """Salva il mapping in CSV per audit trail"""
    output_path = REPORT_DIR / "service_mapping.csv"
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Servizio Originale", "Servizio Canonico"])
        for old, new in sorted(mapping.items()):
            writer.writerow([old, new])
    
    print(f"\n✅ Audit trail salvato: {output_path}")

if __name__ == "__main__":
    try:
        # Step 1: Cleanup interattivo
        mapping = interactive_cleanup()
        
        if not mapping:
            print("\n\n❌ Nessun mapping creato. Esci senza salvare.")
            exit(0)
        
        print(f"\n\n{'='*70}")
        print(f"📊 SUMMARY: {len(mapping)} servizi mappati")
        print(f"{'='*70}")
        for old, new in sorted(mapping.items()):
            print(f"   {old:40s} → {new}")
        
        # Step 2: Chiedi conferma prima di salvare
        print(f"\n{'='*70}")
        confirm = input("Applicare i cambiamenti a master_kb.json? (S/N): ").strip().lower()
        print(f"{'='*70}")
        
        if confirm != 's':
            print("❌ Annullato. master_kb.json NON modificato.")
            exit(0)
        
        # Step 3: Carica KB e applica
        with open(KB_FILE_PATH, 'r', encoding='utf-8') as f:
            kb_data = json.load(f)
        
        kb_data = apply_mapping(mapping, kb_data)
        
        # Step 4: Salva KB pulito (sovrascrivi master_kb.json)
        with open(KB_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(kb_data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ master_kb.json aggiornato!")
        
        # Step 5: Salva mapping CSV per audit trail
        save_mapping_csv(mapping)
        
        print("\n" + "="*70)
        print("✨ Cleanup completato con successo!")
        print("="*70)
        print(f"\n📝 Cosa è stato fatto:")
        print(f"   1. ✅ master_kb.json normalizzato e salvato")
        print(f"   2. ✅ audit_reports/service_mapping.csv creato (audit trail)")
        print(f"\n🚀 Prossimi step:")
        print(f"   1. Verifica le modifiche: git diff master_kb.json")
        print(f"   2. Se OK: git add master_kb.json")
        print(f"   3. git commit -m 'chore: normalize services in master_kb.json'")
        print(f"   4. git push")
        
    except FileNotFoundError:
        print(f"❌ Errore: master_kb.json non trovato nella cartella corrente.")
    except json.JSONDecodeError:
        print(f"❌ Errore: master_kb.json non è un JSON valido.")
    except Exception as e:
        print(f"❌ Errore inaspettato: {e}")
        import traceback
        traceback.print_exc()