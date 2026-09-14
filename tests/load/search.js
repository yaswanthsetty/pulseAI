// k6 load test — PulseAI /api/v1/search (semantic search with rerank).
//
// Usage:
//   k6 run tests/load/search.js                 # smoke (default env below)
//   k6 run -e BASE_URL=http://localhost:8090 -e TOKEN=<jwt> tests/load/search.js
//
// Get a token: curl -X POST $BASE_URL/api/v1/auth/login \
//   -H 'Content-Type: application/json' -d '{"email":"...","password":"..."}'

import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8090";
const TOKEN = __ENV.TOKEN || "";

export const options = {
  scenarios: {
    ramp: {
      executor: "ramping-vus",
      startVUs: 1,
      stages: [
        { duration: "30s", target: 5 }, // warm up (model load + cache)
        { duration: "1m", target: 10 }, // sustained
        { duration: "30s", target: 20 }, // push
        { duration: "30s", target: 0 },
      ],
    },
  },
  thresholds: {
    // Gate: 95% of searches under 5s (CPU rerank is the bottleneck),
    // and fewer than 1% failed requests.
    http_req_duration: ["p(95)<5000"],
    http_req_failed: ["rate<0.01"],
  },
};

const QUERIES = [
  "AI regulation in the European Union",
  "OpenAI new model release",
  "semiconductor export controls",
  "AI safety and alignment research",
  "self-driving cars accidents",
];

export default function () {
  const query = QUERIES[Math.floor(Math.random() * QUERIES.length)];
  const headers = { "Content-Type": "application/json" };
  if (TOKEN) headers["Authorization"] = `Bearer ${TOKEN}`;

  const res = http.post(
    `${BASE_URL}/api/v1/search`,
    JSON.stringify({ query, limit: 5, mode: "hybrid" }),
    { headers }
  );

  check(res, {
    "status 200": (r) => r.status === 200,
    "has results": (r) => {
      try {
        const body = r.json();
        return (Array.isArray(body) ? body : body.results || []).length > 0;
      } catch {
        return false;
      }
    },
  });

  sleep(1);
}
