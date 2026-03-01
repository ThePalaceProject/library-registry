"""Fixtures for S3/MinIO integration testing."""

import json

import boto3
import pytest
from botocore.exceptions import ClientError

from palace.registry.config import Configuration


class S3Fixture:
    """Fixture for setting up S3/MinIO buckets for testing."""

    def __init__(self):
        """Initialize S3 client and create test bucket."""
        aws_config = Configuration.aws_config()
        self.bucket_name = aws_config.bucket_name
        self.endpoint_url = aws_config.endpoint_url

        # Create boto3 client
        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id="palace",
            aws_secret_access_key="palace123",
        )

        # Create bucket if it doesn't exist
        self._create_bucket_if_needed()

    def _create_bucket_if_needed(self):
        """Create the test bucket if it doesn't already exist."""
        bucket_exists = False
        try:
            # Check if bucket exists by trying to get its location
            self.client.head_bucket(Bucket=self.bucket_name)
            bucket_exists = True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                # Bucket doesn't exist, create it with public-read ACL
                self.client.create_bucket(Bucket=self.bucket_name, ACL="public-read")
            else:
                # Some other error occurred
                raise

        # Always set the public read policy, whether bucket was just created or already existed
        # This ensures tests can access objects without authentication
        self._set_public_read_policy()

    def _set_public_read_policy(self):
        """Set bucket policy to allow public read access."""
        # Create a policy that allows public read access to all objects
        # MinIO requires Principal to be in the format {"AWS": ["*"]}
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{self.bucket_name}/*"],
                }
            ],
        }

        # Apply the policy to the bucket
        self.client.put_bucket_policy(
            Bucket=self.bucket_name, Policy=json.dumps(policy)
        )

    def cleanup(self):
        """Clean up all objects in the bucket (but keep the bucket)."""
        try:
            # List all objects in the bucket
            response = self.client.list_objects_v2(Bucket=self.bucket_name)

            if "Contents" in response:
                # Delete all objects
                objects_to_delete = [
                    {"Key": obj["Key"]} for obj in response["Contents"]
                ]
                self.client.delete_objects(
                    Bucket=self.bucket_name, Delete={"Objects": objects_to_delete}
                )
        except ClientError:
            # If cleanup fails, just pass - the bucket will be reused
            pass


@pytest.fixture(scope="session", autouse=True)
def s3_fixture():
    """Session-scoped fixture to set up S3/MinIO for all tests."""
    fixture = S3Fixture()
    yield fixture
    # Optional: cleanup after all tests
    # fixture.cleanup()
