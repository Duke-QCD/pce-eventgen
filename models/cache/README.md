# Event cache backends

Use [`models/campaign.py`](../campaign.py) with your selected INI for normal
operations. Start with the [handbook](../../docs/README.md),
[campaign procedure](../../docs/nersc-campaign.md), and
[complete backend flag index](../../docs/reference/cli-cache.md).

| Backend | Responsibility |
|---|---|
| `survey_raw_designs.py` | Scan raw terminal markers/counts and join cache/reap history; write full survey JSON |
| `make_cache_reap_manifest.py` | Select cache candidates from survey plus record policy; no cache execution or deletion |
| `cache_one_design.py` | Parse OSCAR, pack v3 statistics, audit particle/species/structure/count conventions and validate against raw; never delete raw |
| `publish_cache_to_cfs.py` | Compress/publish, verify shards/metadata/provenance and write `cache.verified`; never delete raw or staging |
| `cache_reap_one_design.py` | Coordinate build/reuse, publication and reaper audit; write stage verdict records; separate `cache` and `reap` commands |
| `reap_raw_particle_lists.py` | Independently verify published payload and current raw state; explicit applied execution removes eligible event directories |

A candidate manifest, published marker, clean wrapper verdict, review selection,
and applied reap are separate pieces of evidence. The publisher is the sole
writer of `cache.verified`; the reaper rechecks current state. Preserve raw
events on any quarantine or ambiguous record history. Full paths/products are
in the [file reference](../../docs/reference/files.md).

Calling a backend directly can execute immediately: in particular,
`cache_reap_one_design.py cache` is not the campaign's submission preview.
Read the selected parser/help and source before direct maintenance use.
