import importlib.util
from pathlib import Path
import unittest
from PIL import Image
spec=importlib.util.spec_from_file_location('banner_assets',Path(__file__).resolve().parents[1]/'src/bannergen/banner_assets.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
class BannerTests(unittest.TestCase):
    def test_background_survives_missing_logo(self):
        image=a.compose_background(Image.new('RGB',(2000,500),'blue'),None,1)
        self.assertEqual(image.getpixel((100,100)),(0,0,255))
    def test_logo_is_never_used_as_fallback_background(self):
        image=a.compose_background(None,Image.new('RGBA',(100,200),'red'),1)
        self.assertEqual(image.getpixel((100,100)),(255,255,255))
        self.assertEqual(image.getpixel((1575,100)),(255,0,0))
        self.assertEqual(image.getpixel((1480,100)),(255,255,255))
    def test_zero_opacity_hides_background_not_logo(self):
        image=a.compose_background(Image.new('RGB',(500,100),'blue'),Image.new('RGB',(200,200),'red'),0)
        self.assertEqual(image.getpixel((100,100)),(255,255,255))
        self.assertEqual(image.getpixel((1500,100)),(255,0,0))
    def test_cache_changes_with_background_and_logo(self):
        self.assertNotEqual(a.cache_key({'background':'a','logo':'b'}),a.cache_key({'background':'c','logo':'b'}))
        self.assertNotEqual(a.cache_key({'language':'en'}),a.cache_key({'language':'zh'}))
if __name__=='__main__':unittest.main()
