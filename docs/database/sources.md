# Sources, pins and reuse boundaries

Runtime versions/image/archive hashes are fixed in [infra/versions.lock.json](../../infra/versions.lock.json); Python and npm dependencies use their checked-in lockfiles. Native acceptance uses PostgreSQL 17.11/PostGIS 3.6.2, Fuseki 6.2.0/Java 21, SeaweedFS 4.48, Keycloak 26.8.1, MySQL 8.4.11, Python 3.12.7 and Node 24.22.0. Actual result files identify the measured code and environment; a version pin alone is not a runtime test.

The approved design retains these fixed external engineering references:

| Source | Fixed reference and use | Limit |
| --- | --- | --- |
| Original farm platform | [`No1dry/liangzihu-farm-platform` at `113e6491c488ebb3fc0529cf56c446e69274ce4f`](https://github.com/No1dry/liangzihu-farm-platform/tree/113e6491c488ebb3fc0529cf56c446e69274ce4f): terrain/measurement/file-package/device handoff concepts | Engineering inputs, not surveyed field truth or certified device compatibility. Repository/file reuse rights remain unconfirmed; no wholesale source copy. |
| Ontology traceability reference | [`lianggeming14/liangzihu-ontology-traceability` at `11686c54375af9bf4450c3e1cbc237cd003bdaf7`](https://github.com/lianggeming14/liangzihu-ontology-traceability/tree/11686c54375af9bf4450c3e1cbc237cd003bdaf7): source identity, device/channel/time/provenance distinctions | Different architecture is not a replacement for the approved Fuseki route. Field integration and complete reuse rights are not certified. |
| Private S3 implementation | [SeaweedFS 4.48 release](https://github.com/seaweedfs/seaweedfs/releases/tag/4.48), [S3 interface documentation](https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API) | Pinned verified binary/image and tested supported paths; not a claim of every AWS feature, object lock, GC or HA. |
| Reference ontology examples | [W3C SSN/SOSA recommendation, 2017-10-19](https://www.w3.org/TR/2017/REC-vocab-ssn-20171019/) | Catalog examples contain original authored synthetic Turtle, explicitly CC0-1.0 for those example bytes. Referenced publications retain their own terms; no vendor manuals are copied. |

Map fixtures remain intentionally synthetic with [their original license record](../../tests/fixtures/maps/LICENSES.md). Bundled fonts retain their existing notices. Real manuals, aerial imagery, GIS boundaries, measurements, protocol captures and device model/firmware evidence need the sources/rights/quality inputs in [real-data-questions.json](real-data-questions.json). A synthetic test fixture never establishes installed equipment counts, capacity/retention requirements, true boundaries or vertical precision.
