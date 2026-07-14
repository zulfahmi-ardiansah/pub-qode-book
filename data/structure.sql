-- master_unspsc definition

CREATE TABLE "master_unspsc" (
	code varchar NULL,
	title varchar NULL,
	definition varchar NULL,
	category varchar NULL,
	parent_code varchar NULL,
	"level" int4 NULL, description varchar,
	CONSTRAINT master_unspsc_code_key UNIQUE (code)
);

-- master_kbli definition

CREATE TABLE "master_kbli" (
	code varchar NULL,
	title varchar NULL,
	definition varchar NULL,
	category varchar NULL,
	parent_code varchar NULL,
	"level" int4 NULL, description varchar,
	CONSTRAINT master_kbli_code_key UNIQUE (code)
);

-- map_master definition

CREATE TABLE map_master (
	code_unspsc varchar,
	code_kbli varchar
);

-- map_view definition

CREATE VIEW map_view AS
SELECT
	m.code_kbli,
	mk.title AS title_kbli,
	m.code_unspsc,
	mu.title AS title_unspsc
FROM map_master m
JOIN master_unspsc mu ON m.code_unspsc = mu.code
JOIN master_kbli mk ON m.code_kbli = mk.code;