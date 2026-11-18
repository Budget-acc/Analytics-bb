# s3_service.py
import boto3
from django.conf import settings
from botocore.exceptions import ClientError
from botocore.config import Config
import logging
import threading
from boto3.s3.transfer import TransferConfig
import io

logger = logging.getLogger("api_requests")

class S3Service:
    """Ultra-fast S3 service with connection pooling."""
    
    _client = None
    _client_lock = threading.Lock()
    
    @classmethod
    def get_client(cls):
        """Get singleton S3 client with optimal configuration."""
        if cls._client is None:
            with cls._client_lock:
                if cls._client is None:
                    config = Config(
                        max_pool_connections=100,
                        retries={'max_attempts': 3, 'mode': 'standard'},
                        connect_timeout=5,
                        read_timeout=30,
                        s3={'addressing_style': 'virtual'}
                    )
                    
                    cls._client = boto3.client(
                        's3',
                        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                        region_name=settings.AWS_S3_REGION_NAME,
                        config=config
                    )
        
        return cls._client
       
    @staticmethod
    def upload_pdf(s3_key, pdf_bytes, expires_in_hours=24, download_filename=None):
        """
        Upload PDF to S3 using multipart upload when large.
        """
        client = S3Service.get_client()

        filename = download_filename or s3_key.split("/")[-1]
        # Keep it simple and safe for browsers/OSes
        filename = str(filename).replace('"', '').replace("\n", " ").replace("\r", " ").strip() or s3_key.split("/")[-1]
        content_disposition = f'attachment; filename="{filename}"'

        buffer = io.BytesIO(pdf_bytes)
        buffer.seek(0)

        transfer_config = TransferConfig(
            multipart_threshold=5 * 1024 * 1024,  # 5MB
            max_concurrency=4,
            multipart_chunksize=5 * 1024 * 1024,
            use_threads=True
        )

        client.upload_fileobj(
            Fileobj=buffer,
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
            Config=transfer_config,
            ExtraArgs={
                'ContentType': 'application/pdf',
                'ContentDisposition': content_disposition,
                'CacheControl': 'public, max-age=31536000'  # 1 year cache for permanent PDFs
            }
        )

        # Presigned URL
        return client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': s3_key,
                'ResponseContentType': 'application/pdf',
                'ResponseContentDisposition': content_disposition
            },
            ExpiresIn=expires_in_hours * 60 * 60  # Convert hours to seconds
        )

    @staticmethod
    def upload_excel(s3_key, excel_bytes, expires_in_hours=24, download_filename=None):
        """
        Upload Excel (.xlsx) file to S3 using multipart upload when large.
        """
        client = S3Service.get_client()

        filename = download_filename or s3_key.split("/")[-1]
        filename = str(filename).replace('"', '').replace("\n", " ").replace("\r", " ").strip() or s3_key.split("/")[-1]
        content_disposition = f'attachment; filename="{filename}"'
        content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

        buffer = io.BytesIO(excel_bytes)
        buffer.seek(0)

        transfer_config = TransferConfig(
            multipart_threshold=5 * 1024 * 1024,  # 5MB
            max_concurrency=4,
            multipart_chunksize=5 * 1024 * 1024,
            use_threads=True
        )

        client.upload_fileobj(
            Fileobj=buffer,
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
            Config=transfer_config,
            ExtraArgs={
                'ContentType': content_type,
                'ContentDisposition': content_disposition,
                'CacheControl': 'public, max-age=31536000'
            }
        )

        return client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': s3_key,
                'ResponseContentType': content_type,
                'ResponseContentDisposition': content_disposition
            },
            ExpiresIn=expires_in_hours * 60 * 60
        )

    @staticmethod
    def download_file(s3_key):
        """Download file from S3."""
        try:
            client = S3Service.get_client()
            response = client.get_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=s3_key
            )
            return response['Body'].read()
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.error(f"File not found: {s3_key}")
            else:
                logger.error(f"S3 download failed: {str(e)}")
            return None
    
    @staticmethod
    def delete_file(s3_key):
        """Delete file from S3 (silent failure)."""
        try:
            client = S3Service.get_client()
            client.delete_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=s3_key
            )
        except ClientError as e:
            logger.error(f"S3 delete failed (non-critical): {str(e)}")

    @staticmethod
    def file_exists(s3_key):
        """Check if file exists in S3 without downloading."""
        try:
            client = S3Service.get_client()
            client.head_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=s3_key
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            logger.error(f"S3 existence check failed: {str(e)}")
            return False

    @staticmethod
    def get_presigned_url(s3_key, expires_in_hours=24, download_filename=None):  
        """Generate presigned URL for existing S3 object."""
        try:
            client = S3Service.get_client()

            filename = download_filename or s3_key.split("/")[-1]
            filename = str(filename).replace('"', '').replace("\n", " ").replace("\r", " ").strip() or s3_key.split("/")[-1]
            content_disposition = f'attachment; filename="{filename}"'

            return client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                    'Key': s3_key,
                    'ResponseContentType': 'application/pdf',
                    'ResponseContentDisposition': content_disposition
                },
                ExpiresIn=expires_in_hours * 60 * 60  # Convert hours to seconds
            )
        except ClientError as e:
            logger.error(f"Presigned URL generation failed: {str(e)}")
            return None

    @staticmethod
    def get_excel_presigned_url(s3_key, expires_in_hours=24, download_filename=None):
        """Generate presigned URL for an existing Excel (.xlsx) S3 object."""
        try:
            client = S3Service.get_client()

            filename = download_filename or s3_key.split("/")[-1]
            filename = str(filename).replace('"', '').replace("\n", " ").replace("\r", " ").strip() or s3_key.split("/")[-1]
            content_disposition = f'attachment; filename="{filename}"'

            return client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                    'Key': s3_key,
                    'ResponseContentType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'ResponseContentDisposition': content_disposition
                },
                ExpiresIn=expires_in_hours * 60 * 60
            )
        except ClientError as e:
            logger.error(f"Excel presigned URL generation failed: {str(e)}")
            return None