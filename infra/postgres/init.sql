\getenv op_password OPENPROJECT_DB_PASSWORD
\getenv app_password APP_DB_PASSWORD
CREATE ROLE openproject LOGIN PASSWORD :'op_password';
CREATE ROLE ai_team LOGIN PASSWORD :'app_password';
CREATE DATABASE openproject OWNER openproject;
CREATE DATABASE ai_team OWNER ai_team;
REVOKE CONNECT ON DATABASE openproject FROM PUBLIC;
REVOKE CONNECT ON DATABASE ai_team FROM PUBLIC;
