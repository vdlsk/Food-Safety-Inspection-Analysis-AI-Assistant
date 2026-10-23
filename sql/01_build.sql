-- Chicago food inspections: cleaned tables. Placeholders in curly braces are filled by src/build_db.py.

CREATE OR REPLACE TABLE raw_inspections AS
SELECT * FROM read_csv('{csv}', header = true, all_varchar = true, quote = '"', escape = '"',
                       max_line_size = 10000000);

CREATE OR REPLACE TABLE inspections AS
WITH t AS (
    SELECT
        CAST("Inspection ID" AS BIGINT)                          AS inspection_id,
        trim("DBA Name")                                         AS dba_name,
        trim("AKA Name")                                         AS aka_name,
        TRY_CAST("License #" AS BIGINT)                          AS license_no,
        lower(trim(coalesce("Facility Type", 'unknown')))        AS facility_type_raw,
        TRY_CAST(regexp_extract("Risk", 'Risk (\d)', 1) AS INTEGER) AS risk_level,
        trim("Address")                                          AS address,
        trim("Zip")                                              AS zip,
        strptime("Inspection Date", '%m/%d/%Y')::DATE            AS inspection_date,
        lower(trim("Inspection Type"))                           AS inspection_type_raw,
        trim("Results")                                          AS result,
        "Violations"                                             AS violations_raw,
        TRY_CAST("Latitude" AS DOUBLE)                           AS lat,
        TRY_CAST("Longitude" AS DOUBLE)                          AS lon
    FROM raw_inspections
)
SELECT DISTINCT ON (inspection_id) *,
    CASE
        WHEN facility_type_raw LIKE '%restaurant%'                     THEN 'restaurant'
        WHEN facility_type_raw LIKE '%grocery%'                        THEN 'grocery store'
        WHEN facility_type_raw LIKE '%school%'                         THEN 'school'
        WHEN facility_type_raw LIKE '%daycare%' OR facility_type_raw LIKE '%children%' THEN 'daycare'
        WHEN facility_type_raw LIKE '%bakery%'                         THEN 'bakery'
        WHEN facility_type_raw LIKE '%long term care%' OR facility_type_raw LIKE '%hospital%' THEN 'healthcare'
        WHEN facility_type_raw LIKE '%mobile%'                         THEN 'mobile food'
        WHEN facility_type_raw LIKE '%catering%'                       THEN 'catering'
        WHEN facility_type_raw LIKE '%tavern%' OR facility_type_raw LIKE '%bar%' THEN 'tavern/bar'
        ELSE 'other'
    END AS facility_type,
    CASE
        WHEN inspection_type_raw LIKE '%re-insp%' OR inspection_type_raw LIKE '%reinsp%' THEN 'reinspection'
        WHEN inspection_type_raw LIKE 'canvass%'                       THEN 'canvass'
        WHEN inspection_type_raw LIKE '%complaint%'                    THEN 'complaint'
        WHEN inspection_type_raw LIKE '%license%'                      THEN 'license'
        WHEN inspection_type_raw LIKE '%food poison%'                  THEN 'food poisoning'
        ELSE 'other'
    END AS inspection_class,
    result IN ('Pass', 'Pass w/ Conditions', 'Fail')                   AS has_outcome,
    CASE WHEN result = 'Fail' THEN 1 ELSE 0 END                        AS failed,
    inspection_date >= DATE '{new_code_start}'                         AS new_code_era
FROM t
WHERE inspection_date IS NOT NULL
ORDER BY inspection_id;

-- One row per cited violation. Format: "N. TITLE - Comments: text | N. TITLE - Comments: text"
CREATE OR REPLACE TABLE violations AS
WITH parts AS (
    SELECT inspection_id, license_no, inspection_date, new_code_era, result, inspection_class,
           trim(unnest(string_split(violations_raw, ' | '))) AS item
    FROM inspections
    WHERE violations_raw IS NOT NULL AND violations_raw <> ''
)
SELECT inspection_id, license_no, inspection_date, new_code_era, result, inspection_class,
       TRY_CAST(regexp_extract(item, '^(\d+)\.', 1) AS INTEGER)                        AS violation_no,
       trim(regexp_extract(item, '^\d+\.\s*(.*?)(\s+-\s+Comments:|$)', 1))            AS violation_title,
       trim(regexp_extract(item, 'Comments:\s*(.*)$', 1))                               AS comments
FROM parts
WHERE regexp_matches(item, '^\d+\.');

-- Canonical title for each violation number within the current (post-July-2018) code
CREATE OR REPLACE TABLE violation_codes AS
SELECT violation_no, mode(violation_title) AS violation_title, count(*) AS citations
FROM violations WHERE new_code_era AND violation_no IS NOT NULL
GROUP BY violation_no ORDER BY violation_no;

-- Establishment-level summary (license = establishment)
CREATE OR REPLACE TABLE establishments AS
SELECT license_no,
       arg_max(dba_name, inspection_date)      AS name,
       arg_max(address, inspection_date)       AS address,
       arg_max(facility_type, inspection_date) AS facility_type,
       arg_max(risk_level, inspection_date)    AS risk_level,
       count(*) FILTER (WHERE has_outcome)                                   AS n_inspections,
       count(*) FILTER (WHERE has_outcome AND inspection_class <> 'reinspection') AS n_regular,
       sum(failed)                                                           AS n_fails,
       sum(failed) FILTER (WHERE inspection_class <> 'reinspection')         AS n_regular_fails,
       min(inspection_date) AS first_inspection, max(inspection_date) AS last_inspection,
       arg_max(result, inspection_date) FILTER (WHERE has_outcome)           AS last_result
FROM inspections
WHERE license_no IS NOT NULL AND license_no > 0
GROUP BY license_no;

-- Establishments that were cited for the same violation in 2+ separate inspections (current code)
CREATE OR REPLACE TABLE repeat_violations AS
SELECT license_no, violation_no, count(DISTINCT inspection_id) AS times_cited,
       min(inspection_date) AS first_cited, max(inspection_date) AS last_cited
FROM violations
WHERE new_code_era AND violation_no IS NOT NULL AND license_no > 0
GROUP BY license_no, violation_no
HAVING count(DISTINCT inspection_id) >= 2;
