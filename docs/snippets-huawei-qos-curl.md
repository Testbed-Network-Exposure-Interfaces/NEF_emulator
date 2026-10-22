# Changing QoS through AsSessionWithQoS (Huawei Slice Manager backend)

These snippets drive the `huwaei` QoS backend
(`backend/app/app/drivers/afSessionWithQos/huawei.py`) through the NEF's
`3gpp-as-session-with-qos` API. Every QoS change becomes a
`PATCH /core/slices/{slice_id}` on the Slice Manager that updates
`uplink_maximum_bitrate_per_ue_kbps` / `downlink_maximum_bitrate_per_ue_kbps`.

## Prerequisites

`.env`:

```bash
QOS__BACKEND=huwaei
QOS__HUWAEI_API_URL=http://10.16.255.55:8000
QOS__HUWAEI_API_USER=notneeded
QOS__HUWAEI_API_PASSWORD=notneeded
QOS__HUWAEI_DEFAULT_AMBRUP=50000   # kbps, applied when the subscription is deleted
QOS__HUWAEI_DEFAULT_AMBRDL=50000   # kbps, applied when the subscription is deleted
```

- The UE must already have a slice association in the Slice Manager (see
  `slice-manager-curl.md`). The driver doesn't create slices yet. With no
  association, the change fails with
  `UE ... has no slice association in Slice Manager for DNN ...`.

### What has to match the Slice Manager

The snippets below use the values from `slice-manager-curl.md`:

| NEF side | Slice Manager side | Value | Must match? |
|----------|--------------------|-------|-------------|
| NEF UE `supi` | `ue_id` in `/core/ues/{ue_id}/...` | `999080100001134` | **Yes**. It's the path parameter of the lookup. |
| Subscription `dnn` | Association `dnn` | `embb` | **Yes**. It's always sent as the `dnn` query filter. |
| Subscription `snssai` (`sst` + `sd`) | Association `snssai` | `{"sst": 1, "sd": "020202"}` ↔ `1-020202` | **Yes, if `sd` is set**. It's sent as the `snssai` filter. If `snssai` or its `sd` is omitted, only `dnn` filters the associations. |
| — | Association `slice_id` | `123124312` | **No**. The NEF never sends a slice ID. It reads `slice_id` from the first association returned by `GET /core/ues/{supi}/slice-associations?dnn=embb&snssai=1-020202` and patches `/core/slices/123124312`. |

The DNN falls back to the UE's `dnn` and then `QOS__HUWAEI_DEFAULT_SLICE_DNN`
when the subscription doesn't set one. The seeded UEs have
`province1.mnc01.mcc202.gprs`, so set `dnn` explicitly.

The UE identifier in the subscription (`ueIpv4Addr` below) is only used to find
the UE in the **NEF** database. It doesn't need to match the association's
`static_ipv4_address`, but the snippets use `10.16.9.5` for consistency. So
create (or edit) a NEF UE with `supi` `999080100001134` and IPv4 `10.16.9.5`.

If several associations match (several QoS templates under the same
DNN/S-NSSAI), the driver uses the first one.

## Get an access token

```bash
NEF=http://localhost:8888

TOKEN=$(curl -s -X POST "$NEF/api/v1/login/access-token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=admin@my-email.com&password=pass' | jq -r .access_token)
```

## Create subscription (applies the initial QoS)

`qosReference` must be a key from
`backend/app/app/core/config/qosCharacteristics.json`. `supportedFeatures: "810"`
turns on `AlternativeQoS_5G` (0x10) and `AltQosWithIndParams_5G` (0x800).
Without them, `altQosReqs` is dropped in the change step below.

```bash
curl -i -X POST "$NEF/nef/api/v1/3gpp-as-session-with-qos/v1/myNetapp/subscriptions" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "ueIpv4Addr": "10.16.9.5",
    "dnn": "embb",
    "snssai": { "sst": 1, "sd": "020202" },
    "notificationDestination": "http://localhost:80/api/v1/utils/session-with-qos/callback",
    "supportedFeatures": "810",
    "qosReference": "QCI_1_voice"
  }'
```

`notificationDestination` points at the NEF's own built-in AsSessionWithQoS
callback receiver (`api/api_v1/endpoints/utils.py`), the same one used in
`docs/test_plan/AsSessionWithQoS/request.json`. The notification is sent from
inside the backend container, so it uses `localhost:80`, not `8888`. Received
notifications show up in the dashboard. To receive them in your own NetApp on
the host instead, use `http://host.docker.internal:<port>/<path>`. The backend
service maps `host.docker.internal` to the host gateway.

The `Location` header and `self` field hold the subscription URL. Keep the
trailing ID:

```bash
SUB_ID=<subscription id>
```

## Get subscriptions

List all subscriptions. This returns every AsSessionWithQoS subscription owned
by the logged-in user, whatever the `scsAsId` in the path. You can also read
`SUB_ID` from the `self` field here.

```bash
curl -s "$NEF/nef/api/v1/3gpp-as-session-with-qos/v1/myNetapp/subscriptions" \
  -H "Authorization: Bearer $TOKEN" | jq
```

Get one subscription by ID:

```bash
curl -s "$NEF/nef/api/v1/3gpp-as-session-with-qos/v1/myNetapp/subscriptions/$SUB_ID" \
  -H "Authorization: Bearer $TOKEN" | jq
```

## Change QoS

### With explicit bitrates (`altQosReqs`)

The NEF uses the first entry. `gbrUl` / `gbrDl` are converted to kbps (rounded
up) and sent to the Slice Manager as the per-UE **maximum** bitrates.

```bash
curl -i -X PATCH "$NEF/nef/api/v1/3gpp-as-session-with-qos/v1/myNetapp/subscriptions/$SUB_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "altQosReqs": [
      {
        "altQosParamSetRef": "high-throughput",
        "gbrUl": "20 Mbps",
        "gbrDl": "100 Mbps"
      }
    ]
  }'
```

### With another QoS reference

```bash
curl -i -X PATCH "$NEF/nef/api/v1/3gpp-as-session-with-qos/v1/myNetapp/subscriptions/$SUB_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{ "qosReference": "QCI_2_voice" }'
```

The NEF only calls the Slice Manager when the resulting QoS profile differs
from the previous one.

## Result

The NEF answers right away, and the Slice Manager change runs in the
background. The driver polls `GET /operations/{request_id}` every
`QOS__HUWAEI_OPERATION_POLL_INTERVAL` seconds. When the operation finishes, it
POSTs to `notificationDestination`:

```json
{
  "transaction": "<subscription self link>",
  "eventReports": [{ "event": "SUCCESSFUL_RESOURCES_ALLOCATION" }]
}
```

On failure, the event is `FAILED_RESOURCES_ALLOCATION`. Check the backend
logs (`make logs-backend`). Each Slice Manager call also logs a `BENCHMARK:` line.

## Revert QoS (delete subscription)

Deleting the subscription patches the slice back to
`QOS__HUWAEI_DEFAULT_AMBRUP` / `QOS__HUWAEI_DEFAULT_AMBRDL`. No notification is
sent for this.

```bash
curl -i -X DELETE "$NEF/nef/api/v1/3gpp-as-session-with-qos/v1/myNetapp/subscriptions/$SUB_ID" \
  -H "Authorization: Bearer $TOKEN"
```
