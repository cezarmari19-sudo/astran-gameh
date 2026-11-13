"""python -m unittest astran_sandbox.test_media_validation"""
import unittest

from astran_sandbox.media_validation import clean_image_ref as c

G = ("googleusercontent.com",)
PNG = "data:image/png;base64,iVBORw0KGgo="


class MediaValidationTests(unittest.TestCase):
    def test_accepts(self):
        self.assertEqual(c(PNG, https_hosts=G), PNG)
        self.assertEqual(c("https://lh3.googleusercontent.com/a/x=s96-c", https_hosts=G),
                         "https://lh3.googleusercontent.com/a/x=s96-c")
        self.assertEqual(c("https://cdn.example.com/a.jpg", any_https=True), "https://cdn.example.com/a.jpg")
        self.assertIsNone(c(None)); self.assertIsNone(c(""))

    def test_rejects(self):
        for bad in ("http://lh3.googleusercontent.com/a", "javascript:alert(1)", "file:///data/x.png",
                    "content://media/1", "https://evil.com/p.gif?uid=1", "https://googleusercontent.com.evil.com/a",
                    "https://user:pw@lh3.googleusercontent.com/a", "data:text/html;base64,PGh0bWw+",
                    "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64," + "A" * 500_001, 123):
            with self.assertRaises(ValueError, msg=str(bad)[:40]):
                c(bad, https_hosts=G)

    def test_https_any_still_blocks_other_schemes(self):
        for bad in ("http://x.com/a.png", "ftp://x.com/a.png", "file:///a.png", "//x.com/a.png"):
            with self.assertRaises(ValueError):
                c(bad, any_https=True)


if __name__ == "__main__":
    unittest.main()