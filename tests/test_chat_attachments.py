import unittest
from unittest.mock import patch, MagicMock
from fastapi import Request
from server.ui_app import get_chat_attachments_endpoint

class TestChatAttachmentsDeduplication(unittest.TestCase):
    @patch("server.ui_app.ords_get")
    def test_deduplicate_attachments_by_gdrive_id_and_filename(self, mock_ords_get):
        # Mock ORDS returning duplicate entries for the same uploaded file
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "items": [
                {
                    "file_name": "image.png",
                    "file_type": "image",
                    "gdrive_file_id": "1Vq2W3xyzAbcDef12345678",
                    "gdrive_web_link": "https://drive.google.com/file/d/1Vq2W3xyzAbcDef12345678/view",
                    "file_size": 102400
                },
                {
                    "file_name": "image.png",
                    "file_type": "image",
                    "gdrive_file_id": "1Vq2W3xyzAbcDef12345678",
                    "gdrive_web_link": "https://drive.google.com/file/d/1Vq2W3xyzAbcDef12345678/view",
                    "file_size": 102400
                }
            ]
        }
        mock_ords_get.return_value = mock_resp

        mock_req = MagicMock(spec=Request)
        res = get_chat_attachments_endpoint("test-session-123", mock_req)

        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 1)
        self.assertEqual(len(res["attachments"]), 1)
        self.assertEqual(res["attachments"][0]["file_name"], "image.png")
        self.assertEqual(res["attachments"][0]["gdrive_file_id"], "1Vq2W3xyzAbcDef12345678")

if __name__ == "__main__":
    unittest.main()
