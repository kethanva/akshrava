# Cloud image fallback

The running phone workflow remains still-image only. The backend always calls the local detector
first; it sends an image to the selected cloud provider **only** when local detection returns no
objects. The app neither returns nor stores cloud captions, tags, raw labels, or the image itself.
If the fallback service fails, the result contains only a coarse availability bit and the phone
speaks a one-time degraded-mode warning; it does not silently imply that fallback remains active.
Only a boxed, allow-listed object (`person`, bicycle, motorcycle, car, bus, truck, dog or cat) may
re-enter the existing conservative hazard scorer. Cloud output never creates crossing advice,
distance, or an approach claim.

## Select one provider

Set `CLOUD_FALLBACK_PROVIDER` to `aws`, `gcp`, `azure`, or `none` (the safe default). Rebuild the
API after changing it because the container installs only the SDK for the selected provider.

| Provider | Image service | Credentials supplied to the container/runtime |
|---|---|---|
| AWS | Rekognition `DetectLabels` | Workload/instance role with only `rekognition:DetectLabels`; set `AWS_REGION`. |
| GCP | Cloud Vision labels + object localization | Application Default Credentials from a service account/workload identity with Vision access. |
| Azure | Azure AI Vision Image Analysis | `AZURE_VISION_ENDPOINT` and `AZURE_VISION_KEY`, stored in the cloud secret manager—not Git or `.env` in production. |

Use one provider per deployment; do not fan a frame out to all three. Each provider has its own
price, residency, retention and consent implications. Confirm the selected region, data-processing
terms and explicit participant consent before enabling it.

## Video is intentionally not part of the phone session

AWS Rekognition Video and Google Video Intelligence are asynchronous services over S3/GCS media;
Azure Video Indexer is a separately authorized paid account with a storage URL/access-token flow.
They require a consented clip-upload service, provider storage bucket/container, lifecycle deletion,
job queue, operator authorization and a reviewed retention policy. None of those are present in the
phone protocol, so the API rejects the idea of streaming video from a donated phone. Build that
separate back-office workflow only after the privacy programme and retention owner approve it.

The provider call shapes in this code follow the official APIs: [AWS Rekognition DetectLabels](https://docs.aws.amazon.com/rekognition/latest/APIReference/API_DetectLabels.html), [Google Cloud Vision](https://cloud.google.com/vision/docs/detect-labels-image-client-libraries), and [Azure Image Analysis](https://learn.microsoft.com/en-us/python/api/overview/azure/ai-vision-imageanalysis-readme?view=azure-python). For later consented video work, see [Google Video Intelligence label detection](https://cloud.google.com/video-intelligence/docs/feature-label-detection), [AWS Rekognition labels](https://docs.aws.amazon.com/rekognition/latest/dg/labels.html), and [Azure Video Indexer API requirements](https://learn.microsoft.com/en-us/azure/azure-video-indexer/video-indexer-use-apis).
