-- =============================================================================
-- cohort_retention.sql  |  PostgreSQL 12+
--
-- Builds, from one subscription table:
--   Step 1  signup-month cohorts
--   Step 2  the active-month spine (one row per user per month they were paying)
--   Step 3  the cohort retention matrix (% of each cohort still active in month N)
--   Step 4  MRR movement: new, churned and net MRR by month
--
-- Grain note: a user is counted as active in every month from their signup
-- month up to, but not including, the month they churned in.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 0. Staging
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS subscriptions CASCADE;

CREATE TABLE subscriptions (
    user_id             INTEGER        PRIMARY KEY,
    signup_date         DATE           NOT NULL,
    plan                VARCHAR(20)    NOT NULL,
    mrr                 NUMERIC(10, 2) NOT NULL,
    acquisition_channel VARCHAR(40),
    status              VARCHAR(10)    NOT NULL CHECK (status IN ('Active', 'Churned')),
    churn_date          DATE
);

-- From psql use \copy so the file is read client-side:
--   \copy subscriptions FROM 'data/raw/subscriptions.csv' CSV HEADER
COPY subscriptions
FROM '/absolute/path/to/data/raw/subscriptions.csv'
WITH (FORMAT csv, HEADER true);

-- A churned row must carry a churn date, and it cannot precede signup.
ALTER TABLE subscriptions
    ADD CONSTRAINT chk_churn_date
    CHECK ((status = 'Active'  AND churn_date IS NULL)
        OR (status = 'Churned' AND churn_date >= signup_date));

CREATE INDEX idx_subs_signup ON subscriptions (signup_date);
CREATE INDEX idx_subs_status ON subscriptions (status);
ANALYZE subscriptions;


-- -----------------------------------------------------------------------------
-- Steps 1-3: cohorts, active months, retention matrix
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS cohort_retention;

CREATE TABLE cohort_retention AS
WITH
-- Reporting window: months that actually exist in the data.
calendar AS (
    SELECT date_trunc('month', MIN(signup_date))::date AS first_month,
           date_trunc('month', MAX(COALESCE(churn_date, signup_date)))::date AS last_month
    FROM subscriptions
),

-- STEP 1 -- assign every user to the month they started paying.
cohorts AS (
    SELECT
        s.user_id,
        date_trunc('month', s.signup_date)::date AS cohort_month,
        date_trunc('month', s.churn_date)::date  AS churn_month,
        s.plan,
        s.mrr,
        s.acquisition_channel,
        s.status
    FROM subscriptions s
),

-- STEP 2 -- expand each user into one row per month they were active.
-- A churn in month M means the last active month is M-1; a user who churns in
-- their signup month therefore contributes month 0 only.
user_months AS (
    SELECT
        c.user_id,
        c.cohort_month,
        c.mrr,
        gs.active_month::date AS active_month,
        -- Whole months between signup and this row: 0 = the signup month.
        (EXTRACT(YEAR  FROM AGE(gs.active_month, c.cohort_month)) * 12
       + EXTRACT(MONTH FROM AGE(gs.active_month, c.cohort_month)))::int AS month_number
    FROM cohorts c
    CROSS JOIN calendar cal
    CROSS JOIN LATERAL generate_series(
        c.cohort_month,
        LEAST(COALESCE(c.churn_month - INTERVAL '1 month', cal.last_month), cal.last_month),
        INTERVAL '1 month'
    ) AS gs(active_month)
),

-- Cohort denominator: how many users started in each month.
cohort_size AS (
    SELECT cohort_month, COUNT(*) AS cohort_users, SUM(mrr) AS cohort_starting_mrr
    FROM cohorts
    GROUP BY cohort_month
),

-- STEP 3 -- retained users and retained MRR per cohort per month offset.
retention AS (
    SELECT
        um.cohort_month,
        um.month_number,
        COUNT(DISTINCT um.user_id) AS retained_users,
        SUM(um.mrr)                AS retained_mrr
    FROM user_months um
    GROUP BY um.cohort_month, um.month_number
)

SELECT
    r.cohort_month,
    r.month_number,
    cs.cohort_users,
    r.retained_users,
    ROUND(100.0 * r.retained_users / cs.cohort_users, 1) AS retention_pct,
    ROUND(100.0 - 100.0 * r.retained_users / cs.cohort_users, 1) AS churned_pct,
    ROUND(r.retained_mrr, 2) AS retained_mrr,
    ROUND(100.0 * r.retained_mrr / cs.cohort_starting_mrr, 1) AS mrr_retention_pct
FROM retention r
JOIN cohort_size cs USING (cohort_month)
ORDER BY r.cohort_month, r.month_number;

ALTER TABLE cohort_retention ADD PRIMARY KEY (cohort_month, month_number);


-- Wide form for a quick look in psql; Power BI should use the long table above
-- and pivot in the Matrix visual instead.
CREATE OR REPLACE VIEW vw_cohort_matrix AS
SELECT
    cohort_month,
    MAX(cohort_users) AS cohort_users,
    MAX(retention_pct) FILTER (WHERE month_number = 0) AS m0,
    MAX(retention_pct) FILTER (WHERE month_number = 1) AS m1,
    MAX(retention_pct) FILTER (WHERE month_number = 2) AS m2,
    MAX(retention_pct) FILTER (WHERE month_number = 3) AS m3,
    MAX(retention_pct) FILTER (WHERE month_number = 4) AS m4,
    MAX(retention_pct) FILTER (WHERE month_number = 5) AS m5,
    MAX(retention_pct) FILTER (WHERE month_number = 6) AS m6
FROM cohort_retention
GROUP BY cohort_month
ORDER BY cohort_month;


-- -----------------------------------------------------------------------------
-- STEP 4 -- MRR movement: what was added, what was lost, what remains.
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS mrr_movement;

CREATE TABLE mrr_movement AS
WITH calendar AS (
    SELECT generate_series(
        date_trunc('month', (SELECT MIN(signup_date) FROM subscriptions)),
        date_trunc('month', (SELECT MAX(COALESCE(churn_date, signup_date)) FROM subscriptions)),
        INTERVAL '1 month'
    )::date AS month
),

-- MRR starting in each month.
new_mrr AS (
    SELECT date_trunc('month', signup_date)::date AS month,
           COUNT(*)  AS new_customers,
           SUM(mrr)  AS new_mrr
    FROM subscriptions
    GROUP BY 1
),

-- MRR lost in each month: the month the status changed to 'Churned'.
churned_mrr AS (
    SELECT date_trunc('month', churn_date)::date AS month,
           COUNT(*) AS churned_customers,
           SUM(mrr) AS lost_mrr
    FROM subscriptions
    WHERE status = 'Churned'
    GROUP BY 1
),

-- Customers (and MRR) on the book during each month.
active_mrr AS (
    SELECT cal.month,
           COUNT(s.user_id) AS active_customers,
           COALESCE(SUM(s.mrr), 0) AS active_mrr
    FROM calendar cal
    LEFT JOIN subscriptions s
           ON date_trunc('month', s.signup_date)::date <= cal.month
          AND (s.churn_date IS NULL
               OR date_trunc('month', s.churn_date)::date > cal.month)
    GROUP BY cal.month
)

SELECT
    a.month,
    a.active_customers,
    ROUND(a.active_mrr, 2)                              AS active_mrr,
    COALESCE(n.new_customers, 0)                        AS new_customers,
    ROUND(COALESCE(n.new_mrr, 0), 2)                    AS new_mrr,
    COALESCE(c.churned_customers, 0)                    AS churned_customers,
    ROUND(COALESCE(c.lost_mrr, 0), 2)                   AS lost_mrr,
    ROUND(COALESCE(n.new_mrr, 0) - COALESCE(c.lost_mrr, 0), 2) AS net_new_mrr,
    -- Churn rate is measured against the base that was exposed to churn: the
    -- customers on the book at the start of the month, i.e. last month's close.
    ROUND(100.0 * COALESCE(c.churned_customers, 0)
          / NULLIF(LAG(a.active_customers) OVER (ORDER BY a.month), 0), 2) AS logo_churn_pct,
    ROUND(100.0 * COALESCE(c.lost_mrr, 0)
          / NULLIF(LAG(a.active_mrr) OVER (ORDER BY a.month), 0), 2)       AS mrr_churn_pct
FROM active_mrr a
LEFT JOIN new_mrr     n ON n.month = a.month
LEFT JOIN churned_mrr c ON c.month = a.month
ORDER BY a.month;

ALTER TABLE mrr_movement ADD PRIMARY KEY (month);


-- -----------------------------------------------------------------------------
-- Validation
-- -----------------------------------------------------------------------------
-- Retention curve across cohorts. The denominator is the cohorts that have
-- actually reached month N: a cohort that signed up last month has not "lost"
-- its month-6 users, it simply has not got there yet, and including it would
-- drag the curve down for every late month.
SELECT month_number,
       SUM(retained_users)                                      AS retained_users,
       SUM(cohort_users)                                        AS exposed_users,
       COUNT(*)                                                 AS cohorts_observed,
       ROUND(100.0 * SUM(retained_users) / SUM(cohort_users), 1) AS retention_pct
FROM cohort_retention
GROUP BY month_number
ORDER BY month_number;

-- Every cohort must start at 100% in month 0.
SELECT COUNT(*) AS cohorts_not_starting_at_100
FROM cohort_retention
WHERE month_number = 0 AND retention_pct <> 100.0;

-- Churn by plan - is the cheapest plan the leakiest?
SELECT plan,
       COUNT(*)                                                        AS customers,
       COUNT(*) FILTER (WHERE status = 'Churned')                      AS churned,
       ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'Churned') / COUNT(*), 1) AS churn_pct,
       ROUND(SUM(mrr) FILTER (WHERE status = 'Active'), 2)             AS active_mrr
FROM subscriptions
GROUP BY plan
ORDER BY active_mrr DESC;


-- -----------------------------------------------------------------------------
-- Power BI consumption views
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_subscriptions AS
SELECT
    s.*,
    date_trunc('month', s.signup_date)::date AS cohort_month,
    CASE WHEN s.status = 'Churned' THEN 1 ELSE 0 END AS is_churned,
    -- Months survived: closed for churned users, months-to-date for active ones.
    (EXTRACT(YEAR  FROM AGE(COALESCE(s.churn_date, CURRENT_DATE), s.signup_date)) * 12
   + EXTRACT(MONTH FROM AGE(COALESCE(s.churn_date, CURRENT_DATE), s.signup_date)))::int
        AS tenure_months,
    CASE WHEN s.status = 'Active' THEN s.mrr ELSE 0 END AS active_mrr,
    CASE WHEN s.status = 'Churned' THEN s.mrr ELSE 0 END AS lost_mrr
FROM subscriptions s;

CREATE OR REPLACE VIEW vw_cohort_retention AS SELECT * FROM cohort_retention;
CREATE OR REPLACE VIEW vw_mrr_movement   AS SELECT * FROM mrr_movement;
