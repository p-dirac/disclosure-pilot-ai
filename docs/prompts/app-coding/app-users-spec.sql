-- The app_users table is for storing login info in a PostgreSql database
-- postgres, under schema applogins, where the table structure is listed below:

--
-- Name: app_users; Type: TABLE; Schema: applogins; Owner: postgres
--

CREATE TABLE applogins.app_users (
    id integer NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    is_active boolean DEFAULT true,
    password_date timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);