--
-- PostgreSQL database dump
--

\restrict m4SF9HhwMEkrXuq3jX8yLU3leZihb9iH9McBNAr9l1EUasJvi0gqR7pjQVbs7dT

-- Dumped from database version 18.3
-- Dumped by pg_dump version 18.3

-- Started on 2026-07-01 13:07:21

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- TOC entry 9 (class 2615 OID 273231)
-- Name: applogins; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA applogins;


--
-- TOC entry 5093 (class 0 OID 0)
-- Dependencies: 9
-- Name: SCHEMA applogins; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA applogins IS 'Contains table to store application user login info.';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- TOC entry 236 (class 1259 OID 273233)
-- Name: app_users; Type: TABLE; Schema: applogins; Owner: -
--

CREATE TABLE applogins.app_users (
    id integer NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    is_active boolean DEFAULT true,
    password_date timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- TOC entry 235 (class 1259 OID 273232)
-- Name: app_users_id_seq; Type: SEQUENCE; Schema: applogins; Owner: -
--

ALTER TABLE applogins.app_users ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME applogins.app_users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- TOC entry 5087 (class 0 OID 273233)
-- Dependencies: 236
-- Data for Name: app_users; Type: TABLE DATA; Schema: applogins; Owner: -
--

INSERT INTO applogins.app_users OVERRIDING SYSTEM VALUE VALUES (2, 'zx@qaz.com', '$2b$12$2c6YDTZ3R6Y1lcQF0FaLNeSDMfSntSBbwvPpwWXD4VpK7mdfYB4t6', true, '2026-05-22 10:40:34.16184-04');


--
-- TOC entry 5097 (class 0 OID 0)
-- Dependencies: 235
-- Name: app_users_id_seq; Type: SEQUENCE SET; Schema: applogins; Owner: -
--

SELECT pg_catalog.setval('applogins.app_users_id_seq', 2, true);


--
-- TOC entry 4935 (class 2606 OID 273246)
-- Name: app_users app_users_email_key; Type: CONSTRAINT; Schema: applogins; Owner: -
--

ALTER TABLE ONLY applogins.app_users
    ADD CONSTRAINT app_users_email_key UNIQUE (email);


--
-- TOC entry 4937 (class 2606 OID 273244)
-- Name: app_users app_users_pkey; Type: CONSTRAINT; Schema: applogins; Owner: -
--

ALTER TABLE ONLY applogins.app_users
    ADD CONSTRAINT app_users_pkey PRIMARY KEY (id);


--
-- TOC entry 5094 (class 0 OID 0)
-- Dependencies: 9
-- Name: SCHEMA applogins; Type: ACL; Schema: -; Owner: -
--

GRANT USAGE ON SCHEMA applogins TO user_10qk;


--
-- TOC entry 5095 (class 0 OID 0)
-- Dependencies: 236
-- Name: TABLE app_users; Type: ACL; Schema: applogins; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE applogins.app_users TO user_10qk;


--
-- TOC entry 5096 (class 0 OID 0)
-- Dependencies: 235
-- Name: SEQUENCE app_users_id_seq; Type: ACL; Schema: applogins; Owner: -
--

GRANT ALL ON SEQUENCE applogins.app_users_id_seq TO user_10qk;


-- Completed on 2026-07-01 13:07:21

--
-- PostgreSQL database dump complete
--

\unrestrict m4SF9HhwMEkrXuq3jX8yLU3leZihb9iH9McBNAr9l1EUasJvi0gqR7pjQVbs7dT

