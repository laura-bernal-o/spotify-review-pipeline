// Shared read-only database access for the dashboard API. DATABASE_URL is a Vercel env var.
import { neon } from "@neondatabase/serverless";

export const sql = neon(process.env.DATABASE_URL);

export function send(res, data) {
  res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=3600");
  res.status(200).json(data);
}

export function fail(res, err) {
  console.error(err);
  res.status(500).json({ error: "database query failed" });
}
