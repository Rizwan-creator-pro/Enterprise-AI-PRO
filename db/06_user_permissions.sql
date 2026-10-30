--------------------------------------------------------------------------------
-- Enterprise AI Hub  |  DB upgrade 06  (USER PERMISSIONS CONTROL)
--
-- Run as schema AIAGENT in Oracle APEX > SQL Workshop > SQL Scripts > Run
--                    or: Database Actions (SQL Developer Web) > SQL (F5)
--
-- What this script does:
--   1. Adds 4 granular permission columns to AI_USERS (safe & additive with default 'Y'):
--        * CAN_SEARCH_OUTSIDE ('Y' / 'N'): Permission to toggle General AI / search live web & external APIs
--        * CAN_UPLOAD         ('Y' / 'N'): Permission to upload files/images (if 'N', hides upload & right Artifacts sidebar)
--        * CAN_VIEW_TABLES    ('Y' / 'N'): Permission to view schema tables & execute table data queries
--        * CAN_VIEW_DOCS      ('Y' / 'N'): Permission to view & preview documents and code attachments
--   2. Updates ORDS auth handlers (auth/user and auth/login) to return these permissions.
--   3. Guarantees admin user has all 'Y' permissions.
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

-- 1. Add permission columns to AI_USERS
BEGIN
    ai_ddl(q'[ALTER TABLE ai_users ADD (can_search_outside CHAR(1) DEFAULT 'Y')]');
    ai_ddl(q'[ALTER TABLE ai_users ADD (can_upload CHAR(1) DEFAULT 'Y')]');
    ai_ddl(q'[ALTER TABLE ai_users ADD (can_view_tables CHAR(1) DEFAULT 'Y')]');
    ai_ddl(q'[ALTER TABLE ai_users ADD (can_view_docs CHAR(1) DEFAULT 'Y')]');
END;
/

-- 2. Add validation check constraints
BEGIN
    ai_ddl(q'[ALTER TABLE ai_users ADD CONSTRAINT chk_usr_search_out CHECK (can_search_outside IN ('Y', 'N'))]');
    ai_ddl(q'[ALTER TABLE ai_users ADD CONSTRAINT chk_usr_upload CHECK (can_upload IN ('Y', 'N'))]');
    ai_ddl(q'[ALTER TABLE ai_users ADD CONSTRAINT chk_usr_tables CHECK (can_view_tables IN ('Y', 'N'))]');
    ai_ddl(q'[ALTER TABLE ai_users ADD CONSTRAINT chk_usr_docs CHECK (can_view_docs IN ('Y', 'N'))]');
END;
/

-- 3. Ensure existing users have 'Y' as safe baseline, and admin has all permissions
UPDATE ai_users
   SET can_search_outside = NVL(can_search_outside, 'Y'),
       can_upload         = NVL(can_upload, 'Y'),
       can_view_tables    = NVL(can_view_tables, 'Y'),
       can_view_docs      = NVL(can_view_docs, 'Y');

UPDATE ai_users
   SET can_search_outside = 'Y',
       can_upload         = 'Y',
       can_view_tables    = 'Y',
       can_view_docs      = 'Y'
 WHERE LOWER(username) = 'admin' OR is_admin = 'Y';

COMMIT;

-- 4. Update ORDS POST auth/user endpoint to return permission flags
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
BEGIN
    SELECT JSON_OBJECT(
               'user_id'            VALUE user_id,
               'username'           VALUE username,
               'password_hash'      VALUE password_hash,
               'is_admin'           VALUE NVL(is_admin, 'N'),
               'role'               VALUE NVL(role, CASE WHEN LOWER(username) = 'admin' THEN 'admin' ELSE 'user' END),
               'daily_token_quota'  VALUE daily_token_quota,
               'can_search_outside' VALUE NVL(can_search_outside, 'Y'),
               'can_upload'         VALUE NVL(can_upload, 'Y'),
               'can_view_tables'    VALUE NVL(can_view_tables, 'Y'),
               'can_view_docs'      VALUE NVL(can_view_docs, 'Y')
           )
      INTO l_out
      FROM ai_users
     WHERE LOWER(username) = LOWER(:username)
       AND NVL(is_active, 'Y') = 'Y';

    OWA_UTIL.MIME_HEADER('application/json', TRUE);
    HTP.P(l_out);
EXCEPTION WHEN NO_DATA_FOUND THEN
    OWA_UTIL.MIME_HEADER('application/json', TRUE);
    HTP.P('{}');
END;]'
    );
END;
/

-- 5. Update legacy ORDS GET auth/login endpoint to include permissions
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
       is_active,
       NVL(is_admin, CASE WHEN LOWER(username) = 'admin' THEN 'Y' ELSE 'N' END) AS is_admin,
       NVL(role, CASE WHEN LOWER(username) = 'admin' THEN 'admin' ELSE 'user' END) AS role,
       NVL(can_search_outside, 'Y') AS can_search_outside,
       NVL(can_upload, 'Y')         AS can_upload,
       NVL(can_view_tables, 'Y')    AS can_view_tables,
       NVL(can_view_docs, 'Y')      AS can_view_docs
  FROM ai_users
 WHERE username = :username
   AND password_hash = :password
   AND NVL(is_active, 'Y') = 'Y']'
    );
END;
/

COMMIT;
PROMPT [SUCCESS] Upgrade 06 completed: User permissions columns and ORDS handlers deployed!
