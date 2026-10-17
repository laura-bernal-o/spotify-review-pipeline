// GET /api/issue?id=<issue_id>&limit=20 — one issue plus its highest-severity member reviews (evidence).
import { sql, send, fail } from "./_db.js";

export default async function handler(req, res) {
  const id = String(req.query.id || "");
  const limit = Math.min(Math.max(parseInt(req.query.limit || "20", 10) || 20, 1), 100);
  try {
    const [issue] = await sql`SELECT * FROM issues WHERE issue_id = ${id}`;
    if (!issue) return res.status(404).json({ error: "unknown issue" });
    const reviews = await sql`SELECT review_id, evidence_quote, severity, intent, sentiment, review_rating,
                                     review_timestamp, needs_review
                              FROM reviews WHERE issue_id = ${id}
                              ORDER BY severity DESC, review_id LIMIT ${limit}`;
    send(res, { issue, reviews });
  } catch (e) { fail(res, e); }
}
