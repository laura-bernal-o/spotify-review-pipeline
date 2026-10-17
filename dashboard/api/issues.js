// GET /api/issues — the full baseline ranking (recomputed by code from saved membership).
import { sql, send, fail } from "./_db.js";

export default async function handler(req, res) {
  try {
    send(res, await sql`SELECT issue_id, rank, name, description, topic, complaint_count, severity_sum,
                               mean_severity, priority_score FROM issues ORDER BY rank`);
  } catch (e) { fail(res, e); }
}
