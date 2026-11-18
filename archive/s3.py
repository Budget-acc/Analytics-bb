"""
Archive bucket client.

Two ways to connect, chosen by ARCHIVE_ROLE_ARN in .env:

  * ARCHIVE_ROLE_ARN empty  -> bucket is in the SAME AWS account.
    The app uses its own keys (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
    or the server's IAM role. Good for local testing.

  * ARCHIVE_ROLE_ARN set    -> bucket is in ANOTHER AWS account.
    The app signs in with its own identity, then assumes the writer role
    in the bucket's account. The credentials refresh by themselves, so a
    long nightly run never fails half way.
"""

import boto3
from botocore.credentials import RefreshableCredentials
from botocore.session import get_session
from django.conf import settings


def _source_session(region):
    """The app's own AWS identity (keys from settings, or the server IAM role)."""
    key = getattr(settings, "ARCHIVE_SOURCE_AWS_ACCESS_KEY_ID", None) or getattr(settings, "AWS_ACCESS_KEY_ID", None)
    secret = getattr(settings, "ARCHIVE_SOURCE_AWS_SECRET_ACCESS_KEY", None) or getattr(settings, "AWS_SECRET_ACCESS_KEY", None)
    if key and secret:
        return boto3.Session(aws_access_key_id=key, aws_secret_access_key=secret, region_name=region)
    return boto3.Session(region_name=region)  # server IAM role / env vars


def _assumed_role_session(role_arn, region, external_id=None):
    """boto3 session for the writer role in the bucket's account, auto-refreshing."""
    sts = _source_session(region).client("sts", region_name=region)

    def refresh():
        params = {
            "RoleArn": role_arn,
            "RoleSessionName": "bookiebuddy-archive-writer",
            "DurationSeconds": 3600,
        }
        if external_id:
            params["ExternalId"] = external_id
        creds = sts.assume_role(**params)["Credentials"]
        return {
            "access_key": creds["AccessKeyId"],
            "secret_key": creds["SecretAccessKey"],
            "token": creds["SessionToken"],
            "expiry_time": creds["Expiration"].isoformat(),
        }

    credentials = RefreshableCredentials.create_from_metadata(
        metadata=refresh(), refresh_using=refresh, method="sts-assume-role"
    )
    botocore_session = get_session()
    botocore_session._credentials = credentials
    botocore_session.set_config_variable("region", region)
    return boto3.Session(botocore_session=botocore_session)


class ArchiveBucket:
    def __init__(self):
        self.bucket = settings.ARCHIVE_S3_BUCKET
        self.region = getattr(settings, "ARCHIVE_S3_REGION", "ap-south-1")
        self.kms_key = getattr(settings, "ARCHIVE_KMS_KEY_ID", None)
        self.expected_owner = getattr(settings, "ARCHIVE_AWS_ACCOUNT_ID", None)

        role_arn = getattr(settings, "ARCHIVE_ROLE_ARN", None)
        if role_arn:
            # Bucket in ANOTHER AWS account: use the writer role there.
            session = _assumed_role_session(role_arn, self.region, getattr(settings, "ARCHIVE_EXTERNAL_ID", None))
        else:
            # Bucket in the SAME AWS account: use the app's own keys / server role.
            session = _source_session(self.region)
        self.client = session.client("s3", region_name=self.region)

    def _owner(self):
        # When ARCHIVE_AWS_ACCOUNT_ID is set, S3 refuses the call if the bucket
        # belongs to any other account (protects against a wrong / look-alike bucket).
        return {"ExpectedBucketOwner": self.expected_owner} if self.expected_owner else {}

    def put(self, key, body, content_type="application/json"):
        extra = {}
        if self.kms_key:
            extra = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key}
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType=content_type, **extra, **self._owner()
        )

    def get(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key, **self._owner())["Body"].read()

    def list_manifests(self, prefix):
        """Every manifest.json key under `prefix` (used by the analytics app)."""
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix, **self._owner()):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith("/manifest.json"):
                    yield obj["Key"]

    def check_access(self):
        """Write and read one small file. Run once after setup."""
        key = f"{getattr(settings, 'ARCHIVE_S3_PREFIX', 'booking-archive/v1/')}_healthcheck.txt"
        self.put(key, b"ok", content_type="text/plain")
        return self.get(key) == b"ok"