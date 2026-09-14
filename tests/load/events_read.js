// k6 load test — PulseAI read endpoints (events list, stats, trending).
//
// Usage:
//   k6 run tests/load/events_read.js
//   k6 run -e BASE_URL=http://localhost:8090 tests/load/events_read.js
//
// These are the endpoints the dashboard and events page hit on every load —
// they must stay fast without any auth.

import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8090";

export const options = {
  scenarios: {
    browse: {
      executor: "ramping-vus",
      startVUs: 1,
      stages: [
        { duration: "20s", target: 10 },
        { duration: "1m", target: 30 },
        { duration: "30s", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<1000"],
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  const checks = {
    "events 200": (r) => r.status === 200,
  };

  const events = http.get(`${BASE_URL}/api/v1/events?limit=20&page=1`);
  check(events, {
    "events 200": (r) => r.status === 200,
    "events payload": (r) => {
      try {
        return r.json().items !== undefined;
      } catch {
        return false;
      }
    },
  });

  const stats = http.get(`${BASE_URL}/api/v1/insights/stats`);
  check(stats, checks);

  const trending = http.get(`${BASE_URL}/api/v1/insights/trending?limit=10`);
  check(trending, { "trending 200": (r) => r.status === 200 });

  sleep(1);
}
