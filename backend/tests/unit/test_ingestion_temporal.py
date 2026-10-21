import io
import json
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from lzo.ingestion.schemas import FileOptions, TemporalFields, UploadBinding, SourceKey
from lzo.ingestion.files import FileParser
from lzo.artifacts.store import ArtifactStore


def parse(tmp_path, fields, *, zone="UTC", fmt="json"):
    options=FileOptions(format=fmt,encoding="utf-8",delimiter=",",sheet="records",header_row=1,
        records_path="",key_paths=("id",),source_crs=None,source_timezone=zone,data_nature="synthetic",
        temporal_fields=TemporalFields(valid_from_path="start",valid_to_path="end",observed_at_path="seen"))
    record={"id":"one",**fields}
    if fmt=="csv":
        import csv
        output=io.StringIO();writer=csv.DictWriter(output,fieldnames=record);writer.writeheader();writer.writerow(record)
        payload=output.getvalue().encode();media="text/csv"
    elif fmt=="xlsx":
        from openpyxl import Workbook
        book=Workbook();book.active.title="records";book.active.append(list(record));book.active.append(list(record.values()))
        output=io.BytesIO();book.save(output);payload=output.getvalue();media="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        data=[record] if fmt=="json" else {"type":"FeatureCollection","features":[{"type":"Feature","properties":record,"geometry":None}]}
        payload=json.dumps(data).encode();media="application/json"
    store=ArtifactStore(tmp_path,max_upload_bytes=2**20);blob=store.put(io.BytesIO(payload),media)
    key=SourceKey(source_id=uuid4(),source_system="farm",source_instance="one",collection="pond",canonical_key="")
    binding=UploadBinding(id=uuid4(),source_key=key,source_revision=1,blob=blob,options=options,epoch=uuid4(),
        received_at=datetime(2030,1,1,tzinfo=timezone.utc),parser_contract="file-v2")
    parser=FileParser(store,binding)
    return parser.parse(blob,key,options)


@pytest.mark.parametrize("fmt",["csv","xlsx","json","geojson"])
def test_common_file_temporal_extraction_preserves_distinct_clocks(tmp_path,fmt):
    fields={"start":"2026-01-01T08:00:00+08:00","end":"2026-02-01T00:00:00Z","seen":"2026-01-02T00:00:00"}
    result=parse(tmp_path,fields,fmt=fmt)
    assert result.complete, result.issues
    change=result.changes[0]
    assert change.valid_from==datetime(2026,1,1,tzinfo=timezone.utc)
    assert change.observed_at==datetime(2026,1,2,tzinfo=timezone.utc)
    assert change.received_at.year==2030 and change.raw_fields["start"]==fields["start"]


@pytest.mark.parametrize("fields,zone,code",[
    ({"end":None,"seen":None},"UTC","MISSING_PATH"),
    ({"start":"2026-01-01T00:00:00","end":None,"seen":None},None,"INVALID_TEMPORAL_VALUE"),
    ({"start":"2026-11-01T01:30:00","end":None,"seen":None},"America/New_York","INVALID_TEMPORAL_VALUE"),
    ({"start":"2026-03-08T02:30:00","end":None,"seen":None},"America/New_York","INVALID_TEMPORAL_VALUE"),
    ({"start":"2026-02-01T00:00:00Z","end":"2026-01-01T00:00:00Z","seen":None},"UTC","INVALID_TEMPORAL_INTERVAL"),
])
def test_temporal_errors_are_located_incomplete(tmp_path,fields,zone,code):
    result=parse(tmp_path,fields,zone=zone)
    assert not result.complete and result.issues[0].code==code
    assert result.issues[0].locator!="$" and result.issues[0].field_path


def test_null_business_bounds_remain_open_and_observation_unknown(tmp_path):
    result=parse(tmp_path,{"start":None,"end":None,"seen":None})
    assert result.complete
    assert result.changes[0].valid_from is result.changes[0].valid_to is result.changes[0].observed_at is None


def test_unconfigured_options_keep_exact_legacy_canonical_form():
    old={"format":"json","encoding":"utf-8","delimiter":",","sheet":None,"header_row":1,
        "records_path":"","key_paths":["id"],"source_crs":None,"source_timezone":None,"data_nature":"synthetic"}
    options=FileOptions.model_validate(old)
    assert options.model_dump(mode="json")==old
    assert FileOptions.model_validate({**old,"temporal_fields":None}).model_dump(mode="json")==old
