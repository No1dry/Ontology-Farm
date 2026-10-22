"""Bucket provisioning with isolated bootstrap credentials, never printed."""
from pathlib import Path
import time
from uuid import uuid4
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

client = boto3.client('s3', endpoint_url='http://object-store:8333',
    aws_access_key_id=Path('/run/secrets/s3_admin_access_key').read_text().strip(),
    aws_secret_access_key=Path('/run/secrets/s3_admin_secret_key').read_text().strip(),
    config=Config(connect_timeout=1,read_timeout=2,retries={'max_attempts':0}))
for attempt in range(120):
    try:
        client.list_buckets()
        try:
            client.head_bucket(Bucket='lzo-artifacts')
        except ClientError as error:
            if error.response['ResponseMetadata']['HTTPStatusCode'] != 404:
                raise
            client.create_bucket(Bucket='lzo-artifacts')
        key = '_readiness/'+str(uuid4())
        client.put_object(Bucket='lzo-artifacts',Key=key,Body=b'ready')
        body = client.get_object(Bucket='lzo-artifacts',Key=key)['Body']
        try:
            assert body.read()==b'ready'
        finally:
            body.close()
        client.delete_object(Bucket='lzo-artifacts',Key=key)
        print('Private artifact bucket ready')
        break
    except (BotoCoreError, ClientError):
        time.sleep(.5)
else:
    raise RuntimeError('Private object store unavailable')
