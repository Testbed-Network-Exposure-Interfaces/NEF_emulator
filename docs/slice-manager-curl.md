# Slice Manager Curl Snippets

### CREATE SLICE
```md
curl -i -X POST \
  http://10.16.255.55:8000/core/slices \
  -H 'Content-Type: application/json' \
  -d '{
    "slice_id": "embb-slice",
    "coverage_area": [
      "it"
    ],
    "sst": 1,
    "sd": "020202",
    "dnn": "embb",
    "priority_label": 1,
    "ue_mobility_level": "nomadic",
    "reliability": 99.9,
    "downlink_latency_ms": 15,
    "uplink_latency_ms": 15,
    "downlink_guaranteed_bitrate_per_ue_kbps": 1000,
    "uplink_guaranteed_bitrate_per_ue_kbps": 1000,
    "downlink_maximum_bitrate_per_ue_kbps": 20000,
    "uplink_maximum_bitrate_per_ue_kbps": 20000,
    "n6_protection": [{"type": "PCC Rule", "name": "rule_any"}]
  }'
```

### PATCH SLICE
```bash
curl -i -X PATCH \
  'http://10.16.255.55:8000/core/slices/embb' \
  -H 'Content-Type: application/json' \
  -d '{
    "ue_mobility_level": "nomadic",
    "reliability": 99.9,
    "priority_label": 60,
    "downlink_latency_ms": 15,
    "uplink_latency_ms": 15,
    "downlink_guaranteed_bitrate_per_ue_kbps": 1000,
    "uplink_guaranteed_bitrate_per_ue_kbps": 1000,
    "downlink_maximum_bitrate_per_ue_kbps": 4000,
    "uplink_maximum_bitrate_per_ue_kbps": 4000,
    "uplink_max_packet_size_bytes": 0,
    "scheduling_type": "BASIC_SCH",
    "uplink_deterministic_communication": "NOT_SUPPORTED",
    "uplink_deterministic_periodicity": 0
  }'
```
  
### GET OPERATION STATUS
```md
curl -i -X GET 'http://10.16.255.55:8000/operations/8c085428-16c9-47fb-8b48-5a83cb8d1702'
```

### NEW UE SLICE ASSOCIATION
```md
curl -i -X POST \
  http://10.16.255.55:8000/core/ues/999080100001127/slice-associations \
  -H 'Content-Type: application/json' \
  -d '{
    "slice_id": "embb-slice",
    "snssai": "1-020202",
    "dnn": "embb",
    "static_ipv4_address": "10.16.9.5",
    "access_mobility_data": true,
    "uplink_aggregate_maximum_bitrate_kbps": 1000,
    "downlink_aggregate_maximum_bitrate_kbps": 3000
  }'
```
  
### GET ALL ASSOCIATIONS FOR A SLICE
```md
curl -i -X GET 'http://10.16.255.55:8000/core/slices/embb-slice/ues'
```

### GET ALL ASSOCIATIONS FOR A UE
```md
curl -i -X GET 'http://10.16.255.55:8000/core/ues/999080100001127/slice-associations'
```

### GET QoS FOR A UE
```md
curl -i -X GET 'http://10.16.255.55:8000/core/ues/999080100001127/qos'
```

### GET USER PLANE INFO FOR UE (INCLUDING IP)
```md
curl -i -X GET 'http://10.16.255.55:8000/core/ues/999080100001127/user-plane-info?detailed=true'
```

### DELETE UE SLICE ASSOCIATION
```md
curl -i -X DELETE 'http://10.16.255.55:8000/core/ues/999080100001127/slice-associations/embb-slice'
```

### DELETE SLICE
```md
curl -i -X DELETE 'http://10.16.255.55:8000/core/slices/embb-slice'
```

### OLD N6 PROTECTION
```md
"n6_protection": [
  {
    "type": "firewall",
    "name": "allow-video-ingest",
    "priority": 1,
    "server_ip": "10.16.9.10",
    "mask_len": 32,
    "start_port": 443,
    "end_port": 443,
    "uplink_guaranteed_bitrate_kbps": 10000,
    "downlink_guaranteed_bitrate_kbps": 50000,
    "uplink_maximum_bitrate_kbps": 20000,
    "downlink_maximum_bitrate_kbps": 100000,
    "protocol": "tcp",
    "pass_block": "pass"
  }
]
'
```
