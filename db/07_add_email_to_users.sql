--------------------------------------------------------------------------------
-- Enterprise AI Hub  |  DB upgrade 07  (EMAIL AUTHENTICATION SUPPORT)
--
-- Run as schema AIAGENT in Oracle APEX > SQL Workshop > SQL Scripts > Run
--                    or: Database Actions (SQL Developer Web) > SQL (F5)
--
-- What this script does:
--   1. Adds EMAIL VARCHAR2(255) column to AI_USERS table (safe & idempotent).
--   2. Creates a unique index on LOWER(email) to prevent duplicate accounts.
--   3. Initializes email addresses for existing users if NULL (e.g. admin@enterprise.com).
--   4. Updates ORDS POST auth/user and GET auth/login handlers to support login
--      directly by email and password, returning the verified email in session.
--------------------------------------------------------------------------------
SET DEFINE OFF
SET SERVEROUTPUT ON

-- Helper procedure for safe, idempotent DDL execution
CREATE OR REPLACE PROCEDURE ai_ddl (p_sql IN VARCHAR2) AS
BEGIN
    EXECUTE IMMEDIATE p_sql;
EXCEPTION
    WHEN OTHERS THEN
        -- Ignore: -955 (name in use), -1430 (column exists), -1408 (index exists), -2260/-2261/-2264 (constraint exists)
        IF SQLCODE IN (-955, -1430, -1408, -2260, -2261, -2264) THEN
            NULL;
        ELSE
            RAISE;
        END IF;
END;
/

-- 1. Add EMAIL column to AI_USERS
BEGIN
    ai_ddl('ALTER TABLE ai_users ADD (email VARCHAR2(255))');
    DBMS_OUTPUT.PUT_LINE('AI_USERS email column verified/added.');
END;
/

-- 2. Populate email for existing users where currently NULL
BEGIN
    UPDATE ai_users
       SET email = CASE 
                     WHEN LOWER(username) = 'admin' THEN 'admin@enterprise.com'
                     ELSE LOWER(username) || '@enterprise.com'
                   END
     WHERE email IS NULL;
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('Existing users updated with corporate email addresses.');
END;
/

-- 3. Create unique index on LOWER(email)
BEGIN
    ai_ddl('CREATE UNIQUE INDEX ai_users_email_uk ON ai_users(LOWER(email))');
    DBMS_OUTPUT.PUT_LINE('Unique index on LOWER(email) created.');
END;
/

-- 4. Update ORDS POST auth/user endpoint to support email-based authentication
BEGIN
    ORDS.DEFINE_TEMPLATE(p_module_name => 'AI', p_pattern => 'auth/user');
    ORDS.DEFINE_HANDLER(
        p_module_name => 'AI',
        p_pattern     => 'auth/user',
        p_method      => 'POST',
        p_source_type => 'plsql/block',
        p_source      => q'[
DECLARE
    l_out CLOB;
    l_id VARCHAR2(255);
BEGIN
    -- Support lookup by email (primary) or username
    l_id := LOWER(TRIM(NVL(:email, :username)));

    SELECT JSON_OBJECT(
               'user_id'            VALUE user_id,
               'username'           VALUE username,
               'email'              VALUE NVL(email, LOWER(username) || '@enterprise.com'),
               'password_hash'      VALUE password_hash,
               'is_admin'           VALUE NVL(is_admin, CASE WHEN LOWER(username) = 'admin' OR LOWER(email) LIKE 'admin@%' THEN 'Y' ELSE 'N' END),
               'role'               VALUE NVL(role, CASE WHEN LOWER(username) = 'admin' OR LOWER(email) LIKE 'admin@%' THEN 'admin' ELSE 'user' END),
               'daily_token_quota'  VALUE daily_token_quota,
               'can_search_outside' VALUE NVL(can_search_outside, 'Y'),
               'can_upload'         VALUE NVL(can_upload, 'Y'),
               'can_view_tables'    VALUE NVL(can_view_tables, 'Y'),
               'can_view_docs'      VALUE NVL(can_view_docs, 'Y')
           )
      INTO l_out
      FROM ai_users
     WHERE (LOWER(email) = l_id OR LOWER(username) = l_id)
       AND NVL(is_active, 'Y') = 'Y'
       AND ROWNUM = 1;

    OWA_UTIL.MIME_HEADER('application/json', TRUE);
    HTP.P(l_out);
EXCEPTION WHEN NO_DATA_FOUND THEN
    OWA_UTIL.MIME_HEADER('application/json', TRUE);
    HTP.P('{}');
END;]'
    );
    DBMS_OUTPUT.PUT_LINE('ORDS POST auth/user handler updated for email authentication.');
END;
/

-- 5. Update legacy ORDS GET auth/login endpoint to include email and support email lookup
BEGIN
    ORDS.DEFINE_TEMPLATE(p_module_name => 'AI', p_pattern => 'auth/login');
    ORDS.DEFINE_HANDLER(
        p_module_name => 'AI',
        p_pattern     => 'auth/login',
        p_method      => 'GET',
        p_source_type => 'json/query',
        p_source      => q'[
SELECT user_id,
       username,
       NVL(email, LOWER(username) || '@enterprise.com') AS email,
       is_active,
       NVL(is_admin, CASE WHEN LOWER(username) = 'admin' OR LOWER(email) LIKE 'admin@%' THEN 'Y' ELSE 'N' END) AS is_admin,
       NVL(role, CASE WHEN LOWER(username) = 'admin' OR LOWER(email) LIKE 'admin@%' THEN 'admin' ELSE 'user' END) AS role,
       NVL(can_search_outside, 'Y') AS can_search_outside,
       NVL(can_upload, 'Y')         AS can_upload,
       NVL(can_view_tables, 'Y')    AS can_view_tables,
       NVL(can_view_docs, 'Y')      AS can_view_docs
  FROM ai_users
 WHERE (LOWER(email) = LOWER(NVL(:email, :username)) OR LOWER(username) = LOWER(NVL(:email, :username)))
   AND password_hash = :password
   AND NVL(is_active, 'Y') = 'Y'
   AND ROWNUM = 1]'
    );
    DBMS_OUTPUT.PUT_LINE('ORDS GET auth/login handler updated for email authentication.');
END;
/

COMMIT;
PROMPT >>> Upgrade 07 completed successfully! Email login is now active.
