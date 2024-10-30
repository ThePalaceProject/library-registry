import base64
import io
import os
from unittest.mock import MagicMock, patch

import requests

from config import Configuration
from tests.fixtures.database import DatabaseTransactionFixture
from util.file_storage import FileObject, LibraryLogoStore, S3FileStorage


class TestS3FileStorage:
    def test_config(self):
        with patch.dict(
            os.environ,
            {
                Configuration.AWS_S3_BUCKET_NAME: "bucket",
                Configuration.AWS_S3_ENDPOINT_URL: "http://localhost",
            },
        ):
            storage = S3FileStorage()

        assert storage.client._endpoint.host == "http://localhost"

    def test_write_and_delete(self):
        """Test the writing to the storage.
        This will require an accessible MiniO, it does not mock the interface.
        """
        storage = S3FileStorage()
        data = io.BytesIO(b"abcdefghijk")
        fobj = storage.write("test-file-1", data)

        aws_config = Configuration.aws_config()

        # Assert the link created is as expected
        link = storage.get_link(fobj)
        assert link == f"{aws_config.endpoint_url}/{aws_config.bucket_name}/test-file-1"

        # Link should work for downloads, without auth
        response = requests.get(link)
        assert response.status_code == 200, "The container needs to be 'Public'"
        assert response.content == b"abcdefghijk"

        # Delete the content
        assert storage.delete("test-file-1") == True

        # The object is no longer available
        response = requests.get(link)
        assert response.status_code == 404


class TestFileObject:
    def test_file_object(self):
        fobj = FileObject("name", "container", "backend")
        assert fobj.backend == "backend"
        assert fobj.container == "container"
        assert fobj.key == "name"
        assert fobj.path() == "backend://container/name"

    def test_from_path(self):
        fobj = FileObject.from_path("local://some/path/to/object")
        assert fobj.backend == "local"
        assert fobj.container == "some"
        assert fobj.key == "path/to/object"


class TestLibraryLogoStore:
    def test_write(self, db: DatabaseTransactionFixture):
        """Requires Minio"""
        library = db.library(short_name="short")
        path1 = LibraryLogoStore.write(library, io.BytesIO(b"logodata..."))

        # Request this data
        response = requests.get(path1)
        assert response.content == b"logodata..."

        # Overwrites should work too
        path2 = LibraryLogoStore.write(library, io.BytesIO(b"differentdata..."))

        # The same file should have gotten updated
        assert path1 == path2

        # Request this data
        response = requests.get(path2)
        assert response.content == b"differentdata..."

    def test_logo_path(self, db: DatabaseTransactionFixture):
        library = db.library()
        # internal urn has the format urn:uuid:xxx, we only put
        # the xxx part into the URL, so we split that for testing here
        assert (
            LibraryLogoStore.logo_path(library, "jpeg")
            == f"logo/{library.internal_urn.split(':', 2)[2]}.jpeg"
        )

    @patch("util.file_storage.LibraryLogoStore.write")
    def test_write_from_b64(
        self, mock_write: MagicMock, db: DatabaseTransactionFixture
    ):
        library = db.library()
        encoded = base64.b64encode(b"someimagedata")
        data = f"data:image/png;base64,{encoded.decode()}"
        LibraryLogoStore.write_from_b64(library, data)

        args = mock_write.call_args_list[0]
        assert args[0][0] == library
        assert args[0][1].read() == b"someimagedata"
        assert args[1]["format"] == "image/png"

    @patch("util.file_storage.LibraryLogoStore.write")
    def test_write_from_b64_no_match(
        self, mock_write: MagicMock, db: DatabaseTransactionFixture
    ):
        library = db.library()
        encoded = base64.b64encode(b"someimagedata")
        data = encoded.decode()
        LibraryLogoStore.write_from_b64(library, data)

        args = mock_write.call_args_list[0]
        assert args[0][0] == library
        assert args[0][1].read() == encoded
        assert args[1]["format"] == "binary/octet-stream"
