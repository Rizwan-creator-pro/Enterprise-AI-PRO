--------------------------------------------------------------------------------
-- Enterprise AI Hub  |  DB Fix 04  (RESTORE PROVIDERS ORDS HANDLER)
--
-- PURPOSE: Fixes the ORA / ORDS 403 "missing.function" error on /engine/providers
--          by querying ONLY the columns that actually exist in table AI_PROVIDERS:
--          (PROVIDER_ID, PROVIDER_NAME, LLM_API_URL, API_KEY, MODEL_NAME, PRIORITY_ORDER, IS_ACTIVE).
--
-- HOW TO RUN:
--   1. Open Oracle APEX -> SQL Workshop -> SQL Commands.
--   2. Ensure Schema is set to: AIAGENT.
--   3. Paste and run this script.
---------------------------------------------------------------------------------
SET SERVEROUTPUT ON

BEGIN
    -- 1. Restore /engine/providers (public listing for model picker, excludes api_key)
    ORDS.DEFINE_HANDLER(
        p_module_name    => 'AI',
        p_pattern        => 'providers',
        p_method         => 'GET',
        p_source_type    => 'json/collection',
        p_comments       => 'Active AI Providers from AI_PROVIDERS table (clean column list)',
        p_source         => 
'SELECT provider_id, provider_name, model_name, priority_order
FROM ai_providers
WHERE is_active = ''Y''
ORDER BY priority_order ASC');

    -- 2. Restore /engine/providers/internal (server-only, includes api_key)
    ORDS.DEFINE_HANDLER(
        p_module_name    => 'AI',
        p_pattern        => 'providers/internal',
        p_method         => 'GET',
        p_source_type    => 'json/collection',
        p_comments       => 'Active AI Providers with API keys for Python server execution',
        p_source         => 
'SELECT provider_id, provider_name, llm_api_url, api_key, model_name, priority_order
FROM ai_providers
WHERE is_active = ''Y''
ORDER BY priority_order ASC');

    COMMIT;
    DBMS_OUTPUT.PUT_LINE('SUCCESS: ORDS handlers for providers and providers/internal updated.');
END;
/
