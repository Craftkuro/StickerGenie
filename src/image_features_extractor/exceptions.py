"""Job-level exceptions raised by the image feature extractor."""


class ImageFeaturesExtractorError(RuntimeError):
    """Base class for failures that affect an entire extraction job."""


class WorkerInitializationError(ImageFeaturesExtractorError):
    """The worker could not load or initialize the ONNX model."""


class WorkerCrashedError(ImageFeaturesExtractorError):
    """The worker exited without sending a terminal protocol message."""


class ExtractionTimeoutError(ImageFeaturesExtractorError):
    """The extraction job exceeded its configured timeout."""


class ExtractionCancelledError(ImageFeaturesExtractorError):
    """The extraction job was cancelled before normal completion."""
