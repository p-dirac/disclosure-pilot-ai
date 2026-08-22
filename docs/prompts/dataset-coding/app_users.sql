
CREATE USER zx@qaz.com WITH PASSWORD 'qwerty';

# allows connect and read permission
GRANT pg_read_all_data TO user_10qk;


CREATE TABLE applogins.app_users (
    id            INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active     BOOLEAN DEFAULT TRUE,
    password_date TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

GRANT USAGE ON SCHEMA applogins TO user_10qk;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE applogins.app_users TO user_10qk;

GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA applogins TO user_10qk;
