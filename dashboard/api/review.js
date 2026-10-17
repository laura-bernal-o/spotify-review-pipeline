// GET /api/review?id=<review_id> — one review traced through enrichment and issue membership.
import { sql, send, fail } from "./_db.js";

export default async function handler(req, res) {
  const id = String(req.query.id || "");
  try {
    const [review] = await sql`SELECT r.*, i.name AS issue_name, i.rank AS issue_rank
                               FROM reviews r LEFT JOIN issues i USING (issue_id) WHERE r.review_id = ${id}`;
    if (!review) return res.status(404).json({ error: "review not in classified scope" });
    send(res, review);
  } catch (e) { fail(res, e); }
}
