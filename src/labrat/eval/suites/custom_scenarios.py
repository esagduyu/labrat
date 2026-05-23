"""Hand-written custom eval scenarios (M26).

22 end-to-end scenarios across domains: e-commerce, ad-tech, SaaS metrics,
logs. Each has a question, expected SQL, and optional gold result / rubric.
"""

from __future__ import annotations

from labrat.eval.models import EvalCase

_CASES: list[EvalCase] = [
    # ── e-commerce ────────────────────────────────────────────────────────────
    EvalCase(
        id="ec-001",
        question="How many orders were placed in the last 30 days?",
        expected_sql=(
            "SELECT COUNT(*) FROM orders WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'"
        ),
        domain="e-commerce",
        rubric="Must filter on created_at with 30-day window",
    ),
    EvalCase(
        id="ec-002",
        question="What is the average order value?",
        expected_sql="SELECT AVG(total_amount) FROM orders",
        domain="e-commerce",
    ),
    EvalCase(
        id="ec-003",
        question="List the top 5 products by revenue.",
        expected_sql=(
            "SELECT product_id, SUM(quantity * unit_price) AS revenue"
            " FROM order_items GROUP BY product_id"
            " ORDER BY revenue DESC LIMIT 5"
        ),
        domain="e-commerce",
        rubric="Must aggregate by product, order by revenue desc, limit 5",
    ),
    EvalCase(
        id="ec-004",
        question="Which customers have placed more than 10 orders?",
        expected_sql=(
            "SELECT customer_id, COUNT(*) AS order_count"
            " FROM orders GROUP BY customer_id HAVING COUNT(*) > 10"
        ),
        domain="e-commerce",
    ),
    EvalCase(
        id="ec-005",
        question="What is the refund rate for last month?",
        expected_sql=(
            "SELECT COUNT(CASE WHEN status = 'refunded' THEN 1 END)::float"
            " / COUNT(*)"
            " FROM orders"
            " WHERE created_at >= DATE_TRUNC('month',"
            " CURRENT_DATE - INTERVAL '1 month')"
            " AND created_at < DATE_TRUNC('month', CURRENT_DATE)"
        ),
        domain="e-commerce",
        rubric="Must compute ratio of refunded orders, scoped to last calendar month",
    ),
    # ── ad-tech ───────────────────────────────────────────────────────────────
    EvalCase(
        id="ad-001",
        question="What is the overall click-through rate across all campaigns?",
        expected_sql=(
            "SELECT SUM(clicks)::float / NULLIF(SUM(impressions), 0) AS ctr FROM ad_campaigns"
        ),
        domain="ad-tech",
        rubric="Must use NULLIF to avoid division by zero",
    ),
    EvalCase(
        id="ad-002",
        question="Which campaign had the highest spend yesterday?",
        expected_sql=(
            "SELECT campaign_id, SUM(spend) AS total_spend"
            " FROM ad_spend WHERE date = CURRENT_DATE - 1"
            " GROUP BY campaign_id ORDER BY total_spend DESC LIMIT 1"
        ),
        domain="ad-tech",
    ),
    EvalCase(
        id="ad-003",
        question="Calculate cost-per-acquisition for each campaign.",
        expected_sql=(
            "SELECT campaign_id,"
            " SUM(spend) / NULLIF(SUM(conversions), 0) AS cpa"
            " FROM ad_campaigns GROUP BY campaign_id"
        ),
        domain="ad-tech",
        rubric="CPA = spend / conversions per campaign",
    ),
    EvalCase(
        id="ad-004",
        question="Show weekly impressions trend for the past 8 weeks.",
        expected_sql=(
            "SELECT DATE_TRUNC('week', date) AS week, SUM(impressions)"
            " FROM ad_spend GROUP BY 1 ORDER BY 1"
        ),
        domain="ad-tech",
    ),
    EvalCase(
        id="ad-005",
        question="Which ad channels have a return on ad spend above 3?",
        expected_sql=(
            "SELECT channel, SUM(revenue) / NULLIF(SUM(spend), 0) AS roas"
            " FROM ad_performance GROUP BY channel"
            " HAVING SUM(revenue) / NULLIF(SUM(spend), 0) > 3"
        ),
        domain="ad-tech",
    ),
    # ── SaaS metrics ──────────────────────────────────────────────────────────
    EvalCase(
        id="saas-001",
        question="What is the current monthly recurring revenue?",
        expected_sql="SELECT SUM(monthly_amount) FROM subscriptions WHERE status = 'active'",
        domain="saas",
        rubric="MRR = sum of active subscription monthly amounts",
    ),
    EvalCase(
        id="saas-002",
        question="How many new subscriptions were created this month?",
        expected_sql=(
            "SELECT COUNT(*) FROM subscriptions"
            " WHERE DATE_TRUNC('month', created_at)"
            " = DATE_TRUNC('month', CURRENT_DATE)"
        ),
        domain="saas",
    ),
    EvalCase(
        id="saas-003",
        question="What is the churn rate for last quarter?",
        expected_sql=(
            "SELECT COUNT(CASE WHEN status = 'cancelled' THEN 1 END)::float"
            " / NULLIF(COUNT(*), 0) FROM subscriptions"
            " WHERE DATE_TRUNC('quarter', updated_at)"
            " = DATE_TRUNC('quarter', CURRENT_DATE - INTERVAL '3 months')"
        ),
        domain="saas",
        rubric="Churn rate = cancelled / total subscriptions in prior quarter",
    ),
    EvalCase(
        id="saas-004",
        question="Show average revenue per user by plan tier.",
        expected_sql=(
            "SELECT plan_tier, AVG(monthly_amount) AS arpu"
            " FROM subscriptions WHERE status = 'active' GROUP BY plan_tier"
        ),
        domain="saas",
    ),
    EvalCase(
        id="saas-005",
        question="Which users have been active in the last 7 days?",
        expected_sql=(
            "SELECT DISTINCT user_id FROM user_events"
            " WHERE occurred_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'"
        ),
        domain="saas",
    ),
    # ── log analysis ──────────────────────────────────────────────────────────
    EvalCase(
        id="log-001",
        question="How many HTTP 500 errors occurred in the last hour?",
        expected_sql=(
            "SELECT COUNT(*) FROM http_logs"
            " WHERE status_code = 500"
            " AND timestamp >= NOW() - INTERVAL '1 hour'"
        ),
        domain="logs",
    ),
    EvalCase(
        id="log-002",
        question="What are the top 10 slowest API endpoints by average response time?",
        expected_sql=(
            "SELECT endpoint, AVG(response_time_ms) AS avg_ms"
            " FROM http_logs GROUP BY endpoint"
            " ORDER BY avg_ms DESC LIMIT 10"
        ),
        domain="logs",
    ),
    EvalCase(
        id="log-003",
        question="Show error rate per service over the past 24 hours.",
        expected_sql=(
            "SELECT service,"
            " SUM(CASE WHEN level = 'ERROR' THEN 1 ELSE 0 END)::float"
            " / NULLIF(COUNT(*), 0) AS error_rate"
            " FROM app_logs WHERE timestamp >= NOW() - INTERVAL '24 hours'"
            " GROUP BY service"
        ),
        domain="logs",
        rubric="Error rate = ERROR count / total log lines per service",
    ),
    EvalCase(
        id="log-004",
        question="Which users triggered the most failed login attempts today?",
        expected_sql=(
            "SELECT user_id, COUNT(*) AS failed_attempts FROM auth_logs"
            " WHERE event = 'login_failed'"
            " AND DATE(timestamp) = CURRENT_DATE"
            " GROUP BY user_id ORDER BY failed_attempts DESC LIMIT 20"
        ),
        domain="logs",
    ),
    EvalCase(
        id="log-005",
        question="Identify the peak traffic hour for yesterday.",
        expected_sql=(
            "SELECT DATE_TRUNC('hour', timestamp) AS hour,"
            " COUNT(*) AS requests"
            " FROM http_logs WHERE DATE(timestamp) = CURRENT_DATE - 1"
            " GROUP BY 1 ORDER BY requests DESC LIMIT 1"
        ),
        domain="logs",
    ),
    # ── cross-domain ──────────────────────────────────────────────────────────
    EvalCase(
        id="cross-001",
        question="Show me the distribution of users by signup month.",
        expected_sql=(
            "SELECT DATE_TRUNC('month', created_at) AS month,"
            " COUNT(*) AS users FROM users GROUP BY 1 ORDER BY 1"
        ),
        domain="general",
    ),
    EvalCase(
        id="cross-002",
        question="What percentage of users have made at least one purchase?",
        expected_sql=(
            "SELECT COUNT(DISTINCT o.user_id)::float"
            " / NULLIF(COUNT(DISTINCT u.id), 0)"
            " FROM users u LEFT JOIN orders o ON u.id = o.user_id"
        ),
        domain="general",
        rubric="Must use LEFT JOIN and NULLIF for safe division",
    ),
]


class CustomScenariosSuite:
    """Hand-written scenarios across e-commerce, ad-tech, SaaS, and logs."""

    suite_name = "custom-scenarios"
    cases: list[EvalCase] = _CASES
