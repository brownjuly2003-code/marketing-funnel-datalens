SELECT
    branch,
    toDate(order_date) AS order_day,
    count() AS orders
FROM rv.bv_order_canonical_mat
WHERE header_source IS NOT NULL
GROUP BY branch, order_day
