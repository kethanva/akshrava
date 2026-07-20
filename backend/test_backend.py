import asyncio
from akshrava_backend.detector import UltralyticsDetector
from PIL import Image
import io
import urllib.request
import ssl

def main():
    detector = UltralyticsDetector("yolo11s.pt")
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    url = "https://raw.githubusercontent.com/ultralytics/yolov5/master/data/images/bus.jpg"
    req_obj = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    req = urllib.request.urlopen(req_obj, context=ctx)
    jpeg = req.read()
    
    img = Image.open(io.BytesIO(jpeg))
    out = io.BytesIO()
    img.save(out, format="JPEG")
    jpeg = out.getvalue()
    
    res = detector.detect(jpeg)
    print("Detections:")
    for d in res:
        print(d)

if __name__ == "__main__":
    main()
