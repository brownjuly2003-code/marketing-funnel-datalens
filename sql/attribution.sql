-- SQLite reference. Click window: 7d; impression fallback: 24h; strict past only.
WITH conversions AS (
    SELECT * FROM events WHERE name = 'subscription'
), eligible AS (
    SELECT c.event_id AS conversion_id, t.event_id AS touch_id, t.channel, t.at,
           c.at AS converted_at
    FROM conversions c JOIN events t ON t.user_id = c.user_id AND t.at < c.at
    WHERE (t.name = 'click' AND t.at >= c.at - 604800)
       OR (t.name = 'impression' AND t.at >= c.at - 86400 AND NOT EXISTS (
           SELECT 1 FROM events k WHERE k.user_id = c.user_id AND k.name = 'click'
             AND k.at >= c.at - 604800 AND k.at < c.at))
), base AS (
    SELECT c.event_id AS conversion_id, coalesce(e.channel, 'unattributed') AS channel,
           row_number() OVER (PARTITION BY c.event_id ORDER BY e.at DESC, e.touch_id DESC) AS rank,
           count(*) OVER (PARTITION BY c.event_id) AS touches,
           CASE WHEN e.at IS NULL THEN 1.0 ELSE exp2(-(c.at - e.at) / 86400.0) END AS decay
    FROM conversions c LEFT JOIN eligible e ON e.conversion_id = c.event_id
), weighted AS (
    SELECT conversion_id, channel, touches, 'last_click' AS model,
           CASE WHEN rank = 1 THEN 1.0 ELSE 0.0 END AS weight FROM base
    UNION ALL
    SELECT conversion_id, channel, touches, 'linear', 1.0 / touches FROM base
    UNION ALL
    SELECT conversion_id, channel, touches, 'time_decay',
           decay / sum(decay) OVER (PARTITION BY conversion_id) FROM base
)
-- channel_touches / path_touches is the exact linear share used for integer money quotas.
SELECT conversion_id, model, channel, sum(weight) AS weight,
       count(*) AS channel_touches, max(touches) AS path_touches
FROM weighted
GROUP BY conversion_id, model, channel HAVING sum(weight) > 0
ORDER BY conversion_id, model, channel;
