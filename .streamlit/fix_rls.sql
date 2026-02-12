-- =============================================================================
-- SIRAYA Health Navigator - Fix RLS Policy per triage_logs
-- =============================================================================
-- Questo script configura le Row Level Security (RLS) policies per la tabella
-- triage_logs in Supabase, permettendo l'inserimento e la lettura dei log.
--
-- ISTRUZIONI:
-- 1. Vai su Supabase Dashboard: https://supabase.com/dashboard
-- 2. Seleziona il tuo progetto
-- 3. Vai su "SQL Editor" nel menu laterale
-- 4. Crea una nuova query
-- 5. Incolla questo script
-- 6. Esegui (Run)
-- =============================================================================

-- 1. Abilita RLS sulla tabella triage_logs
ALTER TABLE triage_logs ENABLE ROW LEVEL SECURITY;

-- 2. Rimuovi eventuali policies esistenti (opzionale - solo se ci sono conflitti)
-- DROP POLICY IF EXISTS "Enable insert for all users" ON triage_logs;
-- DROP POLICY IF EXISTS "Enable read for all users" ON triage_logs;

-- 3. Policy per INSERT - Permetti tutti gli inserimenti
CREATE POLICY "Enable insert for all users" 
ON triage_logs 
FOR INSERT 
WITH CHECK (true);

-- 4. Policy per SELECT - Permetti tutte le letture
CREATE POLICY "Enable read for all users" 
ON triage_logs 
FOR SELECT 
USING (true);

-- 5. Policy per UPDATE (opzionale - se necessario)
-- CREATE POLICY "Enable update for all users" 
-- ON triage_logs 
-- FOR UPDATE 
-- USING (true);

-- 6. Policy per DELETE (opzionale - se necessario)
-- CREATE POLICY "Enable delete for all users" 
-- ON triage_logs 
-- FOR DELETE 
-- USING (true);

-- =============================================================================
-- VERIFICA
-- =============================================================================
-- Esegui questa query per verificare che le policies siano state create:
SELECT 
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd,
    qual,
    with_check
FROM pg_policies 
WHERE tablename = 'triage_logs';

-- Dovresti vedere almeno 2 policies:
-- 1. "Enable insert for all users" - cmd: INSERT
-- 2. "Enable read for all users" - cmd: SELECT

-- =============================================================================
-- ALTERNATIVE: DISABILITA COMPLETAMENTE RLS (NON RACCOMANDATO IN PRODUZIONE)
-- =============================================================================
-- Se vuoi disabilitare completamente RLS (solo per testing/dev):
-- ALTER TABLE triage_logs DISABLE ROW LEVEL SECURITY;
--
-- NOTA: Disabilitare RLS può esporre i dati, quindi è consigliato solo
-- per ambienti di sviluppo. In produzione usa le policies sopra.

-- =============================================================================
-- TROUBLESHOOTING
-- =============================================================================
-- Se i log ancora non vengono scritti dopo aver configurato RLS:
--
-- 1. Verifica che stai usando la SERVICE_ROLE KEY (non anon key)
--    - Vai su Settings → API
--    - Copia la "service_role" key (non "anon" key)
--    - Aggiornala in .streamlit/secrets.toml come SUPABASE_KEY
--
-- 2. Verifica la struttura della tabella:
--    SELECT column_name, data_type 
--    FROM information_schema.columns 
--    WHERE table_name = 'triage_logs';
--
--    Deve avere almeno:
--    - id (bigint o bigserial)
--    - session_id (text)
--    - user_input (text)
--    - bot_response (text)
--    - metadata (jsonb)
--    - processing_time_ms (integer)
--    - created_at (timestamp with time zone)
--
-- 3. Testa l'inserimento manualmente:
--    INSERT INTO triage_logs (session_id, user_input, bot_response, metadata)
--    VALUES ('test123', 'Test input', 'Test response', '{"test": true}');
--
--    Se questo fallisce, controlla i permessi della tabella.
--
-- 4. Controlla i log di Supabase per errori:
--    - Vai su Logs → Database
--    - Cerca errori relativi a triage_logs
--
-- =============================================================================
-- STRUTTURA TABELLA (se non esiste)
-- =============================================================================
-- Se la tabella non esiste ancora, creala con:
--
-- CREATE TABLE triage_logs (
--   id BIGSERIAL PRIMARY KEY,
--   session_id TEXT NOT NULL,
--   user_input TEXT,
--   bot_response TEXT,
--   metadata JSONB,
--   processing_time_ms INTEGER,
--   created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
-- );
-- 
-- -- Indici per performance
-- CREATE INDEX idx_triage_logs_session ON triage_logs(session_id);
-- CREATE INDEX idx_triage_logs_created ON triage_logs(created_at);
-- CREATE INDEX idx_triage_logs_metadata ON triage_logs USING GIN (metadata);
--
-- =============================================================================
