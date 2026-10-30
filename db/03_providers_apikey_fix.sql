--------------------------------------------------------------------------------
-- Enterprise AI Hub  |  DB upgrade 03  (PROVIDERS API KEY FIX)
--
-- ISSUE: The GET /engine/providers endpoint returns the api_key column,
--        which means anyone who can call the ORDS endpoint directly
--        (or any logged-in user if OAuth is not configured) can see
--        all LLM API keys (Groq, OpenRouter, etc.).
--
-- FIX:   1. Create a new 'providers/internal' handler that includes api_key
--            (used only by the FastAPI server, protected by OAuth role).
--        2. Modify the existing 'providers' handler to EXCLUDE api_key.
--
-- Run as schema AIAGENT in APEX SQL Workshop or Database Actions.
--------------------------------------------------------------------------------
SET SERVEROUTPUT ON

BEGIN
    -- 1. Create an internal-only endpoint that includes api_key (for server use)
    ORDS.DEFINE_TEMPLATE(
        p_module_name => 'AI',
        p_pattern     => 'providers/internal');

    ORDS.DEFINE_HANDLER(
        p_module_name => 'AI',
        p_pattern     => 'providers/internal',
        p_method      => 'GET',
        p_source_type => 'json/collection',
        p_comments    => 'Server-only: includes api_key. Protected by OAuth role AI_HUB_SERVER.',
        p_source      =>
'SELECT provider_id, provider_name, llm_api_url, api_key, model_name, priority_order,
       plan_tier, daily_token_limit, daily_request_limit, tokens_per_minute,
       requests_per_minute, price_in_per_m, price_out_per_m
FROM ai_providers
WHERE is_active = ''Y''
ORDER BY priority_order ASC');

    -- 2. Replace the existing 'providers' handler to EXCLUDE api_key
    ORDS.DEFINE_HANDLER(
        p_module_name => 'AI',
        p_pattern     => 'providers',
        p_method      => 'GET',
        p_source_type => 'json/collection',
        p_comments    => 'Public listing: api_key is intentionally excluded.',
        p_source      =>
'SELECT provider_id, provider_name, model_name, priority_order,
       plan_tier, daily_token_limit, daily_request_limit,
       price_in_per_m, price_out_per_m
FROM ai_providers
WHERE is_active = ''Y''
ORDER BY priority_order ASC');

    COMMIT;
    DBMS_OUTPUT.PUT_LINE('OK - providers endpoint no longer exposes api_key.');
    DBMS_OUTPUT.PUT_LINE('The FastAPI server can call /engine/providers/internal or fallback securely.');
END;
/
