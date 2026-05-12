#!/usr/bin/env python3
"""
Master KB Editor - Editor interattivo per master_kb.json
Modifica note e servizi disponibili per ogni struttura
CON SUPPORTO PER TORNARE INDIETRO, SALTARE A STRUTTURA X, E DEBUG SALVATAGGIO
"""

import json
import os
from pathlib import Path
import sys
import shutil


def load_master_kb():
    """Carica il file master_kb.json dalla cartella corrente"""
    kb_path = Path('master_kb.json')
    
    print(f"\n📁 Percorso ricercato: {kb_path.absolute()}")
    print(f"✅ File esiste: {kb_path.exists()}")
    
    if not kb_path.exists():
        print("❌ Errore: File 'master_kb.json' non trovato nella cartella corrente!")
        print(f"   Cercato in: {kb_path.absolute()}")
        sys.exit(1)
    
    try:
        with open(kb_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f"✅ File caricato: {len(data.get('facilities', []))} strutture trovate")
            return data
    except json.JSONDecodeError as e:
        print(f"❌ Errore nel parsing del JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Errore nella lettura del file: {e}")
        sys.exit(1)


def save_master_kb(data):
    """Salva il file master_kb.json aggiornato - CON DEBUG E BACKUP"""
    kb_path = Path('master_kb.json')
    
    print(f"\n" + "="*80)
    print(f"💾 SALVATAGGIO IN CORSO...")
    print(f"="*80)
    print(f"📁 Percorso: {kb_path.absolute()}")
    print(f"✅ File esiste prima: {kb_path.exists()}")
    
    try:
        # Crea backup
        backup_count = len(list(Path(".").glob("master_kb_backup_*.json")))
        backup_path = Path(f'master_kb_backup_{backup_count + 1}.json')
        
        if kb_path.exists():
            shutil.copy(kb_path, backup_path)
            print(f"📦 Backup creato: {backup_path.name}")
        
        # Scrivi il file
        print(f"\n📝 Scrittura file in corso...")
        with open(kb_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        
        # Verifica che il file sia stato scritto
        print(f"🔍 Verifica salvataggio...")
        file_exists = kb_path.exists()
        file_size = kb_path.stat().st_size if file_exists else 0
        
        print(f"✅ File scritto: {file_exists}")
        print(f"📊 Dimensione file: {file_size} bytes")
        print(f"📁 Posizione: {kb_path.absolute()}")
        
        # Verifica contenuto
        with open(kb_path, 'r', encoding='utf-8') as f:
            verificato = json.load(f)
            num_facilities = len(verificato.get('facilities', []))
            print(f"✅ Verifica contenuto: {num_facilities} strutture nel file salvato")
        
        print(f"="*80)
        print(f"✅ SALVATAGGIO COMPLETATO CON SUCCESSO!")
        print(f"="*80)
        return True
        
    except PermissionError as e:
        print(f"❌ ERRORE: Permesso negato!")
        print(f"   Il file potrebbe essere aperto in un altro programma.")
        print(f"   Errore: {e}")
        return False
    except IOError as e:
        print(f"❌ ERRORE I/O: Impossibile scrivere il file!")
        print(f"   Errore: {e}")
        return False
    except Exception as e:
        print(f"❌ Errore nel salvataggio: {e}")
        print(f"   Tipo errore: {type(e).__name__}")
        return False


def migrate_note_from_orari(facility):
    """
    Sposta la 'note' da orari al livello principale della struttura
    Se la nota esiste già a livello principale, non sovrascrive
    """
    orari = facility.get('orari', {})
    nota_da_orari = orari.pop('note', None)
    
    if nota_da_orari and 'note' not in facility:
        facility['note'] = nota_da_orari
        print(f"📝 Nota migrata da orari: {nota_da_orari[:50]}...")
    elif nota_da_orari and 'note' in facility:
        if not facility['note'] and nota_da_orari:
            facility['note'] = nota_da_orari


def display_facility_header(facility, index, total):
    """Mostra l'intestazione della struttura"""
    print("\n" + "="*80)
    print(f"[{index}/{total}] 📍 STRUTTURA: {facility['nome']}")
    print(f"     ID: {facility['id']}")
    print(f"     Tipo: {facility['tipologia']}")
    print(f"     Comune: {facility['comune']}")
    print("="*80)


def edit_note(facility):
    """Modifica la nota della struttura"""
    print("\n📝 SEZIONE NOTE")
    print("-" * 60)
    
    current_note = facility.get('note', '')
    print(f"Nota attuale: {current_note if current_note else '(vuoto)'}\n")
    
    while True:
        choice = input("Vuoi modificare la nota? [S/n/i/e]: ").strip().lower()
        
        if choice == 'e':
            print("❌ Uscita immediata senza salvare!")
            return 'exit'
        elif choice == 'i':
            print("⏹️  Annullo e torno al menu principale")
            return 'skip'
        elif choice == 'n' or choice == '':
            print("⏭️  Nota non modificata")
            return 'continue'
        elif choice == 's':
            new_note = input("\nInserisci la nuova nota (premi Enter per lasciare vuoto):\n> ").strip()
            facility['note'] = new_note
            print(f"✅ Nota aggiornata! Lunghezza: {len(new_note)} caratteri")
            print(f"   Contenuto: {new_note[:50]}..." if len(new_note) > 50 else f"   Contenuto: {new_note}")
            return 'continue'
        else:
            print("❌ Opzione non valida. Prova [S/n/i/e]")


def display_services_table(services, notes_dict=None):
    """Mostra i servizi in una tabella formattata"""
    if not notes_dict:
        notes_dict = {}
    
    print("\n📋 SERVIZI DISPONIBILI:\n")
    print("#   SERVIZIO                                          NOTA")
    print("─" * 80)
    
    for idx, service in enumerate(services, 1):
        note = notes_dict.get(service, '')
        note_preview = (note[:35] + "...") if len(note) > 35 else note
        print(f"{idx:<3} {service:<45} {note_preview}")
    
    print("─" * 80)


def edit_services(facility):
    """Modifica i servizi disponibili"""
    print("\n🔧 SEZIONE SERVIZI")
    print("-" * 60)
    
    services = facility.get('servizi_disponibili', [])
    notes_dict = facility.get('servizi_note', {})
    
    display_services_table(services, notes_dict)
    
    while True:
        choice = input("\nVuoi modificare i servizi offerti? [S/n/i/e]: ").strip().lower()
        
        if choice == 'e':
            print("❌ Uscita immediata senza salvare!")
            return 'exit'
        elif choice == 'i':
            print("⏹️  Annullo modifiche ai servizi e torno al menu principale")
            return 'skip'
        elif choice == 'n' or choice == '':
            print("⏭️  Servizi non modificati")
            return 'continue'
        elif choice == 's':
            break
        else:
            print("❌ Opzione non valida. Prova [S/n/i/e]")
    
    while True:
        print("\nOpzioni:")
        print("  [1] Aggiungere servizio")
        print("  [2] Rimuovere servizio")
        print("  [3] Modificare nota di un servizio")
        print("  [0] Finito")
        print("  [I] Torna indietro (annulla tutto)")
        print("  [E] Esci senza salvare")
        
        opzione = input("\nScelta: ").strip()
        
        if opzione == 'e' or opzione == 'E':
            print("❌ Uscita immediata senza salvare!")
            return 'exit'
        elif opzione == 'i' or opzione == 'I':
            print("⏹️  Annullo e torno al menu principale")
            return 'skip'
        elif opzione == '1':
            nuovo_servizio = input("Nome del nuovo servizio: ").strip()
            if nuovo_servizio and nuovo_servizio not in services:
                services.append(nuovo_servizio)
                if 'servizi_note' not in facility:
                    facility['servizi_note'] = {}
                facility['servizi_note'][nuovo_servizio] = ""
                print(f"✅ Servizio aggiunto: {nuovo_servizio}")
                display_services_table(services, facility.get('servizi_note', {}))
            else:
                print("❌ Servizio già esiste o nome vuoto")
        
        elif opzione == '2':
            display_services_table(services, notes_dict)
            try:
                idx = int(input("Numero del servizio da rimuovere (o [0] per annullare): ")) - 1
                if idx == -1:
                    print("❌ Operazione annullata")
                    continue
                if 0 <= idx < len(services):
                    servizio_rimosso = services.pop(idx)
                    if servizio_rimosso in notes_dict:
                        del notes_dict[servizio_rimosso]
                    print(f"✅ Servizio rimosso: {servizio_rimosso}")
                    display_services_table(services, notes_dict)
                else:
                    print("❌ Numero non valido")
            except ValueError:
                print("❌ Inserisci un numero valido")
        
        elif opzione == '3':
            display_services_table(services, notes_dict)
            try:
                idx = int(input("Numero del servizio (o [0] per annullare): ")) - 1
                if idx == -1:
                    print("❌ Operazione annullata")
                    continue
                if 0 <= idx < len(services):
                    servizio = services[idx]
                    print(f"\nServizio: {servizio}")
                    nota_attuale = notes_dict.get(servizio, '')
                    print(f"Nota attuale: {nota_attuale if nota_attuale else '(vuoto)'}")
                    
                    nuova_nota = input("Nuova nota (premi Enter per lasciare vuoto, o [i] per annullare):\n> ").strip()
                    
                    if nuova_nota.lower() == 'i':
                        print("❌ Operazione annullata")
                        continue
                    
                    notes_dict[servizio] = nuova_nota
                    print(f"✅ Nota aggiornata! Lunghezza: {len(nuova_nota)} caratteri")
                    display_services_table(services, notes_dict)
                else:
                    print("❌ Numero non valido")
            except ValueError:
                print("❌ Inserisci un numero valido")
        
        elif opzione == '0':
            print("✅ Modifiche ai servizi completate!")
            break
        else:
            print("❌ Opzione non valida")
    
    return 'continue'


def process_facilities(data, start_index=1):
    """Processa tutte le strutture a partire da start_index"""
    facilities = data.get('facilities', [])
    
    if not facilities:
        print("❌ Nessuna struttura trovata nel file!")
        return False
    
    print(f"\n🏥 Trovate {len(facilities)} strutture\n")
    
    for idx in range(start_index - 1, len(facilities)):
        facility = facilities[idx]
        fac_number = idx + 1
        
        migrate_note_from_orari(facility)
        
        display_facility_header(facility, fac_number, len(facilities))
        
        # Modifica nota
        nota_result = edit_note(facility)
        if nota_result == 'exit':
            return False
        elif nota_result == 'skip':
            print("⏭️  Saltando questa struttura...")
            continue
        
        # Modifica servizi
        servizi_result = edit_services(facility)
        if servizi_result == 'exit':
            return False
        elif servizi_result == 'skip':
            print("⏭️  Saltando questa struttura...")
            continue
        
        # Chiedi se continuare
        if fac_number < len(facilities):
            while True:
                print(f"\n📊 Struttura {fac_number}/{len(facilities)} completata")
                cont = input("⏭️  Continuare? [S/P/n/e]: ").strip().lower()
                
                if cont == 'e':
                    print("❌ Uscita immediata senza salvare!")
                    return False
                elif cont == 'n':
                    return True
                elif cont == 'p':
                    print("\n📍 Inserisci il numero della prossima struttura da modificare:")
                    try:
                        next_fac = int(input(f"Numero struttura [1-{len(facilities)}]: "))
                        if 1 <= next_fac <= len(facilities):
                            return process_facilities(data, next_fac)
                        else:
                            print(f"❌ Inserisci un numero tra 1 e {len(facilities)}")
                    except ValueError:
                        print("❌ Inserisci un numero valido")
                elif cont == 's' or cont == '':
                    break
                else:
                    print("❌ Prova [S/p/n/e]")
    
    return True


def display_summary(data):
    """Mostra un riassunto delle modifiche"""
    print("\n\n" + "="*80)
    print("📊 RIASSUNTO MODIFICHE")
    print("="*80)
    
    note_count = 0
    servizi_count = 0
    note_totali = 0
    
    for facility in data.get('facilities', []):
        if facility.get('note'):
            note_count += 1
            note_totali += 1
        
        servizi = facility.get('servizi_disponibili', [])
        if servizi:
            servizi_count += len(servizi)
        
        servizi_note = facility.get('servizi_note', {})
        for service, note in servizi_note.items():
            if note:
                note_totali += 1
    
    print(f"\n📈 TOTALI:")
    print(f"   Strutture con note: {note_count}")
    print(f"   Servizi totali: {servizi_count}")
    print(f"   Note complessive: {note_totali}")
    print("="*80)


def main():
    """Programma principale"""
    print("\n" + "="*80)
    print("🏥 MASTER KB EDITOR - Modifica Note e Servizi")
    print("🔧 VERSIONE CON DEBUG SALVATAGGIO")
    print("="*80)
    print("\n📌 COMANDI RAPIDI:")
    print("   [s] = Sì, continua")
    print("   [p] = Salta a struttura numero X")
    print("   [n] = No, fermi qui")
    print("   [e] = Esci senza salvare")
    print("   [i] = Torna indietro")
    print("="*80)
    
    print("\n📂 VERIFICA FILE...")
    data = load_master_kb()
    
    # Chiedi se riprendere da una struttura specifica
    print("\n🔍 Vuoi riprendere da una struttura specifica? [S/n]:")
    start_choice = input("> ").strip().lower()
    
    start_idx = 1
    if start_choice == 's' or start_choice == '':
        try:
            start_idx = int(input("Numero della struttura da cui iniziare: "))
            if start_idx < 1 or start_idx > len(data.get('facilities', [])):
                print(f"❌ Numero non valido! Inizio da 1")
                start_idx = 1
        except ValueError:
            print(f"❌ Numero non valido! Inizio da 1")
            start_idx = 1
    
    # Processa strutture
    should_save = process_facilities(data, start_idx)
    
    if should_save:
        display_summary(data)
        
        print("\n⏳ Tentativo di salvataggio...")
        if save_master_kb(data):
            print("\n✅ Programma completato!")
            print(f"📁 File salvato in: {Path('master_kb.json').absolute()}")
            print("\n💡 VERIFICA: Apri il file con Notepad per controllare le modifiche!")
        else:
            print("\n❌ Errore nel salvataggio! Controlla i permessi del file.")
    else:
        print("\n⏹️  Uscita senza salvare. Le modifiche NON sono state salvate.")


if __name__ == '__main__':
    main()