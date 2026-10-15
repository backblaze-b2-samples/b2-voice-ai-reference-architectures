# B2 reference architectures for voice and speech AI

Reference architectures for storing, versioning and delivering voice and speech AI data on Backblaze B2: training datasets, generated audio and voice agent session recordings. Current B2 product behavior is documented at [backblaze.com/docs](https://www.backblaze.com/docs/cloud-storage).

## Architectures

<!-- catalog:architectures:start -->
No architectures are listed.
<!-- catalog:architectures:end -->

## Where state lives

Architecture definitions live in `catalog/architectures.yaml`. The tables on this page and the files under `docs/`, `skill/references/` and `llms.txt` are generated from it by `tools/generate_catalog.py`.

## Clean up

The tooling in this repository creates no buckets, application keys or lifecycle rules.

## Known limitations

Isolation between tenants is not covered: the layouts assume a single organization.

## Partner integration options

<!-- catalog:integrations:start -->
No integrations are listed.
<!-- catalog:integrations:end -->
