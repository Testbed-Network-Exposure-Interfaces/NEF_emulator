# WLAN_PERFORMANCE Handler — Test Plan

## Prerequisites

1. Change the backend/app/app/db/init_simple.sh script to include the testing ips

2. Start the stack and seed the DB:
   ```bash
   make up && make db-init
   ```

3. Enable the Prometheus backend (in `.env` or environment):
   ```
   ANALYTICSEXPOSURE__BACKEND=prometheus
   ANALYTICSEXPOSURE__URL=http://prometheus:9090
   ```
   Tests 3 and 5 (negative/no-data cases) work with the stub backend. All others require Prometheus with live traffic data.

4. Obtain a JWT token:
   ```http
   POST http://localhost:8888/api/v1/login/access-token
   Content-Type: application/x-www-form-urlencoded

   username=admin@my-email.com&password=pass
   ```
   Use the returned `access_token` as `Authorization: Bearer <token>` on every request below.

5. Callback receiver (internal URL, reachable from the backend container):
   ```
   http://localhost/api/v1/utils/analyticsexposure/callback
   ```

6. Check received notifications after each test:
   ```http
   GET http://localhost:8888/api/v1/utils/monitoring/notifications
   Authorization: Bearer <token>
   ```

7. Replace `<START>` and `<END>` with ISO-8601 timestamps covering a recent window with traffic,
   e.g. 5 minutes ago and now:
   ```
   <START> = 2025-05-18T12:00:00Z
   <END>   = 2025-05-18T12:05:00Z
   ```

8. Clean up each subscription after testing:
   ```http
   DELETE http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions/<subscriptionId>
   Authorization: Bearer <token>
   ```

---

## Test 1 — Happy path: all UEs, all subsets, PERIODIC

Verifies the full end-to-end flow with no filters applied.

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
      "temporalGranSize": 30
    },
    "tgtUe": { "anyUeInd": true }
  }],
  "analyRepInfo": { "notifMethod": "PERIODIC", "repPeriod": 30 },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-1"
}
```

**Expected:**
- `201 Created` with a `subscriptionId` in the response body.
- Within ≤30s the callback receives an `AnalyticsEventNotification` with:
  - `analyEventNotifs[0].analyEvent = "WLAN_PERFORMANCE"`
  - `wlanInfos[0].wlanPerSsidInfos[0].ssId = "5g-atnog"`
  - `wlanInfos[0].wlanPerSsidInfos[0].wlanPerTsInfos` — list of time-slot entries with `trafficInfo` populated
  - `wlanInfos[0].wlanPerUeIdInfos` — one entry per UE that generated traffic, each with a `supi`

---

## Test 2 — SSID filter: matching SSID

Verifies that `wlanReqs.ssIds` allowing `"5g-atnog"` produces the same result as no filter.

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
      "temporalGranSize": 30,
      "wlanReqs": [{ "ssIds": ["5g-atnog"] }]
    },
    "tgtUe": { "anyUeInd": true }
  }],
  "analyRepInfo": { "notifMethod": "PERIODIC", "repPeriod": 30 },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-2"
}
```

**Expected:** Identical to Test 1 — SSID passes the filter.

---

## Test 3 — SSID filter: non-matching SSID (negative)

Verifies that an unknown SSID suppresses all notifications (works with stub backend).

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
      "temporalGranSize": 30,
      "wlanReqs": [{ "ssIds": ["unknown-ssid"] }]
    },
    "tgtUe": { "anyUeInd": true }
  }],
  "analyRepInfo": { "notifMethod": "PERIODIC", "repPeriod": 30 },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-3"
}
```

**Expected:** No callback notifications delivered. Backend logs contain:
```
WlanPerformanceHandler: Only supported SSID '5g-atnog' not in allowed SSIDs, skipping notification
```

---

## Test 4 — Subset filter: `TRAFFIC_INFO` only

Verifies that only traffic fields are populated when `listOfAnaSubsets` is restricted.

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
      "temporalGranSize": 30,
      "listOfAnaSubsets": ["TRAFFIC_INFO"]
    },
    "tgtUe": { "anyUeInd": true }
  }],
  "analyRepInfo": { "notifMethod": "PERIODIC", "repPeriod": 30 },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-4"
}
```

**Expected:** Each `wlanPerTsInfos` entry has `trafficInfo` populated with `uplinkRate`, `downlinkRate`, `uplinkVolume`, `downlinkVolume`, `totalVolume`. Fields `rssi`, `rtt`, `numberOfUes` are `null`.

---

## Test 5 — Subset filter: unsupported subset only (negative)

`RSSI` has no backing Prometheus query, so no queries are issued and no notification is sent.

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
      "temporalGranSize": 30,
      "listOfAnaSubsets": ["RSSI"]
    },
    "tgtUe": { "anyUeInd": true }
  }],
  "analyRepInfo": { "notifMethod": "PERIODIC", "repPeriod": 30 },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-5"
}
```

**Expected:** No callback notifications. `get_event_metrics` returns an empty list, Prometheus is not queried, `metrics_to_notification` receives an empty result and returns `None`.

---

## Test 6 — Ordering: `TIME_SLOT_START` descending

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
      "temporalGranSize": 30,
      "wlanReqs": [{
        "ssIds": ["5g-atnog"],
        "wlanOrderCriter": "TIME_SLOT_START",
        "order": "DESCENDING"
      }]
    },
    "tgtUe": { "anyUeInd": true }
  }],
  "analyRepInfo": { "notifMethod": "PERIODIC", "repPeriod": 30 },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-6"
}
```

**Expected:** `wlanPerSsidInfos[0].wlanPerTsInfos` is ordered by `tsStart` newest first (each entry's `tsStart` is greater than or equal to the next one's).

---

## Test 7 — Ordering: `TRAFFIC_INFO` ascending

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
      "temporalGranSize": 30,
      "wlanReqs": [{
        "wlanOrderCriter": "TRAFFIC_INFO",
        "order": "ASCENDING"
      }]
    },
    "tgtUe": { "anyUeInd": true }
  }],
  "analyRepInfo": { "notifMethod": "PERIODIC", "repPeriod": 30 },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-7"
}
```

**Expected:** `wlanPerTsInfos` ordered by `uplinkVolume + downlinkVolume` lowest first (time slots with least total traffic appear first).

---

## Test 8 — Per-UE targeting via GPSI

Only UE1 is targeted; `wlanPerUeIdInfos` should contain exactly one entry.

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
      "temporalGranSize": 30
    },
    "tgtUe": { "gpsi": "msisdn-2020100000001" }
  }],
  "analyRepInfo": { "notifMethod": "PERIODIC", "repPeriod": 30 },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-8"
}
```

**Expected:**
- `wlanPerUeIdInfos` contains exactly one entry with `supi = "202010000000001"`.
- `wlanPerSsidInfos` still reflects aggregate traffic across all IPs on the SSID (ALL queries use `.*`).

---

## Test 9 — `ONE_TIME`: single notification then poller stops

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
      "temporalGranSize": 30
    },
    "tgtUe": { "anyUeInd": true }
  }],
  "analyRepInfo": { "notifMethod": "ONE_TIME" },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-9"
}
```

**Expected:**
- Exactly **one** callback notification delivered.
- After delivery, `_send_one_time` sets `state.stop_loop = True` and the poller exits.
- Waiting an additional 30s and re-checking `/monitoring/notifications` shows no further entries for `notifId = "wlan-test-9"`.

---

## Test 10 — `ON_EVENT_DETECTION`: notifies only when data is present

### 10a — Window with no traffic (negative)

Use a time window known to have no traffic (e.g. a future timestamp or a window before any UE was active):

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "2000-01-01T00:00:00Z", "endTs": "2000-01-01T00:05:00Z" },
      "temporalGranSize": 30
    },
    "tgtUe": { "anyUeInd": true }
  }],
  "analyRepInfo": { "notifMethod": "ON_EVENT_DETECTION" },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-10a"
}
```

**Expected:** No callback notifications. `metrics_to_notification` returns `None` (empty result), so `_event_detected` is never reached, and `_send_on_event_detection` has an empty filtered list.

### 10b — Window with live traffic (positive)

```http
POST http://localhost:8888/nef/api/v1/3gpp-analyticsexposure/v1/myApp/subscriptions
Authorization: Bearer <token>
Content-Type: application/json

{
  "analyEventsSubs": [{
    "analyEvent": "WLAN_PERFORMANCE",
    "analyEventFilter": {
      "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
      "temporalGranSize": 30
    },
    "tgtUe": { "anyUeInd": true }
  }],
  "analyRepInfo": { "notifMethod": "ON_EVENT_DETECTION" },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-10b"
}
```

**Expected:**
- Callback notification delivered (poller checks every 10s).
- `_event_detected` returns `True` because `wlanInfos` is non-empty.
- Notification continues to fire every 10s as long as data is present in the query window (same fixed `startTs`/`endTs`).

### 10c — Verify `_event_detected` gate: mixed events in one subscription

Subscribe to a second event alongside `WLAN_PERFORMANCE` where the second event produces no data. Confirm only the WLAN notification is included in the payload:

```http
{
  "analyEventsSubs": [
    {
      "analyEvent": "WLAN_PERFORMANCE",
      "analyEventFilter": {
        "extraReportReq": { "startTs": "<START>", "endTs": "<END>" },
        "temporalGranSize": 30
      },
      "tgtUe": { "anyUeInd": true }
    }
  ],
  "analyRepInfo": { "notifMethod": "ON_EVENT_DETECTION" },
  "notifUri": "http://localhost/api/v1/utils/analyticsexposure/callback",
  "notifId": "wlan-test-10c"
}
```

**Expected:** `analyEventNotifs` in the payload contains only the `WLAN_PERFORMANCE` entry. The `_send_on_event_detection` filtered list excludes any notif whose `_event_detected` check returns `False`.