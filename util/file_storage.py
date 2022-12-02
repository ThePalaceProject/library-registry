from __future__ import annotations

import base64
import io
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Optional

import boto3
import botocore
from botocore.config import Config

from config import Configuration
from model import Library

if TYPE_CHECKING:
    pass


@dataclass
class FileObject:
    """A representation of a file stored anywhere"""

    # This is generally the folders + name of the file
    key: str
    # The toplevel directory, in s3 this is the Bucket
    container: str
    # Describes the storage medium. Can be anything.
    # Currently only 's3' is implemented
    backend: str

    def path(self) -> str:
        """Returns the string representation of the file object"""
        return f"{self.backend}://{self.container}/{self.key}"

    @classmethod
    def from_path(cls, path: str) -> "FileObject":
        """Parse a file object path into a FileObject"""
        match = re.match(r"^([a-z0-9]+)://(.*?)/(.*)$", path)
        return cls(backend=match.group(1), container=match.group(2), key=match.group(3))

    def __repr__(self) -> str:
        return self.path()


class FileStorage(ABC):
    """The storage interface"""

    default_storage = None

    @classmethod
    def storage(cls):
        """Return the storage object of the medium in use"""
        if not cls.default_storage:
            cls.default_storage = S3FileStorage()
        return cls.default_storage

    @abstractmethod
    def write(self, name: str, io: IO) -> Optional[FileObject]:
        """Write a file to the storage
        :param name: Name of the file, with the folder path
        :param io: The data stream to be written
        """
        ...

    @abstractmethod
    def get_link(self, obj: FileObject) -> str:
        """Get a downloadable link for a file object"""
        ...

    @abstractmethod
    def delete(self, name: str) -> bool:
        """Delete a object from the storage
        :param name: The file name, with the path
        """
        ...


class S3FileStorage(FileStorage):
    """S3 specific implementation"""

    BACKEND = "s3"
    ACL = "public-read"
    S3_ENDPOINT_URL = "https://{bucket}.s3.{region_code}.amazonaws.com"
    BUCKET_REGION_CACHE = {}

    def __init__(self) -> None:
        config = Configuration.aws_config()
        boto_config = Config(signature_version=botocore.UNSIGNED)
        extras = dict(endpoint_url=config.endpoint_url)

        # We need 2 clients since the UNSIGNED config stops the client from signing post requests too
        # We only need unsigned urls for "get object" requests
        session = boto3.Session()
        self.client = session.client("s3", **extras)
        self.read_client = session.client("s3", config=boto_config, **extras)

        self._bucket_name = config.bucket_name

    def write(
        self, name: str, io: IO, content_type="binary/octet-stream"
    ) -> Optional[FileObject]:
        response = self.client.put_object(
            Key=name,
            Bucket=self._bucket_name,
            Body=io.read(),
            ACL=self.ACL,
            ContentType=content_type,
        )
        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
            return FileObject(
                key=name, container=self._bucket_name, backend=self.BACKEND
            )
        return None

    def delete(self, name: str) -> bool:
        response = self.client.delete_object(Key=name, Bucket=self._bucket_name)
        return response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 204

    def get_link(self, obj: FileObject) -> str:
        """All objects are public-read, return the path to the object"""
        return self.read_client.generate_presigned_url(
            "get_object", Params=dict(Bucket=obj.container, Key=obj.key), ExpiresIn=0
        )


class LibraryLogoStore:
    """The library logo store mechanism"""

    @classmethod
    def logo_path(self, library: Library, ext: str) -> str:
        """Get the folder path for a library logo
        :param library: The library
        :param ext: The extension of the logo, eg. png
        """
        # Remove the urn:uuid: prefix off of the internal
        # urn before turning it into a s3 url, since : needs
        # to be url encoded. Otherwise, the logo urls end up
        # looking kind of ugly with %2A in them.
        prefix = "urn:uuid:"
        uuid = library.internal_urn
        if uuid.startswith(prefix):
            uuid = uuid[len(prefix) :]
        return f"logo/{uuid}.{ext}"

    @classmethod
    def write(cls, library: Library, io: IO, format="image/png") -> str | None:
        """Write the logo to the storage
        :param library: The library
        :param io: The data stream
        :param format: The format of the image
        """
        ext = format if "/" not in format else format.split("/", 1)[1]
        obj = FileStorage.storage().write(
            cls.logo_path(library, ext), io, content_type=format
        )
        if obj:
            return FileStorage.storage().get_link(obj)

    @classmethod
    def write_from_b64(cls, library: Library, data: str) -> str | None:
        """Write a data blob, possibly b64 encoded, to the storage"""
        format = "binary/octet-stream"  # Unknown binary format by default

        # Is this is a b64 encoded data blob?
        match = re.match(r"^data:image/(png|jpg|jpeg);base64,", data[:30])
        if match:
            format = match.group(1)
            format = f"image/{format}"
            data = base64.b64decode(data.split(",", 1)[1])
        elif type(data) is str:
            # If no match, just encode the data
            data = bytes(data, "utf-8")

        return cls.write(library, io.BytesIO(data), format=format)
