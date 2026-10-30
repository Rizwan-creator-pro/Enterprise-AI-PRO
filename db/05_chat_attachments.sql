--------------------------------------------------------------------------------
-- Enterprise AI Hub  |  DB Migration 05  (CHAT ATTACHMENTS & GOOGLE DRIVE REFERENCES)
--
-- PURPOSE: 
--   1. Creates table AI_CHAT_ATTACHMENTS to store file & image reference links
--      (Google Drive web view link, direct download link, file size, user, chat session).
--   2. Registers ORDS endpoints for saving and retrieving current chat files:
--        GET  /engine/chat/attachments/:session_id
--        POST /engine/chat/attachments
--
-- HOW TO RUN:
--   1. Open Oracle APEX -> SQL Workshop -> SQL Commands (or SQL Developer).
--   2. Ensure Schema is set to: AIAGENT.
--   3. Paste and run this script.
---------------------------------------------------------------------------------
SET SERVEROUTPUT ON

-- 1. Create AI_CHAT_ATTACHMENTS table if not exists
DECLARE
    v_count NUMBER := 0;
BEGIN
    SELECT COUNT(*) INTO v_count FROM user_tables WHERE table_name = 'AI_CHAT_ATTACHMENTS';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE '
            CREATE TABLE ai_chat_attachments (
                attachment_id        NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                session_id           VARCHAR2(100) NOT NULL,
                user_id              NUMBER NOT NULL,
                file_name            VARCHAR2(255) NOT NULL,
                file_type            VARCHAR2(50) NOT NULL,
                mime_type            VARCHAR2(100),
                file_size            NUMBER,
                gdrive_file_id       VARCHAR2(150),
                gdrive_web_link      VARCHAR2(1000),
                gdrive_download_link VARCHAR2(1000),
                storage_source       VARCHAR2(50) DEFAULT ''GDRIVE'',
                created_at           TIMESTAMP DEFAULT SYSTIMESTAMP
            )';
        DBMS_OUTPUT.PUT_LINE('SUCCESS: Table AI_CHAT_ATTACHMENTS created.');
    ELSE
        DBMS_OUTPUT.PUT_LINE('NOTICE: Table AI_CHAT_ATTACHMENTS already exists.');
    END IF;
END;
/

-- 2. Indexes for fast retrieval by chat session and user
DECLARE
    v_idx1 NUMBER := 0;
    v_idx2 NUMBER := 0;
BEGIN
    SELECT COUNT(*) INTO v_idx1 FROM user_indexes WHERE index_name = 'IDX_AI_ATT_SESSION';
    IF v_idx1 = 0 THEN
        EXECUTE IMMEDIATE 'CREATE INDEX idx_ai_att_session ON ai_chat_attachments(session_id)';
    END IF;

    SELECT COUNT(*) INTO v_idx2 FROM user_indexes WHERE index_name = 'IDX_AI_ATT_USER';
    IF v_idx2 = 0 THEN
        EXECUTE IMMEDIATE 'CREATE INDEX idx_ai_att_user ON ai_chat_attachments(user_id)';
    END IF;
    DBMS_OUTPUT.PUT_LINE('SUCCESS: Indexes verified.');
END;
/

-- 3. Define ORDS REST endpoints under module 'AI'
BEGIN
    -- GET /chat/attachments/:session_id
    ORDS.DEFINE_TEMPLATE(
        p_module_name    => 'AI',
        p_pattern        => 'chat/attachments/:session_id',
        p_priority       => 0,
        p_etag_type      => 'HASH',
        p_comments       => 'List attachments for a chat session'
    );

    ORDS.DEFINE_HANDLER(
        p_module_name    => 'AI',
        p_pattern        => 'chat/attachments/:session_id',
        p_method         => 'GET',
        p_source_type    => 'json/collection',
        p_comments       => 'Retrieve all documents and images uploaded in current chat session',
        p_source         => 
'SELECT attachment_id, session_id, user_id, file_name, file_type, mime_type, file_size,
        gdrive_file_id, gdrive_web_link, gdrive_download_link, storage_source,
        TO_CHAR(created_at, ''YYYY-MM-DD"T"HH24:MI:SS'') AS created_at
 FROM ai_chat_attachments
 WHERE session_id = :session_id
 ORDER BY attachment_id ASC'
    );

    -- POST /chat/attachments
    ORDS.DEFINE_TEMPLATE(
        p_module_name    => 'AI',
        p_pattern        => 'chat/attachments',
        p_priority       => 0,
        p_etag_type      => 'HASH',
        p_comments       => 'Insert new chat attachment record'
    );

    ORDS.DEFINE_HANDLER(
        p_module_name    => 'AI',
        p_pattern        => 'chat/attachments',
        p_method         => 'POST',
        p_source_type    => 'plsql/block',
        p_comments       => 'Save uploaded document or image reference',
        p_source         => 
'BEGIN
    INSERT INTO ai_chat_attachments (
        session_id, user_id, file_name, file_type, mime_type, file_size,
        gdrive_file_id, gdrive_web_link, gdrive_download_link, storage_source
    ) VALUES (
        :session_id, :user_id, :file_name, :file_type, :mime_type, :file_size,
        :gdrive_file_id, :gdrive_web_link, :gdrive_download_link, NVL(:storage_source, ''GDRIVE'')
    );
    COMMIT;
    :status := 201;
END;'
    );

    COMMIT;
    DBMS_OUTPUT.PUT_LINE('SUCCESS: ORDS Handlers for /chat/attachments created.');
END;
/
