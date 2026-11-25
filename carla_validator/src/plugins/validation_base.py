from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .validation_helper import ValidationOutput
from .validation_helper import ValidationContext


class ValidationBase:
    """
    Base class for all validation plugins.
    """

    def __init__(self, validation_type: str, config: Mapping[str, Any] | None = None):
        self.validation_type = validation_type
        self.cfg = dict(config or {})

    def validate(self, context: ValidationContext) -> ValidationOutput:
        """
        Validate the given data.

        Returns:
            ValidationOutput: The result of the validation.
        """
        raise NotImplementedError("Subclasses must implement this method.")