"""Job-level exceptions raised by the image text extractor."""


class ImageTextExtractorError(RuntimeError):
    """Base class for failures that affect an entire OCR job."""


class WorkerInitializationError(ImageTextExtractorError):
    """The worker could not load or initialize RapidOCR."""


class WorkerCrashedError(ImageTextExtractorError):
    """The worker exited without sending a terminal protocol message."""


class TextExtractionTimeoutError(ImageTextExtractorError):
    """The OCR job exceeded its configured timeout."""


class TextExtractionCancelledError(ImageTextExtractorError):
    """The OCR job was cancelled before normal completion."""
