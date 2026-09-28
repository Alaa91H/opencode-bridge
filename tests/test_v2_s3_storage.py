from __future__ import annotations

import io
import unittest

from bridge.infrastructure.storage.s3 import S3Storage


class NotFound(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "404"}}


class FakeS3:
    def __init__(self):
        self.objects = {}

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise NotFound()

    def upload_fileobj(self, source, bucket, key):
        self.objects[(bucket, key)] = source.read()

    def download_fileobj(self, bucket, key, target):
        target.write(self.objects[(bucket, key)])

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


class S3StorageContractTests(unittest.TestCase):
    def test_round_trip_dedup_and_delete(self):
        client = FakeS3()
        storage = S3Storage(client, "bucket", prefix="bridge")
        first = storage.put_stream(io.BytesIO(b"payload"))
        second = storage.put_stream(io.BytesIO(b"payload"))
        self.assertEqual(first.key, second.key)
        self.assertEqual(len(client.objects), 1)
        with storage.open(first.key) as handle:
            self.assertEqual(handle.read(), b"payload")
        self.assertIsNone(storage.local_path(first.key))
        storage.delete(first.key)
        self.assertFalse(storage.exists(first.key))

    def test_hash_mismatch_is_rejected_before_upload(self):
        client = FakeS3()
        storage = S3Storage(client, "bucket")
        with self.assertRaises(ValueError):
            storage.put_stream(io.BytesIO(b"payload"), sha256="0" * 64)
        self.assertEqual(client.objects, {})
