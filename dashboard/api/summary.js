// GET /api/summary — overall metrics, run provenance, verifier agreement, AI recommendations + claims, monthly trend.
import { sql, send, fail } from "./_db.js";

export default async function handler(req, res) {
  try {
    const [metrics, info, recs, claims, monthly] = await Promise.all([
      sql`SELECT key, value FROM overall_metrics`,
      sql`SELECT key, value FROM run_info`,
      sql`SELECT position, title, rationale, claim_ids FROM recommendations ORDER BY position`,
      sql`SELECT c.claim_id, c.issue_id, c.metric, c.value, i.name FROM claims c JOIN issues i USING (issue_id) ORDER BY c.claim_id`,
      sql`SELECT month, classified, complaints FROM monthly ORDER BY month`,
    ]);
    send(res, {
      metrics: Object.fromEntries(metrics.map((r) => [r.key, r.value])),
      info: Object.fromEntries(info.map((r) => [r.key, r.value])),
      recommendations: recs,
      claims,
      monthly,
    });
  } catch (e) { fail(res, e); }
}
