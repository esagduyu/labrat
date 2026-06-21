---
kind: scent
domain: ecommerce_sales
tables: [orders, customers, products, events]
confidence: verified
---

## Quick Reference
The ecommerce dataset tracks customer orders, the products ordered, and product/usage
events. Core grain: `orders` is one row per placed order (`order_id`); `total_amount`
holds the order's revenue as a decimal dollar amount. Exclude test rows (`is_test = true`)
and non-completed orders from revenue figures.

## Dimensions
- **Order status** — `orders.status` encodes the order lifecycle (inspect the distinct
  values before filtering; non-completed states such as cancelled should be excluded from
  revenue).
- **Test data** — `customers`, `orders`, and `events` each carry an `is_test BOOLEAN`
  flag; filter `is_test = false` for real metrics.
- **Money** — `orders.total_amount` and `products.price` are `DECIMAL(10,2)` dollar
  amounts (no minor-unit/cents conversion needed).

## Key Tables
- **orders** — grain: one row per order (`order_id`). Join to customers on
  `orders.customer_id = customers.customer_id`; to products on
  `orders.product_id = products.product_id`.
- **customers** — grain: one row per customer (`customer_id`).
- **products** — grain: one row per product (`product_id`).
- **events** — grain: one row per event (`event_id`); `wau` is the weekly-active-users
  measure. Join to customers on `events.customer_id = customers.customer_id`.

## Gotchas
- Revenue is `orders.total_amount` directly — there is no separate line-item table; do not
  look for `order_items`.
- Test rows are present in `customers`/`orders`/`events`; forgetting `is_test = false`
  inflates every count and total.
- Non-completed orders still have rows — filter by `orders.status` for revenue questions
  (check the distinct status values first).

## Best Practices
- Net revenue = `SUM(orders.total_amount)` over non-test, completed orders.
- Confirm join cardinality with `verify_join` before trusting an orders ↔ customers join.

## Cross-References
- Run `profile_dataset` first for ground-truth schema/row counts; this doc states intent,
  the profiler states fact.
